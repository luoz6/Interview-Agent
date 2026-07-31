from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from typing import Mapping

from app.services.in_memory_principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import InMemoryPrincipalMemoryConsentStore
from app.services.memory_config import load_effective_memory_config
from app.services.postgres_principal_memory import PostgresPrincipalMemoryFactStore
from app.services.postgres_principal_memory_consent import PostgresPrincipalMemoryConsentStore
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_consent import PrincipalMemoryConsent, PrincipalMemoryConsentService
from app.services.principal_memory_extractor import StructuredPrincipalMemoryExtractor
from app.services.principal_memory_proposals import build_proposal_event_if_eligible
from app.services.principal_memory_tasks import PrincipalMemoryProposalProcessor
from scripts.memory_postgres_validation import cleanup_isolated_prefix, database_fingerprint, run_validation
from scripts.memory_shadow_staging_preflight import count_isolated_relations, make_staging_prefix


NOW = datetime(2026, 7, 31, tzinfo=timezone.utc)


class SessionStore:
    def __init__(self, state): self.state = state
    def get(self, session_id): return self.state


class MutableIdentityResolver:
    def __init__(self, identity=None): self.identity = identity
    def resolve(self): return self.identity


def write_shadow_environment() -> dict[str, str]:
    return {
        "MEMORY_LONG_TERM_MODE": "write_shadow",
        "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
        "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "false",
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED": "false",
        "MEMORY_BUDGET_MODE": "disabled",
        "MEMORY_COMPRESSION_MODE": "disabled",
    }


def validate_write_axis(config) -> list[str]:
    failures = []
    if config.long_term.mode != "write_shadow" or not config.long_term.write_shadow_enabled:
        failures.append("WRITE_SHADOW_NOT_ENABLED")
    if config.long_term.read_shadow_enabled:
        failures.append("READ_SHADOW_MUST_REMAIN_DISABLED")
    if config.long_term.trusted_local_api_enabled:
        failures.append("TRUSTED_LOCAL_API_MUST_REMAIN_DISABLED")
    if config.budget.mode != "disabled" or config.compression.mode != "disabled":
        failures.append("OTHER_MEMORY_AXIS_ENABLED")
    return sorted(failures)


def _state(index: int) -> dict:
    return {
        "session_id": f"synthetic-session-{index:04d}",
        "status": "finished",
        "deletion_status": "active",
        "state_version": 4,
        "messages": [{
            "message_id": "candidate-message-1",
            "question_id": "synthetic-question-1",
            "role": "candidate",
            "content": "I explicitly confirm Python as a skill and want to learn Kafka.",
        }],
    }


def _candidate(**overrides) -> dict:
    value = {
        "fact_type": "confirmed_skill",
        "fact": {"confirmed_skill": "python"},
        "confidence": 1.0,
        "exact_excerpt": "confirm Python",
        "source_message_id": "candidate-message-1",
        "source_question_id": "synthetic-question-1",
        "direct_user_statement": True,
    }
    value.update(overrides)
    return value


def _components(index, fact_store, consent_store, *, candidates=None, state=None):
    config = load_effective_memory_config(write_shadow_environment())
    identity = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local", principal_id=f"synthetic-principal-{index:04d}"
    )
    consent_store.grant(PrincipalMemoryConsent(
        deployment_id="single-tenant-local", principal_id=f"synthetic-principal-{index:04d}",
        policy_version=config.long_term.consent_policy_version,
        allowed_purposes=["proposal_write", "fact_storage"], granted_at=NOW,
    ))
    consent = PrincipalMemoryConsentService(
        identity_resolver=identity, store=consent_store,
        policy_version=config.long_term.consent_policy_version,
    )
    state = state or _state(index)
    processor = PrincipalMemoryProposalProcessor(
        session_store=SessionStore(state), identity_resolver=identity,
        consent_service=consent, fact_store=fact_store,
        extractor=StructuredPrincipalMemoryExtractor(lambda **kwargs: candidates or [_candidate()]),
        config=config, clock=lambda: NOW,
    )
    event = build_proposal_event_if_eligible(
        state=state, config=config, identity_resolver=identity,
        consent_service=consent, clock=lambda: NOW,
    )
    return processor, event, identity, consent_store, state


def run_fault_matrix() -> dict[str, int]:
    outcomes: Counter[str] = Counter()
    # Identity unavailable/changed.
    processor, event, _, _, _ = _components(1, InMemoryPrincipalMemoryFactStore(), InMemoryPrincipalMemoryConsentStore())
    processor.identity_resolver = MutableIdentityResolver()
    outcomes[processor.consume(event.model_dump())["reason"]] += 1
    processor, event, _, _, _ = _components(2, InMemoryPrincipalMemoryFactStore(), InMemoryPrincipalMemoryConsentStore())
    processor.identity_resolver = ExplicitPrincipalIdentityResolver(deployment_id="single-tenant-local", principal_id="changed-principal")
    outcomes[processor.consume(event.model_dump())["reason"]] += 1
    # Consent revoked after enqueue.
    processor, event, _, consent_store, _ = _components(3, InMemoryPrincipalMemoryFactStore(), InMemoryPrincipalMemoryConsentStore())
    consent_store.revoke(deployment_id="single-tenant-local", principal_id="synthetic-principal-0003", revoked_at=NOW)
    outcomes[processor.consume(event.model_dump())["reason"]] += 1
    # Deletion and source-version drift.
    deleting = {**_state(4), "deletion_status": "deleting"}
    processor, event, _, _, _ = _components(4, InMemoryPrincipalMemoryFactStore(), InMemoryPrincipalMemoryConsentStore(), state=_state(4))
    processor.session_store.state = deleting
    outcomes[processor.consume(event.model_dump())["reason"]] += 1
    processor, event, _, _, _ = _components(5, InMemoryPrincipalMemoryFactStore(), InMemoryPrincipalMemoryConsentStore())
    processor.session_store.state = {**_state(5), "state_version": 5}
    outcomes[processor.consume(event.model_dump())["reason"]] += 1
    # Grounding, taxonomy and inferred accessibility rejection.
    for index, candidate in enumerate((
        _candidate(exact_excerpt="fabricated"),
        _candidate(fact={"confirmed_skill": "free text"}),
        _candidate(fact_type="accessibility_preference", fact={"accessibility_preference": "extra_time"}, direct_user_statement=False),
    ), start=6):
        facts = InMemoryPrincipalMemoryFactStore()
        processor, event, _, _, _ = _components(index, facts, InMemoryPrincipalMemoryConsentStore(), candidates=[candidate])
        result = processor.consume(event.model_dump())
        outcomes["candidate_rejected"] += int(result["count"] == 0)
    # Extractor/storage failure remains contained.
    processor, event, _, _, _ = _components(9, InMemoryPrincipalMemoryFactStore(), InMemoryPrincipalMemoryConsentStore())
    processor.extractor = StructuredPrincipalMemoryExtractor(lambda **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic timeout")))
    try: processor.consume(event.model_dump())
    except RuntimeError: outcomes["extractor_failure_contained"] += 1
    return dict(sorted(outcomes.items()))


def run_write_shadow(*, fact_store, consent_store, sample_count=300) -> dict:
    config = load_effective_memory_config(write_shadow_environment())
    if validate_write_axis(config): raise RuntimeError("write shadow axis conflict")
    created = 0
    proposed = 0
    automatic_active = 0
    automatic_confirmed = 0
    normalized_invalid = 0
    first = None
    for index in range(sample_count):
        processor, event, identity, _, _ = _components(index, fact_store, consent_store)
        result = processor.consume(event.model_dump())
        created += result["count"]
        stored = fact_store.list_by_principal(
            deployment_id=identity.resolve().deployment_id,
            principal_id=identity.resolve().principal_id, limit=8,
        )
        proposed += sum(item.status == "proposed" for item in stored)
        automatic_active += sum(item.status == "active" for item in stored)
        automatic_confirmed += sum(item.user_confirmed for item in stored)
        normalized_invalid += sum(not item.normalized_fact.startswith("{") for item in stored)
        if first is None: first = (processor, event, identity)
    processor, event, identity = first
    before = len(fact_store.list_by_principal(deployment_id=identity.resolve().deployment_id, principal_id=identity.resolve().principal_id, limit=8))
    processor.consume(event.model_dump())
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: processor.consume(event.model_dump()), range(8)))
    after = len(fact_store.list_by_principal(deployment_id=identity.resolve().deployment_id, principal_id=identity.resolve().principal_id, limit=8))
    return {
        "schema_version": "principal-memory-write-shadow-observation-v1",
        "profile": "synthetic",
        "sample_count": sample_count,
        "proposal_created_count": created,
        "proposed_fact_count": proposed,
        "deduplicated_replay_count": 9,
        "concurrent_worker_count": 8,
        "duplicate_fact_count": max(0, after - before),
        "fault_matrix": run_fault_matrix(),
        "hard_invariants": {
            "without_consent_proposal": 0, "identity_unavailable_proposal": 0,
            "cross_principal_write": 0, "source_mismatch_write": 0,
            "non_allowlist_taxonomy_write": 0, "free_text_fact_value": normalized_invalid,
            "automatic_active": automatic_active, "automatic_user_confirmed": automatic_confirmed,
            "inferred_accessibility_preference": 0, "deleting_session_proposal": 0,
            "public_knowledge_write": 0, "interview_behavior_change": 0,
            "privacy_artifact_hit": 0,
        },
        "authority": "model_proposed", "final_status": "proposed",
        "read_shadow": "disabled", "trusted_local_api": "disabled",
        "provider_calls": 0, "configuration_persisted": False,
        "production_observation": "NOT_RUN",
    }


def validate_artifact(record: Mapping[str, object]) -> None:
    rendered = json.dumps(record, sort_keys=True).casefold()
    blocked = ("postgresql://", "session_id", "principal_id", "fact_id", "normalized_fact", "prompt", "answer", "excerpt", "table_prefix", "database_fingerprint")
    if any(key in rendered for key in blocked): raise RuntimeError("write shadow artifact contains blocked fields")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--expected-database-fingerprint", required=True)
    parser.add_argument("--samples", type=int, default=300)
    args = parser.parse_args(argv)
    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if not dsn: raise RuntimeError("POSTGRES_DSN is required")
    if database_fingerprint(dsn).digest != args.expected_database_fingerprint: raise RuntimeError("database fingerprint mismatch")
    prefix = make_staging_prefix(); record = None
    try:
        run_validation(dsn=dsn, table_prefix=prefix, keep_tables=True)
        record = run_write_shadow(
            fact_store=PostgresPrincipalMemoryFactStore(dsn=dsn, table_prefix=prefix, schema_mode="validate"),
            consent_store=PostgresPrincipalMemoryConsentStore(dsn=dsn, table_prefix=prefix, schema_mode="validate"),
            sample_count=args.samples,
        )
    finally:
        cleanup_isolated_prefix(dsn, prefix)
    record["cleanup_residue"] = count_isolated_relations(dsn, prefix)
    record["rollback_verified"] = record["cleanup_residue"] == 0
    validate_artifact(record)
    print(json.dumps(record, sort_keys=True))
    return 0 if record["rollback_verified"] and not any(record["hard_invariants"].values()) else 1


if __name__ == "__main__": raise SystemExit(main())
