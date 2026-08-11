from pathlib import Path

from app.services.report_replay import replay_fixture_with_quality


FIXTURE_DIR = Path("tests/fixtures/report_payloads")


def test_replay_payload_fixtures_pass_quality_gates():
    fixture_paths = sorted(FIXTURE_DIR.glob("*.json"))
    assert fixture_paths

    for path in fixture_paths:
        report, issues = replay_fixture_with_quality(str(path))
        assert report.is_fallback is False, path.name
        assert issues == [], f"{path.name}: {issues}"
