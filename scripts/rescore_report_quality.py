import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.report_eval_replay import rescore_run


def main() -> int:
    parser = argparse.ArgumentParser(description="Rescore a saved report-quality run")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("tests/golden/report_quality_v1.json"),
    )
    args = parser.parse_args()
    metrics = rescore_run(run_dir=args.run_dir, dataset_path=args.dataset)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0 if metrics["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
