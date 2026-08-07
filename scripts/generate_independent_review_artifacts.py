from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.services.independent_review_handoff import export_reviewer_handoff


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export a reviewer-only independent-review handoff into an empty "
            "staging directory. This command never reads coordinator keys."
        )
    )
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument(
        "--review-kind",
        choices=(
            "gate2_calibration",
            "gate3_dataset",
            "gate3_fixed_adaptive",
            "t49_semantic",
        ),
        required=True,
    )
    parser.add_argument("--handoff-id", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--packet", required=True)
    parser.add_argument("--empty-sheet", required=True)
    parser.add_argument("--public-validation")
    args = parser.parse_args()

    sources = {
        "protocol": args.protocol,
        "packet": args.packet,
        "empty_sheet": args.empty_sheet,
    }
    if args.public_validation:
        sources["public_validation"] = args.public_validation
    manifest = export_reviewer_handoff(
        workspace_root=args.workspace_root,
        staging_dir=args.staging_dir,
        review_kind=args.review_kind,
        handoff_id=args.handoff_id,
        created_at=datetime.fromisoformat(args.created_at.replace("Z", "+00:00")),
        sources=sources,
    )
    print(
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
