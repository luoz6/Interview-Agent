import json

from scripts.run_t64_quality_replays import main, run_quality_replays


def test_t64_runs_all_four_offline_quality_replays_without_provider(tmp_path):
    result = run_quality_replays(out=tmp_path, run_id="test-replays")

    assert result["engineering_status"] == "PASS"
    assert result["quality_status"] == "BLOCKED"
    assert result["provider_called"] is False
    assert result["provider_calls"] == 0
    assert set(result["replays"]) == {
        "initial_question",
        "followup_decision",
        "report_score",
        "report_semantic",
    }
    assert all(
        replay["engineering_status"] == "PASS"
        for replay in result["replays"].values()
    )
    assert json.loads(
        (tmp_path / "test-replays/result.json").read_text(encoding="utf-8")
    ) == result


def test_t64_quality_replay_cli_rejects_unsafe_run_id(tmp_path, capsys):
    assert main(["--out", str(tmp_path), "--run-id", "../escape"]) == 1
    assert '"status": "FAIL"' in capsys.readouterr().out
