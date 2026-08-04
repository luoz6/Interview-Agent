from datetime import datetime, timedelta, timezone

import pytest

from app.services.in_memory_principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import (
    InMemoryPrincipalMemoryConsentStore,
)
from app.services.in_memory_principal_memory_control import (
    InMemoryPrincipalMemoryControlStore,
)
from app.services.llm import OpenAIInterviewLLM, _build_followup_prompt
from app.services.memory_config import load_effective_memory_config
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_consent import (
    PrincipalMemoryConsent,
    PrincipalMemoryConsentService,
)
from app.services.principal_memory_consume import (
    ASSISTANCE_CONTEXT_KIND,
    ASSISTANCE_LABEL,
    ASSISTANCE_WARNING,
    PrincipalMemoryLocalConsumeService,
)
from app.services.principal_memory_control import PrincipalMemoryControlService
from app.services.principal_memory_contracts import canonical_principal_fact
from app.services.principal_memory_lifecycle import PrincipalMemoryLifecycleService


NOW = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
BASE_CONTEXT = [
    {"role": "interviewer", "content": "Describe the tradeoff."},
    {"role": "knowledge_evidence", "content": "Synthetic public guidance."},
    {"role": "candidate", "content": "I chose a queue for isolation."},
]


class Sessions:
    def __init__(self):
        self.deleted = set()

    def get(self, session_id):
        return {
            "session_id": session_id,
            "deletion_status": "deleted" if session_id in self.deleted else "active",
        }


class WordEstimator:
    def estimate_text(self, text, *, model):
        del model
        return len(text.replace("\n", " ").split())

    def estimate_messages(self, messages, *, model):
        return self.estimate_text(
            "\n".join(
                f"{message.get('role', '')}: {message.get('content', '')}"
                for message in messages
            ),
            model=model,
        )


class AlwaysOverCapEstimator(WordEstimator):
    def estimate_text(self, text, *, model):
        del text, model
        return 121


def build_consumer(*, estimator=None):
    config = load_effective_memory_config(
        {
            "MEMORY_LONG_TERM_MODE": "local_consume",
            "MEMORY_LOCAL_PRINCIPAL_ENABLED": "true",
            "MEMORY_LOCAL_PRINCIPAL_ID": "local-owner",
            "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED": "true",
            "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
            "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
            "MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED": "true",
        }
    )
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        assurance="trusted_local",
        clock=lambda: NOW,
    )
    consents = InMemoryPrincipalMemoryConsentStore()
    consents.grant(
        PrincipalMemoryConsent(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            policy_version=config.long_term.consent_policy_version,
            allowed_purposes=["fact_storage", "local_consume"],
            granted_at=NOW,
        )
    )
    controls = InMemoryPrincipalMemoryControlStore()
    control = PrincipalMemoryControlService(
        identity_resolver=resolver,
        store=controls,
        clock=lambda: NOW,
    )
    consent = PrincipalMemoryConsentService(
        identity_resolver=resolver,
        store=consents,
        policy_version=config.long_term.consent_policy_version,
        control_service=control,
    )
    facts = InMemoryPrincipalMemoryFactStore()
    sessions = Sessions()
    lifecycle = PrincipalMemoryLifecycleService(
        identity_resolver=resolver,
        consent_service=consent,
        fact_store=facts,
        session_store=sessions,
        config=config,
        clock=lambda: NOW,
    )
    consumer = PrincipalMemoryLocalConsumeService(
        fact_store=facts,
        consent_service=consent,
        identity_resolver=resolver,
        session_store=sessions,
        config=config,
        estimator=estimator or WordEstimator(),
        model="synthetic-model",
    )
    return consumer, lifecycle, facts, consents, control


def declare(lifecycle, fact_type, value):
    return lifecycle.declare(
        fact_type=fact_type,
        normalized_fact=canonical_principal_fact(value),
    )


def prepare(consumer, *, current_tags=None, role_tags=None, session_id="session-a"):
    return consumer.prepare(
        provider_context=BASE_CONTEXT,
        current_tags=set(current_tags or {"python"}),
        role_tags=set(role_tags or {"backend"}),
        now=NOW,
        session_id=session_id,
    )


def test_local_consume_golden_block_is_bounded_labelled_and_before_current_answer():
    consumer, lifecycle, *_ = build_consumer()
    declare(lifecycle, "declared_preference", {"interview_language": "zh_hans"})
    declare(lifecycle, "learning_goal", {"learning_goal": "python"})
    declare(lifecycle, "declared_preference", {"target_role_family": "backend"})

    result = consumer.finalize(prepare(consumer), now=NOW)

    assert result.outcome == "consumed"
    assert result.selected_count == 3
    assert result.estimated_tokens <= 120
    assert result.provider_context[-2]["role"] == "system"
    assert result.provider_context[-2]["context_kind"] == ASSISTANCE_CONTEXT_KIND
    assert result.provider_context[-1] == BASE_CONTEXT[-1]
    block = result.provider_context[-2]["content"]
    assert block.startswith(f"[{ASSISTANCE_LABEL}]\n")
    assert block.endswith(f"[/{ASSISTANCE_LABEL}]")
    assert ASSISTANCE_WARNING in block
    assert "interview_language" in block
    assert "learning_goal" in block
    assert "target_role_family" in block
    assert "fact_id" not in block
    assert "principal" not in block.casefold()
    assert "session_id" not in block.casefold()
    assert "session-local" not in block.casefold()
    prompt = _build_followup_prompt(result.provider_context)
    assert prompt.count(ASSISTANCE_LABEL) == 2
    assert prompt.index(ASSISTANCE_LABEL) < prompt.index(BASE_CONTEXT[-1]["content"])
    fitted = OpenAIInterviewLLM(chat_model=object())._fit_followup_context(
        result.provider_context
    )
    fitted_blocks = [
        item
        for item in fitted
        if item.get("context_kind") == ASSISTANCE_CONTEXT_KIND
    ]
    assert len(fitted_blocks) == 1
    assert fitted_blocks[0]["content"] == block
    assert fitted[-1]["role"] == "candidate"


def test_fact_and_token_caps_are_hard_and_never_partially_truncate_a_fact():
    consumer, lifecycle, *_ = build_consumer()
    declare(lifecycle, "declared_preference", {"interview_language": "en"})
    declare(lifecycle, "declared_preference", {"target_role_family": "backend"})
    declare(lifecycle, "learning_goal", {"learning_goal": "python"})
    declare(lifecycle, "learning_goal", {"learning_goal": "kafka"})

    result = consumer.finalize(
        prepare(consumer, current_tags={"python", "kafka"}), now=NOW
    )
    assert result.selected_count == 3
    assert result.provider_context[-2]["content"].count("- category=") == 3

    over_cap, over_lifecycle, *_ = build_consumer(
        estimator=AlwaysOverCapEstimator()
    )
    declare(over_lifecycle, "declared_preference", {"interview_language": "en"})
    suppressed = over_cap.finalize(prepare(over_cap), now=NOW)
    assert suppressed.reason == "token_cap"
    assert suppressed.provider_context == BASE_CONTEXT
    assert ASSISTANCE_LABEL not in repr(suppressed.provider_context)


def test_confirmed_skill_and_irrelevant_current_context_are_excluded():
    consumer, lifecycle, *_ = build_consumer()
    declare(lifecycle, "confirmed_skill", {"confirmed_skill": "python"})
    declare(lifecycle, "learning_goal", {"learning_goal": "kafka"})
    declare(lifecycle, "declared_preference", {"target_role_family": "security"})
    declare(
        lifecycle,
        "accessibility_preference",
        {"accessibility_preference": "keyboard_only"},
    )

    result = consumer.finalize(prepare(consumer), now=NOW)
    assert result.provider_context == BASE_CONTEXT
    assert result.selected_count == 0


@pytest.mark.parametrize("race", ["disable", "revoke_consent", "revoke_fact", "delete"])
def test_operation_time_recheck_suppresses_disable_revoke_and_delete_races(race):
    consumer, lifecycle, facts, consents, control = build_consumer()
    declared = declare(
        lifecycle,
        "declared_preference",
        {"interview_language": "mixed"},
    )
    prepared = prepare(consumer)

    if race == "disable":
        control.set_global_enabled(False)
    elif race == "revoke_consent":
        consents.revoke(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            revoked_at=NOW + timedelta(seconds=1),
        )
    elif race == "revoke_fact":
        lifecycle.revoke(
            fact_type="declared_preference",
            normalized_fact=canonical_principal_fact(
                {"interview_language": "mixed"}
            ),
            expected_version=declared["version"],
        )
    else:
        facts.purge_by_principal(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
        )

    result = consumer.finalize(prepared, now=NOW + timedelta(seconds=2))
    assert result.provider_context == BASE_CONTEXT
    assert result.outcome != "consumed"


def test_session_ignore_is_immediate_and_scoped():
    consumer, lifecycle, *_rest, control = build_consumer()
    declare(lifecycle, "declared_preference", {"interview_language": "en"})
    control.set_session_ignored("session-a", True)

    ignored = consumer.finalize(prepare(consumer, session_id="session-a"), now=NOW)
    allowed = consumer.finalize(prepare(consumer, session_id="session-b"), now=NOW)

    assert ignored.provider_context == BASE_CONTEXT
    assert allowed.outcome == "consumed"


def test_non_local_modes_and_non_trusted_identity_never_consume():
    consumer, lifecycle, *_ = build_consumer()
    declare(lifecycle, "declared_preference", {"interview_language": "en"})
    consumer.config = consumer.config.model_copy(
        update={
            "long_term": consumer.config.long_term.model_copy(
                update={"mode": "read_shadow"}
            )
        }
    )
    assert consumer.finalize(prepare(consumer), now=NOW).provider_context == BASE_CONTEXT

    consumer, lifecycle, *_ = build_consumer()
    declare(lifecycle, "declared_preference", {"interview_language": "en"})
    consumer.identity_resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        assurance="test",
        clock=lambda: NOW,
    )
    assert consumer.finalize(prepare(consumer), now=NOW).provider_context == BASE_CONTEXT


def test_consumption_is_read_only_and_does_not_create_effects_or_change_facts():
    consumer, lifecycle, facts, *_ = build_consumer()
    declare(lifecycle, "declared_preference", {"interview_language": "en"})
    before = facts.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        limit=100,
        include_terminal=True,
    )

    assert consumer.finalize(prepare(consumer), now=NOW).outcome == "consumed"

    after = facts.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        limit=100,
        include_terminal=True,
    )
    assert after == before


def test_local_consume_caps_cannot_be_configured_above_the_absolute_limit():
    complete = {
        "MEMORY_LONG_TERM_MODE": "local_consume",
        "MEMORY_LOCAL_PRINCIPAL_ENABLED": "true",
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED": "true",
        "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
        "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
        "MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED": "true",
    }
    with pytest.raises(ValueError):
        load_effective_memory_config(
            {**complete, "MEMORY_LONG_TERM_MAX_LOCAL_CONSUME_FACTS": "4"}
        )
    with pytest.raises(ValueError):
        load_effective_memory_config(
            {**complete, "MEMORY_LONG_TERM_MAX_LOCAL_CONSUME_TOKENS": "121"}
        )
