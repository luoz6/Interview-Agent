from __future__ import annotations


class UnsupportedRowSchemaVersionError(ValueError):
    def __init__(self, *, row_type: str, version: object) -> None:
        super().__init__(
            f"unsupported {row_type} row schema version: {version!r}"
        )
        self.row_type = row_type
        self.version = version


def require_supported_row_version(
    value: object,
    *,
    row_type: str,
    current_version: str,
) -> str:
    """Validate a stored version while accepting pre-version legacy rows.

    Database migrations backfill NULL values before making the column required.
    Accepting a missing value here keeps read compatibility for legacy fixtures and
    exported rows that predate the physical column; any explicit unknown version
    fails closed.
    """

    if value is None:
        return current_version
    if value != current_version:
        raise UnsupportedRowSchemaVersionError(
            row_type=row_type,
            version=value,
        )
    return current_version
