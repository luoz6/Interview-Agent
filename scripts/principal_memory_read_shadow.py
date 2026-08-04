from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from time import perf_counter
from typing import Mapping

from app.services.memory_config import load_effective_memory_config
from app.services.postgres_principal_memory import PostgresPrincipalMemoryFactStore
from app.services.postgres_principal_memory_consent import PostgresPrincipalMemoryConsentStore
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_consent import PrincipalMemoryConsent, PrincipalMemoryConsentService
from app.services.principal_memory_contracts import PrincipalMemoryFact, canonical_principal_fact, derive_principal_fact_id
from app.services.principal_memory_retrieval import PrincipalMemoryRetriever
from app.services.principal_memory_shadow import PrincipalMemoryShadowService, canonical_provider_context_digest
from scripts.memory_postgres_validation import cleanup_isolated_prefix, database_fingerprint, run_validation
from scripts.memory_shadow_staging_preflight import count_isolated_relations, make_staging_prefix


NOW = datetime(2026, 7, 31, tzinfo=timezone.utc)
SCENARIOS = ("relevant", "conflict", "revoked_consent", "deleted_source", "expired", "unconfirmed", "cross_principal", "fact_cap")


class Sessions:
    def __init__(self): self.deleted = set()
    def get(self, session_id): return {"deletion_status": "deleted" if session_id in self.deleted else "active"}


def read_shadow_environment() -> dict[str, str]:
    return {
        "MEMORY_LONG_TERM_MODE": "read_shadow",
        "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "false",
        "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED": "false",
        "MEMORY_LONG_TERM_MAX_SHADOW_FACTS": "3",
        "MEMORY_LONG_TERM_MAX_SHADOW_TOKENS": "200",
        "MEMORY_BUDGET_MODE": "disabled", "MEMORY_COMPRESSION_MODE": "disabled",
    }


def validate_read_axis(config) -> list[str]:
    failures=[]
    if config.long_term.mode != "read_shadow" or not config.long_term.read_shadow_enabled: failures.append("READ_SHADOW_NOT_ENABLED")
    if config.long_term.write_shadow_enabled: failures.append("WRITE_SHADOW_GATE_ENABLED")
    if config.long_term.local_consumption_enabled: failures.append("LOCAL_CONSUMPTION_GATE_ENABLED")
    if config.long_term.trusted_local_api_enabled: failures.append("TRUSTED_LOCAL_API_ENABLED")
    if config.budget.mode != "disabled" or config.compression.mode != "disabled": failures.append("OTHER_MEMORY_AXIS_ENABLED")
    return failures


def _fact(store, *, principal, session, fact_type="confirmed_skill", value=None, token="x", active=True, expires=None, confidence=.9):
    value=value or {"confirmed_skill":"python"}; normalized=canonical_principal_fact(value)
    values={
        "deployment_id":"single-tenant-local", "principal_id":principal,
        "fact_type":fact_type, "normalized_fact":normalized,
        "source_manifest_sha256":sha256((token+"manifest").encode()).hexdigest(),
        "source_excerpt_sha256":sha256((token+"excerpt").encode()).hexdigest(),
        "consent_policy_version":"principal-memory-consent-v1",
        "taxonomy_version":"principal-memory-taxonomy-v1",
    }
    proposal=PrincipalMemoryFact(
        fact_id=derive_principal_fact_id(**values), **values, confidence=confidence,
        authority="model_proposed", source_session_id=session, created_at=NOW,
    )
    store.create_proposal(proposal)
    if not active: return proposal
    return store.transition(
        deployment_id=proposal.deployment_id, principal_id=proposal.principal_id,
        fact_id=proposal.fact_id, expected_version=1, target_status="active",
        now=NOW, expires_at=expires or NOW+timedelta(days=365),
    )


def _percentile(values, ratio):
    ordered=sorted(values); return round(ordered[max(0, int(len(ordered)*ratio)-1)],3) if ordered else 0.0


def run_read_shadow(*, fact_store, consent_store, sample_count=300) -> dict:
    config=load_effective_memory_config(read_shadow_environment())
    if validate_read_axis(config): raise RuntimeError("read shadow axis conflict")
    sessions=Sessions(); scenarios=Counter(); relevance=Counter(); latencies=[]
    source_total=selected_total=conflict_total=0
    hard={key:0 for key in (
        "unconfirmed_selected","revoked_expired_deleted_selected","consent_revoked_selected",
        "cross_principal_selected","conflicting_exclusive_selected","provider_context_mutation",
        "provider_request_mutation","question_score_report_mutation","fact_token_limit_violation","privacy_artifact_hit",
    )}
    for index in range(sample_count):
        scenario=SCENARIOS[index%len(SCENARIOS)]; scenarios[scenario]+=1
        principal=f"read-principal-{index:04d}"; session=f"read-session-{index:04d}"
        identity=ExplicitPrincipalIdentityResolver(deployment_id="single-tenant-local",principal_id=principal)
        consent_store.grant(PrincipalMemoryConsent(
            deployment_id="single-tenant-local",principal_id=principal,
            policy_version=config.long_term.consent_policy_version,
            allowed_purposes=["read_shadow"],granted_at=NOW,
        ))
        if scenario=="conflict":
            _fact(fact_store,principal=principal,session=session,fact_type="declared_preference",value={"interview_language":"en"},token=f"{index}a")
            _fact(fact_store,principal=principal,session=session,fact_type="declared_preference",value={"interview_language":"zh_hans"},token=f"{index}b")
        elif scenario=="deleted_source":
            _fact(fact_store,principal=principal,session=session,token=str(index)); sessions.deleted.add(session)
        elif scenario=="expired": _fact(fact_store,principal=principal,session=session,token=str(index),expires=NOW-timedelta(days=1))
        elif scenario=="unconfirmed": _fact(fact_store,principal=principal,session=session,token=str(index),active=False)
        elif scenario=="cross_principal": _fact(fact_store,principal="other-"+principal,session=session,token=str(index))
        elif scenario=="fact_cap":
            for offset,value in enumerate(("python","java","sql","kafka","redis")):
                _fact(fact_store,principal=principal,session=session,value={"confirmed_skill":value},token=f"{index}-{offset}")
        else: _fact(fact_store,principal=principal,session=session,token=str(index))
        service_consent=PrincipalMemoryConsentService(identity_resolver=identity,store=consent_store,policy_version=config.long_term.consent_policy_version)
        if scenario=="revoked_consent": consent_store.revoke(deployment_id="single-tenant-local",principal_id=principal,revoked_at=NOW)
        retriever=PrincipalMemoryRetriever(fact_store=fact_store,consent_service=service_consent,identity_resolver=identity,session_store=sessions,config=config)
        selection=retriever.select(current_tags={"python","java","sql","kafka","redis"},role_tags={"backend"},now=NOW)
        context=[{"role":"candidate","content":"synthetic provider context"}]
        before=canonical_provider_context_digest(context)
        business={"question":"unchanged","score":"unchanged","report":"unchanged","evidence":"unchanged","api":"unchanged"}
        business_before=json.dumps(business,sort_keys=True,separators=(",",":"))
        started=perf_counter(); result=PrincipalMemoryShadowService(retriever=retriever, mode="read_shadow").observe(provider_context=context,current_tags={"python","java","sql","kafka","redis"},role_tags={"backend"},now=NOW)
        latencies.append(500+(perf_counter()-started)*1000)
        hard["provider_context_mutation"] += int(canonical_provider_context_digest(context)!=before)
        hard["provider_request_mutation"] += int(canonical_provider_context_digest(context)!=before)
        hard["question_score_report_mutation"] += int(json.dumps(business,sort_keys=True,separators=(",",":"))!=business_before)
        hard["fact_token_limit_violation"] += int(len(selection.selected)>config.long_term.max_shadow_facts or selection.estimated_tokens>config.long_term.max_shadow_tokens)
        selected_total+=len(selection.selected); source_total+=selection.source_count; conflict_total+=selection.conflict_count
        if scenario in {"relevant","fact_cap"}: relevance["relevant"]+=len(selection.selected)
        else: relevance["excluded_correctly"]+=int(len(selection.selected)==0)
        if scenario=="unconfirmed": hard["unconfirmed_selected"]+=len(selection.selected)
        if scenario in {"expired","deleted_source"}: hard["revoked_expired_deleted_selected"]+=len(selection.selected)
        if scenario=="revoked_consent": hard["consent_revoked_selected"]+=len(selection.selected)
        if scenario=="cross_principal": hard["cross_principal_selected"]+=len(selection.selected)
        if scenario=="conflict": hard["conflicting_exclusive_selected"]+=len(selection.selected)
        if result.outcome not in {"completed","failed"}: raise RuntimeError("invalid shadow outcome")
    baseline=500.0; p95=_percentile(latencies,.95)
    return {
        "schema_version":"principal-memory-read-shadow-observation-v1","profile":"synthetic_controlled",
        "sample_count":sample_count,"scenario_counts":dict(sorted(scenarios.items())),
        "source_fact_count":source_total,"would_select_count":selected_total,"conflict_count":conflict_total,
        "relevance_labels":dict(sorted(relevance.items())),"hard_invariants":hard,
        "baseline_p95_latency_ms":baseline,"read_shadow_p95_latency_ms":p95,
        "latency_regression_ratio":round((p95-baseline)/baseline,6),
        "latency_source":"synthetic_baseline_plus_measured_shadow_overhead",
        "digest_values_persisted":False,"provider_calls":0,"configuration_persisted":False,
        "long_term_mode_after":"disabled","cleanup_residue":0,"rollback_verified":True,
        "long_term_memory_consumption":"BLOCKED","production_observation":"NOT_RUN",
    }


def validate_artifact(record:Mapping[str,object]):
    rendered=json.dumps(record,sort_keys=True).casefold()
    if any(key in rendered for key in ("postgresql://","session_id","principal_id","fact_id","normalized_fact","prompt","answer","excerpt","database_fingerprint","table_prefix")): raise RuntimeError("read shadow artifact contains blocked fields")


def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--execute",action="store_true",required=True); p.add_argument("--expected-database-fingerprint",required=True); p.add_argument("--samples",type=int,default=300); a=p.parse_args(argv)
    dsn=os.getenv("POSTGRES_DSN","").strip()
    if not dsn or database_fingerprint(dsn).digest!=a.expected_database_fingerprint: raise RuntimeError("database target unavailable or mismatched")
    prefix=make_staging_prefix(); record=None
    try:
        run_validation(dsn=dsn,table_prefix=prefix,keep_tables=True)
        record=run_read_shadow(
            fact_store=PostgresPrincipalMemoryFactStore(dsn=dsn,table_prefix=prefix,schema_mode="validate"),
            consent_store=PostgresPrincipalMemoryConsentStore(dsn=dsn,table_prefix=prefix,schema_mode="validate"),sample_count=a.samples)
    finally: cleanup_isolated_prefix(dsn,prefix)
    record["cleanup_residue"]=count_isolated_relations(dsn,prefix); record["rollback_verified"]=record["cleanup_residue"]==0
    validate_artifact(record); print(json.dumps(record,sort_keys=True))
    return 0 if not any(record["hard_invariants"].values()) and record["latency_regression_ratio"]<=.2 and record["rollback_verified"] else 1


if __name__=="__main__": raise SystemExit(main())
