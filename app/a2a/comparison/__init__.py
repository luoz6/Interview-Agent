"""A2A-V1 local/A2A parity comparison."""

from app.a2a.comparison.comparator import (
    ComparisonResult,
    DeterministicArtifactComparator,
)
from app.a2a.comparison.dual_path import DualPathRunner, DualPathResult

__all__ = [
    "ComparisonResult",
    "DeterministicArtifactComparator",
    "DualPathRunner",
    "DualPathResult",
]
