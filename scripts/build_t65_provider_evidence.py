from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if not __package__:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from app.services.t65_provider_evidence import (
    build_performance_observability,
    build_t65_usage_cost_ledger,
)
from app.services.independent_review_handoff import DetachedSignatureEvidence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build privacy-safe T65 Provider usage and observability evidence"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    usage = commands.add_parser("usage-ledger")
    usage.add_argument("--manifest", type=Path, action="append", required=True)
    usage.add_argument(
        "--receipt",
        action="append",
        default=[],
        metavar="DIMENSION=PATH",
        help="privacy-safe sealed attempt receipt explicitly mapped to a source dimension",
    )
    usage.add_argument(
        "--attempt-ledger",
        action="append",
        default=[],
        metavar="DIMENSION=PATH",
        help="sealed raw attempt ledger explicitly mapped to a source dimension",
    )
    usage.add_argument("--candidate-revision", required=True)
    usage.add_argument("--candidate-tree", required=True)
    usage.add_argument("--authorization-sha256", required=True)
    usage.add_argument("--authorization-id", required=True)
    usage.add_argument("--provider", required=True)
    usage.add_argument("--model", required=True)
    usage.add_argument(
        "--execution-manifest",
        type=Path,
        required=True,
        help=(
            "externally frozen interview-quality-v1-t65-control-manifest-v1; "
            "the repository v0.2.x plan execution manifest is not this trust artifact"
        ),
    )
    usage.add_argument("--execution-signature", type=Path)
    usage.add_argument("--execution-public-key", type=Path)
    usage.add_argument("--execution-authority-id")
    usage.add_argument("--candidate-repository", type=Path)
    usage.add_argument("--out", type=Path, required=True)

    observability = commands.add_parser("observability")
    observability.add_argument("--source", type=Path, action="append", required=True)
    observability.add_argument("--usage-ledger", type=Path, required=True)
    observability.add_argument("--candidate-revision", required=True)
    observability.add_argument("--candidate-tree", required=True)
    observability.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "usage-ledger":
            execution_signature = (
                DetachedSignatureEvidence.model_validate(
                    json.loads(args.execution_signature.read_text(encoding="utf-8"))
                )
                if args.execution_signature is not None
                else None
            )
            execution_public_key_pem = (
                args.execution_public_key.read_bytes()
                if args.execution_public_key is not None
                else None
            )
            result = build_t65_usage_cost_ledger(
                manifest_paths=args.manifest,
                expected_revision=args.candidate_revision,
                expected_tree=args.candidate_tree,
                authorization_sha256=args.authorization_sha256,
                expected_authorization_id=args.authorization_id,
                expected_provider=args.provider,
                expected_model=args.model,
                execution_manifest_path=args.execution_manifest,
                receipt_paths_by_dimension=_parse_dimension_paths(
                    args.receipt, label="receipt"
                ),
                ledger_paths_by_dimension=_parse_dimension_paths(
                    args.attempt_ledger, label="attempt ledger"
                ),
                execution_signature=execution_signature,
                execution_public_key_pem=execution_public_key_pem,
                execution_authority_id=args.execution_authority_id,
                candidate_repository=args.candidate_repository,
            )
            exit_code = 0 if result.quality_status == "PASS" else 2
        else:
            result = build_performance_observability(
                source_paths=args.source,
                usage_ledger_path=args.usage_ledger,
                expected_revision=args.candidate_revision,
                expected_tree=args.candidate_tree,
            )
            # This builder represents the truthful B-layer fallback. It can prove
            # observability is blocked, but it cannot manufacture a C1 runtime PASS.
            exit_code = 2
        _write_exclusive_json(args.out, result.model_dump(mode="json"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": "t65-provider-evidence-cli-error-v1",
                    "status": "BLOCKED",
                    "error_code": "SOURCE_CAPTURE_INCOMPLETE",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 2
    print(result.model_dump_json())
    return exit_code


def _write_exclusive_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def _parse_dimension_paths(values: list[str], *, label: str) -> dict[str, Path]:
    allowed = {"initial_question", "followup", "report_scoring"}
    result: dict[str, Path] = {}
    for value in values:
        dimension, separator, raw_path = value.partition("=")
        if (
            not separator
            or dimension not in allowed
            or not raw_path
            or dimension in result
        ):
            raise ValueError(f"{label} mapping must be one unique DIMENSION=PATH")
        result[dimension] = Path(raw_path)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
