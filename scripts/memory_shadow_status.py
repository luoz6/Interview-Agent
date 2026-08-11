from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Mapping

from app.services.memory_shadow_observability import (
    MemoryShadowObservabilityService,
    validate_status_artifact,
)
from contracts.evidence import (
    EvidenceRegistry,
    EvidenceVerifier,
    ProposalReviewEvidencePayload,
    ShadowEvidencePayload,
)
from contracts.policies import ProposalReviewEvidencePolicy, ShadowEvidencePolicy
from scripts.memory_shadow_evidence_support import verify_policy_bound_evidence
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    load_receipt_signer,
    require_environment_value,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = {
    "budget": ROOT / "reports" / "memory" / "budget-shadow-evidence-v1.json",
    "write": ROOT / "reports" / "memory" / "write-shadow-evidence-v1.json",
    "quality": ROOT / "reports" / "memory" / "proposal-review-evidence-v1.json",
    "lifecycle": ROOT / "reports" / "memory" / "lifecycle-shadow-evidence-v1.json",
    "read": ROOT / "reports" / "memory" / "read-shadow-evidence-v1.json",
}
SCOPES = {
    "budget": "memory.budget-shadow.controlled",
    "write": "memory.write-shadow.controlled",
    "quality": "memory.proposal-review.controlled",
    "lifecycle": "memory.lifecycle-shadow.controlled",
    "read": "memory.read-shadow.controlled",
}


def _metric_int(payload: ShadowEvidencePayload, name: str) -> int:
    if name not in payload.metrics:
        raise ValueError(f"required protected metric is missing: {name}")
    value = payload.metrics[name]
    if value < 0 or not value.is_integer():
        raise ValueError(f"protected metric must be a non-negative integer: {name}")
    return int(value)


def _metric_float(payload: ShadowEvidencePayload, name: str) -> float:
    if name not in payload.metrics:
        raise ValueError(f"required protected metric is missing: {name}")
    return float(payload.metrics[name])


def _prefixed_metrics(
    payload: ShadowEvidencePayload,
    prefix: str,
) -> dict[str, int]:
    return {
        name.removeprefix(prefix): _metric_int(payload, name)
        for name in sorted(payload.metrics)
        if name.startswith(prefix)
    }


def _budget_record(payload: ShadowEvidencePayload) -> dict[str, object]:
    return {
        "followup_sample_count": payload.sample_count,
        "language_sample_counts": _prefixed_metrics(
            payload,
            "language_sample_count_",
        ),
        "estimator_error_direction": _prefixed_metrics(
            payload,
            "estimator_error_direction_",
        ),
        "known_over_budget_provider_calls": _metric_int(
            payload,
            "known_over_budget_provider_calls",
        ),
        "mandatory_current_content_losses": _metric_int(
            payload,
            "mandatory_current_content_losses",
        ),
        "would_select_count": _metric_int(payload, "would_select_count"),
        "would_drop_count": _metric_int(payload, "would_drop_count"),
        "fallback_count": _metric_int(payload, "fallback_count"),
        "baseline_error_rate": _metric_float(payload, "baseline_error_rate"),
        "followup_error_rate": _metric_float(payload, "followup_error_rate"),
        "baseline_p95_latency_ms": _metric_float(
            payload,
            "baseline_p95_latency_ms",
        ),
        "followup_p95_latency_ms": _metric_float(
            payload,
            "followup_p95_latency_ms",
        ),
        "data_complete": "BUDGET_SHADOW_DATA_INCOMPLETE" not in payload.violations,
        "unavailable_bucket_count": _metric_int(
            payload,
            "unavailable_bucket_count",
        ),
        "privacy_audit_hits": _metric_int(payload, "privacy_audit_hits"),
        "budget_config_conflict": "BUDGET_SHADOW_CONFIG_CONFLICT"
        in payload.violations,
        "cleanup_residue": _metric_int(payload, "cleanup_residue"),
    }


def _write_record(payload: ShadowEvidencePayload) -> dict[str, object]:
    return {
        "sample_count": payload.sample_count,
        "proposal_created_count": _metric_int(
            payload,
            "proposal_created_count",
        ),
        "deduplicated_replay_count": _metric_int(
            payload,
            "deduplicated_replay_count",
        ),
        "fault_matrix": _prefixed_metrics(payload, "fault_"),
        "hard_invariants": _prefixed_metrics(payload, "hard_"),
        "cleanup_residue": _metric_int(payload, "cleanup_residue"),
    }


def _quality_record(payload: ProposalReviewEvidencePayload) -> dict[str, object]:
    return {
        "reviewed_count": payload.review_case_count,
        "label_counts": payload.label_counts.model_dump(mode="json"),
        "privacy_sensitive_count": payload.label_counts.privacy_sensitive,
        "stale_source_accepted_count": payload.stale_source_accepted_count,
        "quality_gate": "PASS",
    }


def _lifecycle_record(payload: ShadowEvidencePayload) -> dict[str, object]:
    return {
        "confirmed_count": _metric_int(payload, "confirmed_count"),
        "superseded_count": _metric_int(payload, "superseded_count"),
        "rejected_count": _metric_int(payload, "rejected_count"),
        "selected_after_revoke": _metric_int(
            payload,
            "selected_after_revoke",
        ),
        "fact_residue": _metric_int(payload, "fact_residue"),
        "consent_residue": _metric_int(payload, "consent_residue"),
        "cleanup_residue": _metric_int(payload, "cleanup_residue"),
        "race_matrix": _prefixed_metrics(payload, "race_"),
    }


def _read_record(payload: ShadowEvidencePayload) -> dict[str, object]:
    return {
        "sample_count": payload.sample_count,
        "scenario_counts": _prefixed_metrics(payload, "scenario_"),
        "source_fact_count": _metric_int(payload, "source_fact_count"),
        "would_select_count": _metric_int(payload, "would_select_count"),
        "conflict_count": _metric_int(payload, "conflict_count"),
        "hard_invariants": _prefixed_metrics(payload, "hard_"),
        "read_shadow_p95_latency_ms": _metric_float(
            payload,
            "read_shadow_p95_latency_ms",
        ),
        "baseline_p95_latency_ms": _metric_float(
            payload,
            "baseline_p95_latency_ms",
        ),
        "latency_regression_ratio": _metric_float(
            payload,
            "latency_regression_ratio",
        ),
        "provider_calls": _metric_int(payload, "provider_calls"),
        "cleanup_residue": _metric_int(payload, "cleanup_residue"),
    }


class FileMemoryShadowEvidenceSource:
    """Verify protected inputs and expose only their aggregate projection."""

    def __init__(
        self,
        paths: Mapping[str, Path],
        *,
        input_revision: str,
        proposal_review_revision: str,
        environ: Mapping[str, str],
    ) -> None:
        self.paths = dict(paths)
        self.input_revision = input_revision
        self.proposal_review_revision = proposal_review_revision
        self.environ = environ

    def load(self) -> dict[str, Mapping[str, object]]:
        signer = load_receipt_signer(self.environ)
        verifier = EvidenceVerifier(
            registry=EvidenceRegistry.default(),
            receipt_signer=signer,
        )

        def shadow(stage: str, minimum_samples: int):
            return verify_policy_bound_evidence(
                path=self.paths[stage],
                revision=self.input_revision,
                scope=SCOPES[stage],
                payload_type=ShadowEvidencePayload,
                evaluate_policy=lambda payload: ShadowEvidencePolicy(
                    minimum_samples=minimum_samples
                ).evaluate(payload, production_scope=False),
                verifier=verifier,
            ).payload

        budget = shadow("budget", 300)
        write = shadow("write", 300)
        lifecycle = shadow("lifecycle", 5)
        read = shadow("read", 300)
        quality = verify_policy_bound_evidence(
            path=self.paths["quality"],
            revision=self.proposal_review_revision,
            scope=SCOPES["quality"],
            payload_type=ProposalReviewEvidencePayload,
            evaluate_policy=ProposalReviewEvidencePolicy().evaluate,
            verifier=verifier,
        ).payload
        return {
            "budget": _budget_record(budget),
            "write": _write_record(write),
            "quality": _quality_record(quality),
            "lifecycle": _lifecycle_record(lifecycle),
            "read": _read_record(read),
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a read-only aggregate Memory Shadow status."
    )
    parser.add_argument("--status-only", action="store_true", required=True)
    parser.add_argument("--input-revision")
    parser.add_argument("--proposal-review-revision")
    parser.add_argument("--budget", type=Path, default=DEFAULT_PATHS["budget"])
    parser.add_argument("--write", type=Path, default=DEFAULT_PATHS["write"])
    parser.add_argument("--quality", type=Path, default=DEFAULT_PATHS["quality"])
    parser.add_argument(
        "--lifecycle", type=Path, default=DEFAULT_PATHS["lifecycle"]
    )
    parser.add_argument("--read", type=Path, default=DEFAULT_PATHS["read"])
    parser.add_argument("--output", type=Path)
    return parser


def format_status_input_blocked_output() -> tuple[str, ...]:
    return (
        "MEMORY_SHADOW_STATUS=BLOCKED",
        "GATE=STATUS_INPUT_EVIDENCE_UNVERIFIED",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        input_revision = args.input_revision or require_environment_value(
            os.environ,
            "EVIDENCE_REVISION",
        )
        proposal_review_revision = args.proposal_review_revision or input_revision
        source = FileMemoryShadowEvidenceSource(
            {
                "budget": args.budget,
                "write": args.write,
                "quality": args.quality,
                "lifecycle": args.lifecycle,
                "read": args.read,
            },
            input_revision=input_revision,
            proposal_review_revision=proposal_review_revision,
            environ=os.environ,
        )
        result = MemoryShadowObservabilityService().build_status(source.load())
        validate_status_artifact(result)
    except (
        AcceptanceConfigurationError,
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
    ):
        print("\n".join(format_status_input_blocked_output()))
        return 1
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 1 if result["automatic_stop"]["triggered"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
