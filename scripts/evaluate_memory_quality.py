from __future__ import annotations

import argparse
import json

from app.services.memory_quality_dataset import load_memory_quality_dataset
from app.services.memory_quality_eval import evaluate_memory_quality


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate synthetic memory quality")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--deterministic", action="store_true")
    mode.add_argument("--real-provider", action="store_true")
    args = parser.parse_args(argv)
    if args.real_provider:
        parser.error(
            "real-provider evaluation requires separate provider, dataset, budget, "
            "and redacted-output authorization"
        )
    result = evaluate_memory_quality(load_memory_quality_dataset())
    safe = {key: value for key, value in result.items() if key != "cases"}
    print(json.dumps(safe, ensure_ascii=False, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
