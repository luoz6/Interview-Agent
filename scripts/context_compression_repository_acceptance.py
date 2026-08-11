from __future__ import annotations

import argparse
from pathlib import Path
import sys

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from scripts import memory_system_optimization_acceptance as historical


ROOT = historical.ROOT
FOCUSED_TESTS = historical.ADAPTIVE_FOCUSED_TESTS
REVIEWED_TEST_EXEMPTIONS = historical.REVIEWED_TEST_EXEMPTIONS
SCENARIO_EVIDENCE = historical.SCENARIO_EVIDENCE
sanitized_test_environment = historical.sanitized_test_environment
verify_acceptance_manifest = historical.verify_acceptance_manifest


def run_repository_gates(*, python: str) -> None:
    historical.run_repository_gates(
        python=python,
        focused_tests=FOCUSED_TESTS,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args(argv)

    historical.verify_traceability()
    historical.verify_adaptive_context_traceability()
    historical.verify_acceptance_manifest(
        focused_tests=FOCUSED_TESTS,
        exemptions=REVIEWED_TEST_EXEMPTIONS,
        scenario_evidence=SCENARIO_EVIDENCE,
    )
    historical.verify_safe_defaults()
    historical.audit_artifact_paths(
        [ROOT / "reports" / "context-compression-repository-acceptance"]
    )
    if not args.skip_tests:
        run_repository_gates(python=sys.executable)
    print("READY_FOR_SHADOW")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
