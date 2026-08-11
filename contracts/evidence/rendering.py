from __future__ import annotations

from contracts.evidence.envelope import EvidenceBundle


def render_gate_lines(bundle: EvidenceBundle) -> tuple[str, ...]:
    artifact = bundle.artifact
    lines = [
        f"VERIFICATION_STATUS={artifact.verification_status.value}",
        "PROMOTION_DECISION="
        + (
            artifact.promotion_decision.value
            if artifact.promotion_decision is not None
            else "NONE"
        ),
        f"PAYLOAD_TYPE={artifact.payload_type}",
        f"REVISION={artifact.envelope.revision}",
        f"SCOPE={artifact.envelope.scope}",
    ]
    lines.extend(f"GATE={code}" for code in sorted(artifact.gate_codes))
    return tuple(lines)
