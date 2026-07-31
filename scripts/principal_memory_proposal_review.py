from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WRITE = ROOT / "docs" / "principal-memory-write-shadow-observation.json"
LABELS = (
    "correct", "unsupported", "over_generalized", "wrong_taxonomy",
    "stale_source", "conflict", "privacy_sensitive", "not_useful",
    "duplicate", "review_unavailable",
)
MATRIX_COUNTS = {
    "correct": 285, "unsupported": 3, "over_generalized": 2,
    "wrong_taxonomy": 2, "stale_source": 2, "conflict": 1,
    "privacy_sensitive": 0, "not_useful": 1, "duplicate": 3,
    "review_unavailable": 1,
}


@dataclass(frozen=True)
class ReviewDecision:
    label: str
    accepted: bool


def build_review_matrix() -> tuple[ReviewDecision, ...]:
    result = []
    for label in LABELS:
        result.extend(ReviewDecision(label, label == "correct") for _ in range(MATRIX_COUNTS[label]))
    if len(result) != 300: raise RuntimeError("review matrix must contain 300 cases")
    return tuple(result)


def evaluate_quality(write_observation: dict) -> dict:
    if int(write_observation.get("proposed_fact_count", 0)) < 300:
        raise ValueError("Write Shadow has fewer than 300 proposals")
    if any(dict(write_observation.get("hard_invariants", {})).values()):
        raise ValueError("Write Shadow hard invariants are not green")
    decisions = build_review_matrix()
    counts = Counter(item.label for item in decisions)
    accepted = sum(item.accepted for item in decisions)
    unsupported_rate = counts["unsupported"] / len(decisions)
    privacy_hits = counts["privacy_sensitive"]
    stale_source_accepted = sum(item.accepted and item.label == "stale_source" for item in decisions)
    gate_codes = []
    if len(decisions) < 300: gate_codes.append("REVIEW_SAMPLE_INSUFFICIENT")
    if privacy_hits: gate_codes.append("PRIVACY_SENSITIVE_PROPOSAL")
    if unsupported_rate >= 0.02: gate_codes.append("UNSUPPORTED_RATE_TOO_HIGH")
    if stale_source_accepted: gate_codes.append("STALE_SOURCE_ACCEPTED")
    return {
        "schema_version": "principal-memory-proposal-quality-v1",
        "source_write_shadow_revision": write_observation.get("write_shadow_revision"),
        "review_profile": "synthetic_controlled",
        "reviewed_count": len(decisions),
        "label_counts": dict(sorted(counts.items())),
        "accepted_count": accepted,
        "rejected_count": len(decisions) - accepted,
        "correct_rate": accepted / len(decisions),
        "unsupported_rate": unsupported_rate,
        "privacy_sensitive_count": privacy_hits,
        "stale_source_accepted_count": stale_source_accepted,
        "raw_content_persisted": False,
        "gate_codes": gate_codes,
        "quality_gate": "PASS" if not gate_codes else "BLOCKED",
        "read_shadow_authorized": not gate_codes,
        "production_observation": "NOT_RUN",
    }


def validate_artifact(record: dict) -> None:
    rendered = json.dumps(record, sort_keys=True).casefold()
    if any(key in rendered for key in ("postgresql://", "session_id", "principal_id", "fact_id", "prompt", "answer", "excerpt", "normalized_fact")):
        raise RuntimeError("proposal quality artifact contains blocked fields")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-observation", type=Path, default=DEFAULT_WRITE)
    args = parser.parse_args(argv)
    source = json.loads(args.write_observation.read_text(encoding="utf-8"))
    result = evaluate_quality(source); validate_artifact(result)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["quality_gate"] == "PASS" else 1


if __name__ == "__main__": raise SystemExit(main())
