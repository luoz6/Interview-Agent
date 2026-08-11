from __future__ import annotations

from enum import Enum


class VerificationStatus(str, Enum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"


class PromotionDecision(str, Enum):
    HOLD = "HOLD"
    CONTINUE_OBSERVATION = "CONTINUE_OBSERVATION"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    READY = "READY"
