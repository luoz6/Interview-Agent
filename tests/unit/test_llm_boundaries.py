from typing import get_args, get_type_hints

from app.adapters.providers.llm_config import (
    LLMConfig as CanonicalLLMConfig,
    MissingLLMConfigError as CanonicalMissingLLMConfigError,
)
from app.domain.interview.plan_revision import PlanConfigurationSnapshot
from app.ports.llm import InterviewLLM as CanonicalInterviewLLM
from app.adapters.providers.llm import (
    InterviewLLM as LegacyInterviewLLM,
    LLMConfig as LegacyLLMConfig,
    MissingLLMConfigError as LegacyMissingLLMConfigError,
)


def test_llm_contract_and_config_exports_preserve_object_identity():
    assert LegacyInterviewLLM is CanonicalInterviewLLM
    assert LegacyLLMConfig is CanonicalLLMConfig
    assert LegacyMissingLLMConfigError is CanonicalMissingLLMConfigError


def test_llm_port_annotations_resolve_at_runtime():
    hints = get_type_hints(CanonicalInterviewLLM.generate_plan)

    assert PlanConfigurationSnapshot in get_args(hints["configuration"])
