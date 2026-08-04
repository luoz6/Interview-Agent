from __future__ import annotations

import argparse
import json
import os

from app.services.principal_memory_exclusive_scan import (
    scan_postgres_exclusive_facts,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Principal Memory exclusive-fact scan"
    )
    parser.add_argument("--table-prefix", default=os.getenv("RUNTIME_TABLE_PREFIX", "interview"))
    args = parser.parse_args(argv)
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        raise SystemExit("POSTGRES_DSN is required")
    report = scan_postgres_exclusive_facts(
        dsn=dsn,
        table_prefix=args.table_prefix,
    )
    print(json.dumps(report.as_dict(), sort_keys=True, separators=(",", ":")))
    return 2 if report.repair_required else 0


if __name__ == "__main__":
    raise SystemExit(main())
