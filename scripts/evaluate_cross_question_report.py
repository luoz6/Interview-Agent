from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.cross_question_report_diagnostics import (
    RM4B_COMPLETION_STATUS,
    build_rm4b_artifact,
    default_rm4b_fixture_path,
    read_and_validate_rm4b_artifacts,
    replay_rm4b_artifacts,
    write_rm4b_artifacts,
)


DEFAULT_FIXTURE = default_rm4b_fixture_path(ROOT)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or replay the deterministic RM4B multi-question report artifact."
    )
    parser.add_argument(
        "--mode",
        choices=("build", "replay", "complete"),
        required=True,
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.mode == "build":
            artifact, observer = build_rm4b_artifact(args.fixture)
            write_rm4b_artifacts(args.out, artifact, observer)
        elif args.mode == "replay":
            replay_rm4b_artifacts(
                fixture_path=args.fixture,
                output_dir=args.out,
            )
        else:
            artifact, _ = read_and_validate_rm4b_artifacts(args.out)
            if artifact["status"] != RM4B_COMPLETION_STATUS:
                return 2
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"RM4B_DIAGNOSTIC_FAILED: {type(exc).__name__}: {exc}")
        return 2
    print(RM4B_COMPLETION_STATUS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
