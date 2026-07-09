from dataclasses import dataclass


@dataclass(frozen=True)
class SessionVersionConflict(Exception):
    expected_version: int
    actual_version: int

    def __str__(self) -> str:
        return (
            "session version conflict: "
            f"expected {self.expected_version}, actual {self.actual_version}"
        )
