from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

from app.services.memory_shadow_observability import (
    MemoryShadowObservabilityService,
    validate_status_artifact,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = {
    "budget": ROOT / "docs" / "memory-budget-shadow-observation.json",
    "write": ROOT / "docs" / "principal-memory-write-shadow-observation.json",
    "quality": ROOT / "docs" / "principal-memory-proposal-quality.json",
    "lifecycle": ROOT / "docs" / "principal-memory-lifecycle-drill-evidence.json",
    "read": ROOT / "docs" / "principal-memory-read-shadow-observation.json",
}


class FileMemoryShadowEvidenceSource:
    def __init__(self, paths: Mapping[str, Path]) -> None:
        self.paths = dict(paths)

    def load(self) -> dict[str, Mapping[str, object]]:
        loaded: dict[str, Mapping[str, object]] = {}
        for stage, path in self.paths.items():
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, Mapping):
                raise ValueError(f"aggregate evidence for {stage} must be an object")
            loaded[stage] = value
        return loaded


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a read-only aggregate Memory Shadow status."
    )
    parser.add_argument("--status-only", action="store_true", required=True)
    parser.add_argument("--budget", type=Path, default=DEFAULT_PATHS["budget"])
    parser.add_argument("--write", type=Path, default=DEFAULT_PATHS["write"])
    parser.add_argument("--quality", type=Path, default=DEFAULT_PATHS["quality"])
    parser.add_argument(
        "--lifecycle", type=Path, default=DEFAULT_PATHS["lifecycle"]
    )
    parser.add_argument("--read", type=Path, default=DEFAULT_PATHS["read"])
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = FileMemoryShadowEvidenceSource(
        {
            "budget": args.budget,
            "write": args.write,
            "quality": args.quality,
            "lifecycle": args.lifecycle,
            "read": args.read,
        }
    )
    result = MemoryShadowObservabilityService().build_status(source.load())
    validate_status_artifact(result)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 1 if result["automatic_stop"]["triggered"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
