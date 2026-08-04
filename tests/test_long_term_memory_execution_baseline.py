from pathlib import Path
import hashlib


BASELINE = Path("docs/long-term-memory-production-execution-baseline.md")
ENV_EXAMPLE = Path(".env.example")


def baseline_text() -> str:
    return BASELINE.read_text(encoding="utf-8")


def test_execution_baseline_is_frozen_without_production_authority() -> None:
    text = baseline_text()

    for state in (
        "EXECUTION_BASELINE=FROZEN",
        "HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED",
        "PRODUCTION_DATA_USE_SPEC=NOT_APPROVED",
        "PRODUCTION_BUDGET_SHADOW=NOT_RUN",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "IMPLEMENTATION=NOT_AUTHORIZED",
        "PRODUCTION_CANARY=NOT_AUTHORIZED",
    ):
        assert state in text


def test_baseline_records_revisions_divergence_and_test_evidence() -> None:
    text = baseline_text()

    assert "848699fd93ecfa8a55fe9e6b3f4bf7d06710e201" in text
    assert "80936bbd73ce0199b33de5db93c13e1edcb81281" in text
    assert "6969efa119de0da33698f0de74f4fdeee502b375" in text
    assert "behind `0`, ahead `5`" in text
    assert "1726 passed, 166 skipped, 1 warning" in text
    assert "Deployed revision | `NOT_OBSERVED`" in text


def test_baseline_pins_the_canonical_plan_digest() -> None:
    plan = Path(
        "docs/superpowers/plans/"
        "2026-08-03-long-term-memory-production-shadows-consumption-and-promotion.md"
    )
    canonical_bytes = (
        plan.read_bytes()
        .replace(b"\r\n", b"\n")
        .replace(b"\r", b"\n")
    )
    digest = hashlib.sha256(canonical_bytes).hexdigest().upper()

    assert digest == "DE0AFE41E815B8BEFBD56AE4ACDD5ED7E07540A0BAFFD3D06BDCA4E6542C3227"
    assert digest in baseline_text()


def test_baseline_preserves_user_owned_paths() -> None:
    text = baseline_text()

    for path in (
        ".hallmark/log.json",
        "frontend/src/pages/ReportsPage.jsx",
        "frontend/src/styles/reports-app.css",
        "tests/browser/reference-ui.spec.js",
        "tests/browser/reports-ui.spec.js",
    ):
        assert path in text
    assert "no reset, no restore, no clean" in text


def test_repository_example_keeps_existing_memory_modes_disabled() -> None:
    env = ENV_EXAMPLE.read_text(encoding="utf-8")

    for assignment in (
        "MEMORY_BUDGET_MODE=disabled",
        "MEMORY_COMPRESSION_MODE=disabled",
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED=false",
        "MEMORY_LONG_TERM_MODE=disabled",
        "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED=false",
        "MEMORY_LONG_TERM_READ_SHADOW_ENABLED=false",
    ):
        assert assignment in env


def test_unapproved_c1a_configuration_is_not_prematurely_added() -> None:
    env = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "MEMORY_LONG_TERM_ASSIST_C1A_ENABLED" not in env
    assert "MEMORY_AUTHENTICATED_SELF_SERVICE_ENABLED" not in env
    assert "MEMORY_LONG_TERM_MAX_PRINCIPALS" not in env
    assert "MEMORY_LONG_TERM_MAX_SESSIONS" not in env
