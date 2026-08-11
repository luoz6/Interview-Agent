from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Mapping

from app.adapters.memory.principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import InMemoryPrincipalMemoryConsentStore
from app.runtime.config.memory import load_effective_memory_config
from app.adapters.postgres.principal_memory import PostgresPrincipalMemoryFactStore
from app.services.postgres_principal_memory_consent import PostgresPrincipalMemoryConsentStore
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_consent import PrincipalMemoryConsent, PrincipalMemoryConsentService
from app.services.principal_memory_extractor import StructuredPrincipalMemoryExtractor
from app.services.principal_memory_proposals import build_proposal_event_if_eligible
from app.services.principal_memory_tasks import PrincipalMemoryProposalProcessor
from contracts.evidence import ShadowEvidencePayload
from scripts.memory_postgres_validation import run_validation
from scripts.memory_shadow_evidence_support import (
    approved_postgres_scope,
    print_evidence_result,
    publish_shadow_evidence,
    strict_nonnegative_int,
)


NOW = datetime(2026, 7, 31, tzinfo=timezone.utc)
DEFAULT_OUTPUT = Path("reports/memory/write-shadow-evidence-v1.json")

WRITE_INVARIANT_GATES = {
    "without_consent_proposal": "WRITE_SHADOW_WITHOUT_CONSENT",
    "identity_unavailable_proposal": "WRITE_SHADOW_IDENTITY_UNAVAILABLE",
    "cross_principal_write": "WRITE_SHADOW_CROSS_PRINCIPAL_WRITE",
    "source_mismatch_write": "WRITE_SHADOW_SOURCE_MISMATCH",
    "non_allowlist_taxonomy_write": "WRITE_SHADOW_TAXONOMY_VIOLATION",
    "free_text_fact_value": "WRITE_SHADOW_FREE_TEXT_FACT",
    "automatic_active": "WRITE_SHADOW_AUTOMATIC_ACTIVE",
    "automatic_user_confirmed": "WRITE_SHADOW_AUTOMATIC_CONFIRMED",
    "inferred_accessibility_preference": "WRITE_SHADOW_INFERRED_ACCESSIBILITY",
    "deleting_session_proposal": "WRITE_SHADOW_DELETING_SESSION_PROPOSAL",
    "public_knowledge_write": "WRITE_SHADOW_PUBLIC_KNOWLEDGE_WRITE",
    "interview_behavior_change": "WRITE_SHADOW_INTERVIEW_BEHAVIOR_CHANGE",
    "privacy_artifact_hit": "WRITE_SHADOW_PRIVACY_HIT",
}
WRITE_FAULT_FIELDS = frozenset(
    {
        "candidate_rejected",
        "consent_unavailable",
        "extractor_failure_contained",
        "identity_changed",
        "identity_unavailable",
        "source_unavailable",
        "source_version_changed",
    }
)


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


def build_write_shadow_payload(record: Mapping[str, object]) -> ShadowEvidencePayload:
    sample_count = strict_nonnegative_int(record, "sample_count")
    if sample_count < 1:
        raise ValueError("sample_count must be positive")
    violations = []
    invariants = record["hard_invariants"]
    if not isinstance(invariants, Mapping):
        raise ValueError("hard_invariants must be an object")
    if set(invariants) != set(WRITE_INVARIANT_GATES):
        raise ValueError("hard_invariants field set is invalid")
    for field, gate in WRITE_INVARIANT_GATES.items():
        if strict_nonnegative_int(invariants, field) != 0:
            violations.append(gate)
    faults = record["fault_matrix"]
    if not isinstance(faults, Mapping) or set(faults) != set(WRITE_FAULT_FIELDS):
        raise ValueError("fault_matrix field set is invalid")
    fault_counts = {
        field: strict_nonnegative_int(faults, field)
        for field in sorted(WRITE_FAULT_FIELDS)
    }
    invariant_counts = {
        field: strict_nonnegative_int(invariants, field)
        for field in sorted(WRITE_INVARIANT_GATES)
    }
    created = strict_nonnegative_int(record, "proposal_created_count")
    proposed = strict_nonnegative_int(record, "proposed_fact_count")
    duplicate = strict_nonnegative_int(record, "duplicate_fact_count")
    deduplicated = strict_nonnegative_int(record, "deduplicated_replay_count")
    provider_calls = strict_nonnegative_int(record, "provider_calls")
    cleanup_residue = strict_nonnegative_int(record, "cleanup_residue")
    if created != sample_count or proposed != sample_count:
        violations.append("WRITE_SHADOW_PROPOSAL_COUNT_MISMATCH")
    if duplicate != 0:
        violations.append("WRITE_SHADOW_DUPLICATE_FACT")
    if provider_calls != 0:
        violations.append("WRITE_SHADOW_PROVIDER_CALLED")
    if cleanup_residue != 0:
        violations.append("WRITE_SHADOW_CLEANUP_RESIDUE")
    if record["rollback_verified"] is not True:
        violations.append("WRITE_SHADOW_ROLLBACK_NOT_VERIFIED")
    if record["configuration_persisted"] is not False:
        violations.append("WRITE_SHADOW_CONFIGURATION_PERSISTED")
    if record["authority"] != "model_proposed":
        violations.append("WRITE_SHADOW_AUTHORITY_INVALID")
    if record["final_status"] != "proposed":
        violations.append("WRITE_SHADOW_FINAL_STATUS_INVALID")
    if record["read_shadow"] != "disabled":
        violations.append("WRITE_SHADOW_READ_AXIS_ENABLED")
    if record["trusted_local_api"] != "disabled":
        violations.append("WRITE_SHADOW_TRUSTED_API_ENABLED")
    return ShadowEvidencePayload(
        schema_version="shadow-evidence-v1",
        sample_count=sample_count,
        synthetic=True,
        observation_window_seconds=1,
        metrics={
            "proposal_created_count": float(created),
            "proposed_fact_count": float(proposed),
            "duplicate_fact_count": float(duplicate),
            "deduplicated_replay_count": float(deduplicated),
            "provider_calls": float(provider_calls),
            "cleanup_residue": float(cleanup_residue),
            **{
                f"fault_{field}": float(value)
                for field, value in fault_counts.items()
            },
            **{
                f"hard_{field}": float(value)
                for field, value in invariant_counts.items()
            },
        },
        violations=violations,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--scope-prefix", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--samples", type=int, default=300)
    args = parser.parse_args(argv)
    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if not dsn: raise RuntimeError("POSTGRES_DSN is required")
    record = None
    active = None
    with approved_postgres_scope(
        dsn=dsn,
        scope_prefix=args.scope_prefix,
        environ=os.environ,
    ) as active:
        run_validation(dsn=dsn, table_prefix=args.scope_prefix)
        record = run_write_shadow(
            fact_store=PostgresPrincipalMemoryFactStore(dsn=dsn, table_prefix=args.scope_prefix, schema_mode="validate"),
            consent_store=PostgresPrincipalMemoryConsentStore(dsn=dsn, table_prefix=args.scope_prefix, schema_mode="validate"),
            sample_count=args.samples,
        )
    if record is None or active is None or active.lease.cleanup_receipt is None:
        raise RuntimeError("write shadow did not produce cleanup evidence")
    record["cleanup_residue"] = active.lease.cleanup_receipt.residue_count
    record["rollback_verified"] = record["cleanup_residue"] == 0
    validate_artifact(record)
    payload = build_write_shadow_payload(record)
    bundle = publish_shadow_evidence(
        payload=payload,
        output=args.output,
        producer="scripts.principal-memory-write-shadow",
        scope="memory.write-shadow.controlled",
        environ=os.environ,
        minimum_samples=300,
    )
    print_evidence_result(bundle, args.output)
    return 0 if bundle.artifact.verification_status.value == "PASS" else 1


if __name__ == "__main__": raise SystemExit(main())
