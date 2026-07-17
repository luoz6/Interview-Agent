from scripts.stage43b_recovery_acceptance import (
    CHECKS,
    AcceptanceFailure,
    run_acceptance,
)


class FakeAdapter:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []
        self.cleaned = False

    def setup(self):
        self.calls.append("setup")

    def run_check(self, name):
        self.calls.append(name)
        if name == self.failure:
            raise AcceptanceFailure("check_failed")
        return {"status": "PASS"}

    def cleanup(self):
        self.cleaned = True


def test_runner_executes_every_named_check_and_cleans_up():
    adapter = FakeAdapter()

    result = run_acceptance(adapter)

    assert result["status"] == "PASS"
    assert tuple(result["checks"]) == CHECKS
    assert adapter.cleaned is True


def test_failed_check_returns_stable_code_and_cleans_up():
    adapter = FakeAdapter(failure=CHECKS[2])

    result = run_acceptance(adapter)

    assert result == {
        "status": "FAIL",
        "error_code": "check_failed",
        "failed_check": CHECKS[2],
        "checks": {
            CHECKS[0]: {"status": "PASS"},
            CHECKS[1]: {"status": "PASS"},
        },
    }
    assert adapter.cleaned is True
