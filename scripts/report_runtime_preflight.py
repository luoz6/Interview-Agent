from __future__ import annotations

from app.services.report_runtime_preflight import run_report_runtime_preflight


def main() -> int:
    result = run_report_runtime_preflight(check_external=True)
    for check in result.checks:
        label = "PASS" if check.passed else "FAIL"
        print(f"[{label}] {check.code}")
    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
