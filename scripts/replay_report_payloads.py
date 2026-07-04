import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.report_replay import replay_fixture


def iter_fixture_paths(target: str | None) -> list[Path]:
    if target:
        path = Path(target)
        if path.is_dir():
            return sorted(path.glob("*.json"))
        return [path]

    trace_dir = Path(os.getenv("REPORT_TRACE_DIR", "tests/fixtures/report_payloads"))
    return sorted(trace_dir.glob("*.json"))


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    target = args[0] if args else None
    fixture_paths = iter_fixture_paths(target)
    if not fixture_paths:
        print("No replay fixtures found.")
        return 1

    for fixture_path in fixture_paths:
        report = replay_fixture(str(fixture_path))
        first_feedback_reference_count = len(report.feedbacks[0].references) if report.feedbacks else 0
        print(
            f"{fixture_path.name} "
            f"is_fallback={report.is_fallback} "
            f"overall_score={report.overall_score} "
            f"first_feedback_reference_count={first_feedback_reference_count}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
