from __future__ import annotations

import argparse

from app.services.config import (
    get_embedding_settings,
    get_pgvector_table,
    get_postgres_dsn,
    get_runtime_table_prefix,
)
from app.services.embedding_providers import build_embedding_provider
from app.services.postgres_identifiers import derive_runtime_identifiers
from app.services.postgres_runtime_migrations import (
    RUNTIME_MIGRATION_ID,
    migrate_postgres_runtime,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate PostgreSQL runtime schema")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    prefix = get_runtime_table_prefix()
    registry = derive_runtime_identifiers(prefix)
    if not args.apply:
        print("mode=DRY_RUN")
        print(f"migration={RUNTIME_MIGRATION_ID}")
        print(f"identifier_max_bytes={registry.longest_byte_length}")
        return 0
    result = migrate_postgres_runtime(
        dsn=get_postgres_dsn(),
        table_prefix=prefix,
        pgvector_table=get_pgvector_table(),
        embedding_provider=build_embedding_provider(get_embedding_settings()),
    )
    print("mode=APPLY")
    print(f"migration={result.migration_id}")
    print(f"applied={str(result.applied).lower()}")
    print(f"identifier_max_bytes={result.runtime_identifier_max_bytes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
