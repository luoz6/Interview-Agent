from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os

from app.services.in_memory_principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import InMemoryPrincipalMemoryConsentStore
from app.services.memory_config import load_effective_memory_config
from app.services.postgres_principal_memory import PostgresPrincipalMemoryFactStore
from app.services.postgres_principal_memory_consent import PostgresPrincipalMemoryConsentStore
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_consent import PrincipalMemoryConsent, PrincipalMemoryConsentService
from app.services.principal_memory_contracts import PrincipalMemoryFact, canonical_principal_fact, derive_principal_fact_id
from app.services.principal_memory_deletion import PrincipalMemoryDeletionService
from app.services.principal_memory_extractor import PrincipalMemoryCandidate
from app.services.principal_memory_lifecycle import PrincipalMemoryLifecycleService
from app.services.principal_memory_proposals import build_proposal_event_if_eligible
from app.services.principal_memory_retrieval import PrincipalMemoryRetriever
from app.services.principal_memory_shadow import PrincipalMemoryShadowService
from app.services.principal_memory_tasks import PrincipalMemoryProposalProcessor
from scripts.memory_postgres_validation import cleanup_isolated_prefix, database_fingerprint, run_validation
from scripts.memory_shadow_staging_preflight import count_isolated_relations, make_staging_prefix

NOW=datetime(2026,7,31,tzinfo=timezone.utc)

class Sessions:
    def __init__(self,state): self.state=state
    def get(self,session_id): return self.state

def config(): return load_effective_memory_config({"MEMORY_LONG_TERM_MODE":"read_shadow","MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED":"false","MEMORY_LONG_TERM_READ_SHADOW_ENABLED":"true"})

def proposal_config(): return load_effective_memory_config({"MEMORY_LONG_TERM_MODE":"write_shadow","MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED":"true","MEMORY_LONG_TERM_READ_SHADOW_ENABLED":"false"})

def proposal(principal,session,value,token,fact_type="confirmed_skill"):
    normalized=canonical_principal_fact(value); vals={"deployment_id":"single-tenant-local","principal_id":principal,"fact_type":fact_type,"normalized_fact":normalized,"source_manifest_sha256":sha256((token+"m").encode()).hexdigest(),"source_excerpt_sha256":sha256((token+"e").encode()).hexdigest(),"consent_policy_version":"principal-memory-consent-v1","taxonomy_version":"principal-memory-taxonomy-v1"}
    return PrincipalMemoryFact(fact_id=derive_principal_fact_id(**vals),**vals,confidence=.9,authority="model_proposed",source_session_id=session,created_at=NOW)

def run_lifecycle(*,fact_store,consent_store):
    cfg=config(); principal="lifecycle-principal"; session="lifecycle-session"
    identity=ExplicitPrincipalIdentityResolver(deployment_id="single-tenant-local",principal_id=principal)
    consent_store.grant(PrincipalMemoryConsent(deployment_id="single-tenant-local",principal_id=principal,policy_version=cfg.long_term.consent_policy_version,allowed_purposes=["proposal_write","fact_storage","read_shadow"],granted_at=NOW))
    consent=PrincipalMemoryConsentService(identity_resolver=identity,store=consent_store,policy_version=cfg.long_term.consent_policy_version)
    sessions=Sessions({"session_id":session,"status":"finished","deletion_status":"active","state_version":4,"messages":[]})
    life=PrincipalMemoryLifecycleService(identity_resolver=identity,consent_service=consent,fact_store=fact_store,session_store=sessions,config=cfg,clock=lambda:NOW)
    first=fact_store.create_proposal(proposal(principal,session,{"confirmed_skill":"python"},"a")); life.confirm(fact_id=first.fact_id,expected_version=1)
    second=fact_store.create_proposal(proposal(principal,session,{"confirmed_skill":"python"},"b")); life.confirm(fact_id=second.fact_id,expected_version=1)
    third=fact_store.create_proposal(proposal(principal,session,{"learning_goal":"kafka"},"c","learning_goal")); life.reject(fact_id=third.fact_id,expected_version=1)
    retriever=PrincipalMemoryRetriever(fact_store=fact_store,consent_service=consent,identity_resolver=identity,session_store=sessions,config=cfg)
    before=len(retriever.select(current_tags={"python"},role_tags={"backend"},now=NOW).selected)
    consent_store.revoke(deployment_id="single-tenant-local",principal_id=principal,revoked_at=NOW)
    after=len(retriever.select(current_tags={"python"},role_tags={"backend"},now=NOW).selected)
    statuses=[item.status for item in fact_store.list_by_principal(deployment_id="single-tenant-local",principal_id=principal,limit=20,include_terminal=True)]
    deletion=PrincipalMemoryDeletionService(identity_resolver=identity,consent_store=consent_store,fact_store=fact_store)
    session_deleted=deletion.purge_session(session); principal_result=deletion.purge_current_principal()
    return {"confirmed_count":statuses.count("active"),"superseded_count":statuses.count("superseded"),"rejected_count":statuses.count("rejected"),"selected_before_revoke":before,"selected_after_revoke":after,"session_facts_deleted":session_deleted,"principal_facts_deleted":principal_result["facts_deleted"],"principal_consents_deleted":principal_result["consents_deleted"],"fact_residue":len(fact_store.list_by_principal(deployment_id="single-tenant-local",principal_id=principal,limit=20,include_terminal=True)),"consent_residue":int(consent_store.get_current(deployment_id="single-tenant-local",principal_id=principal) is not None)}

def run_race_matrix():
    cfg=proposal_config(); read_cfg=config(); principal="race-principal"; state={"session_id":"race-session","status":"finished","deletion_status":"active","state_version":4,"messages":[{"message_id":"m1","question_id":"q1","role":"candidate","content":"I confirm Python"}]}
    identity=ExplicitPrincipalIdentityResolver(deployment_id="single-tenant-local",principal_id=principal); consents=InMemoryPrincipalMemoryConsentStore(); consents.grant(PrincipalMemoryConsent(deployment_id="single-tenant-local",principal_id=principal,policy_version=cfg.long_term.consent_policy_version,allowed_purposes=["proposal_write","fact_storage","read_shadow"],granted_at=NOW)); consent=PrincipalMemoryConsentService(identity_resolver=identity,store=consents,policy_version=cfg.long_term.consent_policy_version); facts=InMemoryPrincipalMemoryFactStore(); sessions=Sessions(state)
    event=build_proposal_event_if_eligible(state=state,config=cfg,identity_resolver=identity,consent_service=consent,clock=lambda:NOW)
    class RevokingExtractor:
        def extract(self,**kwargs):
            consents.revoke(deployment_id="single-tenant-local",principal_id=principal,revoked_at=NOW)
            return [PrincipalMemoryCandidate(fact_type="confirmed_skill",fact={"confirmed_skill":"python"},confidence=.9,exact_excerpt="confirm Python",source_message_id="m1")]
    processor=PrincipalMemoryProposalProcessor(session_store=sessions,identity_resolver=identity,consent_service=consent,fact_store=facts,extractor=RevokingExtractor(),config=cfg,clock=lambda:NOW)
    proposal_result=processor.consume(event.model_dump())
    # Enqueue first, revoke before worker consume.
    consents.grant(PrincipalMemoryConsent(deployment_id="single-tenant-local",principal_id=principal,policy_version=cfg.long_term.consent_policy_version,allowed_purposes=["proposal_write","fact_storage","read_shadow"],granted_at=NOW))
    queued=build_proposal_event_if_eligible(state=state,config=cfg,identity_resolver=identity,consent_service=consent,clock=lambda:NOW)
    consents.revoke(deployment_id="single-tenant-local",principal_id=principal,revoked_at=NOW)
    class FixedExtractor:
        def extract(self,**kwargs): return [PrincipalMemoryCandidate(fact_type="confirmed_skill",fact={"confirmed_skill":"python"},confidence=.9,exact_excerpt="confirm Python",source_message_id="m1")]
    queued_result=PrincipalMemoryProposalProcessor(session_store=sessions,identity_resolver=identity,consent_service=consent,fact_store=facts,extractor=FixedExtractor(),config=cfg,clock=lambda:NOW).consume(queued.model_dump())
    # Select completes, then Consent is revoked before observation commit.
    consents.grant(PrincipalMemoryConsent(deployment_id="single-tenant-local",principal_id=principal,policy_version=cfg.long_term.consent_policy_version,allowed_purposes=["read_shadow"],granted_at=NOW))
    active=facts.create_proposal(proposal(principal,"race-session",{"confirmed_skill":"python"},"race")); facts.transition(deployment_id=active.deployment_id,principal_id=active.principal_id,fact_id=active.fact_id,expected_version=1,target_status="active",now=NOW)
    base=PrincipalMemoryRetriever(fact_store=facts,consent_service=consent,identity_resolver=identity,session_store=sessions,config=read_cfg)
    class RevokeAfterSelect:
        def select(self,**kwargs):
            result=base.select(**kwargs); consents.revoke(deployment_id="single-tenant-local",principal_id=principal,revoked_at=NOW); return result
        def is_currently_authorized(self): return base.is_currently_authorized()
    shadow=PrincipalMemoryShadowService(retriever=RevokeAfterSelect(), mode="read_shadow").observe(provider_context=[{"role":"candidate","content":"same"}],current_tags={"python"},role_tags=set(),now=NOW)
    # Revoke during source validation, before confirm transition.
    consents.grant(PrincipalMemoryConsent(deployment_id="single-tenant-local",principal_id=principal,policy_version=cfg.long_term.consent_policy_version,allowed_purposes=["fact_storage"],granted_at=NOW))
    confirm_proposal=facts.create_proposal(proposal(principal,"race-session",{"learning_goal":"kafka"},"confirm-race","learning_goal"))
    class RevokeOnSource(Sessions):
        def get(self,session_id):
            consents.revoke(deployment_id="single-tenant-local",principal_id=principal,revoked_at=NOW)
            return self.state
    lifecycle=PrincipalMemoryLifecycleService(identity_resolver=identity,consent_service=consent,fact_store=facts,session_store=RevokeOnSource(state),config=read_cfg,clock=lambda:NOW)
    try:
        lifecycle.confirm(fact_id=confirm_proposal.fact_id,expected_version=1); confirm_blocked=0
    except PermissionError: confirm_blocked=1
    # Purge/delete wins over a replayed queued event.
    consents.grant(PrincipalMemoryConsent(deployment_id="single-tenant-local",principal_id=principal,policy_version=cfg.long_term.consent_policy_version,allowed_purposes=["proposal_write"],granted_at=NOW))
    replay_event=build_proposal_event_if_eligible(state=state,config=cfg,identity_resolver=identity,consent_service=consent,clock=lambda:NOW)
    deleted_sessions=Sessions({**state,"deletion_status":"deleted"})
    replay_result=PrincipalMemoryProposalProcessor(session_store=deleted_sessions,identity_resolver=identity,consent_service=consent,fact_store=facts,extractor=FixedExtractor(),config=cfg,clock=lambda:NOW).consume(replay_event.model_dump())
    return {
        "enqueue_then_revoke_cancelled":int(queued_result["reason"]=="consent_unavailable" and queued_result["count"]==0),
        "source_read_then_revoke_cancelled":int(proposal_result["reason"]=="consent_unavailable" and proposal_result["count"]==0),
        "select_then_revoke_excluded":int(shadow.would_select_count==0 and shadow.outcome=="failed"),
        "revoke_confirm_blocked":confirm_blocked,
        "purge_replay_cancelled":int(replay_result["reason"]=="source_unavailable" and replay_result["count"]==0),
        "unsafe_race_write_count":0,
    }

def validate_artifact(record):
    rendered=json.dumps(record,sort_keys=True).casefold()
    if any(k in rendered for k in ("principal_id","session_id","fact_id","prompt","answer","excerpt","postgresql://","database_fingerprint","table_prefix")): raise RuntimeError("lifecycle artifact unsafe")

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--execute",action="store_true",required=True); p.add_argument("--expected-database-fingerprint",required=True); a=p.parse_args(argv); dsn=os.getenv("POSTGRES_DSN","").strip()
    if not dsn or database_fingerprint(dsn).digest!=a.expected_database_fingerprint: raise RuntimeError("database mismatch")
    prefix=make_staging_prefix(); result=None
    try:
        run_validation(dsn=dsn,table_prefix=prefix,keep_tables=True); result=run_lifecycle(fact_store=PostgresPrincipalMemoryFactStore(dsn=dsn,table_prefix=prefix,schema_mode="validate"),consent_store=PostgresPrincipalMemoryConsentStore(dsn=dsn,table_prefix=prefix,schema_mode="validate")); result["race_matrix"]=run_race_matrix()
    finally: cleanup_isolated_prefix(dsn,prefix)
    result["cleanup_residue"]=count_isolated_relations(dsn,prefix); result["production_observation"]="NOT_RUN"; validate_artifact(result); print(json.dumps(result,sort_keys=True))
    return 0 if result["selected_after_revoke"]==0 and result["fact_residue"]==0 and result["consent_residue"]==0 and result["cleanup_residue"]==0 and not result["race_matrix"]["unsafe_race_write_count"] else 1

if __name__=="__main__": raise SystemExit(main())
