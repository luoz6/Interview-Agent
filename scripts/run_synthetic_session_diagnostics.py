from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.synthetic_session_diagnostics import (
    RM5_COMPLETION_STATUS,
    RM5_FAILURE_STATUS,
    build_rm5_artifact,
    default_rm5_scenario_path,
    replay_rm5_artifact,
)


DEFAULT_SCENARIOS = default_rm5_scenario_path(ROOT)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run or replay the offline RM5 synthetic-session diagnostic contract."
    )
    parser.add_argument("--mode", choices=("contract", "dry", "resume", "replay"), required=True)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scenario-id", action="append", dest="scenario_ids")
    parser.add_argument("--run-id", default="rm5-offline-diagnostic-v1")
    parser.add_argument(
        "--fail-after",
        type=int,
        default=None,
        help="Inject a terminal failure after N completed scenarios to exercise resume.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.mode == "replay":
            manifest = replay_rm5_artifact(
                scenario_path=args.scenarios,
                output_dir=args.out,
            )
            print(manifest.get("status", RM5_COMPLETION_STATUS))
            return 0
        if args.mode == "contract":
            # Contract mode deliberately builds no session records and therefore
            # cannot make a Provider call.  Use dry mode for the full artifact.
            from app.services.synthetic_session_diagnostics import load_rm5_scenarios

            load_rm5_scenarios(args.scenarios)
            print("RM5_SYNTHETIC_CONTRACT_VALID")
            return 0
        manifest = build_rm5_artifact(
            scenario_path=args.scenarios,
            output_dir=args.out,
            scenario_ids=args.scenario_ids,
            run_id=args.run_id,
            fail_after=args.fail_after,
            resume=args.mode == "resume",
        )
        print(manifest["status"])
        return 0 if manifest["status"] == RM5_COMPLETION_STATUS else 2
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"RM5_SYNTHETIC_DIAGNOSTIC_FAILED: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
