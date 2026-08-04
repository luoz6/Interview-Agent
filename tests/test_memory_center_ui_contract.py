from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "frontend" / "public"


def _read(name: str) -> str:
    return (PUBLIC / name).read_text(encoding="utf-8")


def test_memory_center_is_a_dependency_free_public_entrypoint():
    html = _read("memory-center.html")
    css = _read("memory-center.css")
    script = _read("memory-center.js")

    assert '<link rel="stylesheet" href="/memory-center.css"' in html
    assert '<script type="module" src="/memory-center.js"' in html
    assert "@import" not in css
    assert "url(http" not in css
    assert "https://" not in html + css + script
    assert "<img" not in html


def test_memory_center_only_accepts_controlled_taxonomy_values():
    html = _read("memory-center.html")
    script = _read("memory-center.js")

    assert '<select id="fact-key"' in html
    assert '<select id="fact-value"' in html
    assert 'id="fact-value"' not in html.replace('<select id="fact-value"', "")
    assert "canonical_principal_fact" not in script
    for taxonomy_key in (
        "interview_language",
        "target_role_family",
        "confirmed_skill",
        "learning_goal",
        "accessibility_preference",
    ):
        assert taxonomy_key in script
    assert "textarea" not in html.lower()
    assert 'type="text"' not in html.lower()


def test_memory_center_uses_safe_refs_without_rendering_durable_locators():
    html = _read("memory-center.html")
    script = _read("memory-center.js")

    assert "item.safe_ref" in script
    assert "item.version" in script
    for forbidden in (
        "item.fact_id",
        "item.principal_id",
        "item.session_id",
        "source_locator",
        "source_digest",
        "deployment_id",
    ):
        assert forbidden not in script
    assert "principal_id" not in html
    assert "fact_id" not in html
    assert "session_id" not in html


def test_memory_center_mutations_are_explicit_and_delete_is_confirmed():
    html = _read("memory-center.html")
    script = _read("memory-center.js")

    assert '"X-Local-Memory-Action": "1"' in script
    assert '<dialog id="delete-dialog" aria-labelledby="delete-title">' in html
    assert 'id="cancel-delete"' in html
    assert 'id="confirm-delete"' in html
    assert '.showModal()' in script
    assert 'request("", { method: "DELETE", headers: mutationHeaders })' in script


def test_memory_center_supports_confirmation_correction_and_session_override():
    html = _read("memory-center.html")
    script = _read("memory-center.js")

    assert 'actionButton("确认"' in script
    assert 'actionButton("编辑"' in script
    assert 'method: "PUT"' in script
    assert "expected_version: item.version" in script
    assert "normalized_value: { [key]: select.value }" in script
    assert 'id="session-control-form"' in html
    assert 'id="session-key"' in html
    assert 'data-session-action="ignore"' in html
    assert 'data-session-action="restore"' in html
    assert "encodeURIComponent(sessionKey)" in script
    assert "item.session_id" not in script
    assert "item.fact_id" not in script


def test_memory_center_has_semantic_accessibility_and_reduced_motion_contracts():
    html = _read("memory-center.html")
    css = _read("memory-center.css")

    assert '<a class="skip-link" href="#memory-main">' in html
    assert '<main id="memory-main"' in html
    assert "<fieldset" in html and "<legend" in html
    assert 'role="status" aria-live="polite"' in html
    assert 'aria-labelledby="delete-title"' in html
    assert ":focus-visible" in css
    assert "outline: 3px" in css
    assert "min-height: 44px" in css
    assert "@media (max-width: 760px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "animation: none !important" in css
    assert "transition: none !important" in css


def test_memory_center_does_not_connect_memory_to_evaluation_or_knowledge_paths():
    html = _read("memory-center.html")
    script = _read("memory-center.js")
    executable = script.lower()

    for forbidden_route in (
        "/report",
        "/score",
        "/evaluation",
        "/knowledge",
        "/prep",
        "/review",
    ):
        assert forbidden_route not in executable
    assert "resume" not in executable
    assert "candidate" not in executable
    for forbidden_copy in ("评分", "报告", "知识库", "Knowledge"):
        assert forbidden_copy not in html
    assert "只用于你明确许可的本机记忆用途" in html
