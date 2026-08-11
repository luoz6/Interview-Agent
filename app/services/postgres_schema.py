from __future__ import annotations

import re
from typing import Literal

from app.services.postgres_connections import (
    ConnectionProvider,
    PostgresSchemaNotReady,
)
from app.services.postgres_identifiers import validate_postgres_identifier
from app.services.postgres_schema_contract import (
    LATEST_RUNTIME_MIGRATION,
    is_strict_positive_when_present_check,
    required_check_tokens_for_relation,
    required_columns_for_relation,
    required_foreign_key_tokens_for_relation,
    required_index_tokens_for_relation,
    required_nullable_columns_for_relation,
    required_strict_positive_columns_for_relation,
)


SchemaMode = Literal["migrate", "validate"]


def validate_schema_mode(mode: str) -> SchemaMode:
    if mode not in {"migrate", "validate"}:
        raise ValueError("schema_mode must be migrate or validate")
    return mode  # type: ignore[return-value]


def resolve_schema_mode(
    mode: str | None,
    *,
    provider_is_owned: bool,
) -> SchemaMode:
    if mode is None:
        if not provider_is_owned:
            raise ValueError(
                "injected connection providers require explicit schema_mode"
            )
        # Temporary DSN-only compatibility path for existing isolated tests.
        # Production composition always injects a provider and therefore can
        # never reach implicit migration behavior.
        mode = "migrate"
    return validate_schema_mode(mode)


def validate_relations(
    provider: ConnectionProvider,
    relation_names: tuple[str, ...],
) -> None:
    for name in relation_names:
        validate_postgres_identifier(name)
    with provider.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name, to_regclass('public.' || name) "
                "FROM unnest(%s::text[]) AS name",
                (list(relation_names),),
            )
            rows = cursor.fetchall()
            if len(rows) != len(relation_names) or any(
                row[1] is None for row in rows
            ):
                raise PostgresSchemaNotReady(
                    "PostgreSQL runtime schema is not ready"
                )

            required = {
                name: required_columns_for_relation(name)
                for name in relation_names
                if required_columns_for_relation(name)
            }
            if required:
                cursor.execute(
                    "SELECT table_name, column_name, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "AND table_name = ANY(%s::text[])",
                    (list(required),),
                )
                present: dict[str, set[str]] = {
                    name: set() for name in required
                }
                nullable: dict[str, set[str]] = {
                    name: set() for name in required
                }
                for row in cursor.fetchall():
                    table_name, column_name = row[:2]
                    present.setdefault(table_name, set()).add(column_name)
                    if len(row) >= 3 and str(row[2]).upper() == "YES":
                        nullable.setdefault(table_name, set()).add(column_name)
                if any(
                    not columns.issubset(present.get(name, set()))
                    for name, columns in required.items()
                ):
                    raise PostgresSchemaNotReady(
                        "PostgreSQL runtime columns are incompatible"
                    )
                nullable_requirements = {
                    name: required_nullable_columns_for_relation(name)
                    for name in required
                    if required_nullable_columns_for_relation(name)
                }
                if any(
                    not columns.issubset(nullable.get(name, set()))
                    for name, columns in nullable_requirements.items()
                ):
                    raise PostgresSchemaNotReady(
                        "PostgreSQL runtime columns are incompatible"
                    )

            index_requirements = {
                name: required_index_tokens_for_relation(name)
                for name in relation_names
                if required_index_tokens_for_relation(name)
            }
            if index_requirements:
                cursor.execute(
                    "SELECT tablename, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' "
                    "AND tablename = ANY(%s::text[])",
                    (list(index_requirements),),
                )
                definitions: dict[str, list[set[str]]] = {
                    name: [] for name in index_requirements
                }
                for table_name, index_definition in cursor.fetchall():
                    normalized = (
                        str(index_definition)
                        .lower()
                        .replace('"', "")
                        .replace("'", "")
                        .replace("::text", "")
                    )
                    tokens = {
                        token
                        for token in normalized.replace("(", " ")
                        .replace(")", " ")
                        .replace("[", " ")
                        .replace("]", " ")
                        .replace(",", " ")
                        .replace("=", " ")
                        .split()
                    }
                    definitions.setdefault(table_name, []).append(tokens)
                for table_name, requirements in index_requirements.items():
                    for required_tokens in requirements:
                        if not any(
                            required_tokens.issubset(tokens)
                            for tokens in definitions.get(table_name, [])
                        ):
                            raise PostgresSchemaNotReady(
                                "PostgreSQL runtime indexes are incompatible"
                            )

            check_requirements = {
                name: required_check_tokens_for_relation(name)
                for name in relation_names
                if required_check_tokens_for_relation(name)
            }
            strict_positive_requirements = {
                name: required_strict_positive_columns_for_relation(name)
                for name in relation_names
                if required_strict_positive_columns_for_relation(name)
            }
            checked_relations = set(check_requirements) | set(
                strict_positive_requirements
            )
            if checked_relations:
                cursor.execute(
                    "SELECT relation.relname, pg_get_constraintdef(rule.oid) "
                    "FROM pg_constraint AS rule "
                    "JOIN pg_class AS relation ON relation.oid=rule.conrelid "
                    "JOIN pg_namespace AS namespace "
                    "ON namespace.oid=relation.relnamespace "
                    "WHERE namespace.nspname='public' AND rule.contype='c' "
                    "AND relation.relname = ANY(%s::text[])",
                    (list(checked_relations),),
                )
                definitions: dict[str, list[set[str]]] = {
                    name: [] for name in checked_relations
                }
                raw_definitions: dict[str, list[str]] = {
                    name: [] for name in checked_relations
                }
                for table_name, check_definition in cursor.fetchall():
                    raw_definitions.setdefault(table_name, []).append(
                        str(check_definition)
                    )
                    normalized = (
                        str(check_definition)
                        .lower()
                        .replace('"', "")
                        .replace("'", "")
                        .replace("::text", "")
                    )
                    tokens = {
                        token
                        for token in normalized.replace("(", " ")
                        .replace(")", " ")
                        .replace("[", " ")
                        .replace("]", " ")
                        .replace(",", " ")
                        .replace("=", " ")
                        .split()
                    }
                    tokens.update(
                        re.sub(r"\s+", "", comparison)
                        for comparison in re.findall(
                            r"\b[a-z_][a-z0-9_]*\s*(?:<=|>=|<>|=|~)\s*"
                            r"[a-z_][a-z0-9_]*\b",
                            normalized,
                        )
                    )
                    tokens.update(
                        re.sub(r"\s+", "", comparison)
                        for comparison in re.findall(
                            r"\b[a-z_][a-z0-9_]*\s*~\s*\^\[[^\s]+\$",
                            normalized,
                        )
                    )
                    definitions.setdefault(table_name, []).append(tokens)
                for table_name, requirements in check_requirements.items():
                    for required_tokens in requirements:
                        if not any(
                            required_tokens.issubset(tokens)
                            for tokens in definitions.get(table_name, [])
                        ):
                            raise PostgresSchemaNotReady(
                                "PostgreSQL runtime checks are incompatible"
                            )
                for table_name, columns in (
                    strict_positive_requirements.items()
                ):
                    for column in columns:
                        if not any(
                            is_strict_positive_when_present_check(
                                definition,
                                column=column,
                            )
                            for definition in raw_definitions.get(
                                table_name,
                                [],
                            )
                        ):
                            raise PostgresSchemaNotReady(
                                "PostgreSQL runtime checks are incompatible"
                            )

            foreign_key_requirements = {
                name: required_foreign_key_tokens_for_relation(name)
                for name in relation_names
                if required_foreign_key_tokens_for_relation(name)
            }
            if foreign_key_requirements:
                cursor.execute(
                    "SELECT relation.relname, pg_get_constraintdef(rule.oid) "
                    "FROM pg_constraint AS rule "
                    "JOIN pg_class AS relation ON relation.oid=rule.conrelid "
                    "JOIN pg_namespace AS namespace "
                    "ON namespace.oid=relation.relnamespace "
                    "WHERE namespace.nspname='public' AND rule.contype='f' "
                    "AND relation.relname = ANY(%s::text[])",
                    (list(foreign_key_requirements),),
                )
                definitions: dict[str, list[set[str]]] = {
                    name: [] for name in foreign_key_requirements
                }
                for table_name, foreign_key_definition in cursor.fetchall():
                    normalized = (
                        str(foreign_key_definition)
                        .lower()
                        .replace('"', "")
                        .replace("'", "")
                        .replace("::text", "")
                    )
                    tokens = {
                        token
                        for token in normalized.replace("(", " ")
                        .replace(")", " ")
                        .replace("[", " ")
                        .replace("]", " ")
                        .replace(",", " ")
                        .replace("=", " ")
                        .split()
                    }
                    definitions.setdefault(table_name, []).append(tokens)
                for table_name, requirements in foreign_key_requirements.items():
                    for required_tokens in requirements:
                        if not any(
                            required_tokens.issubset(tokens)
                            for tokens in definitions.get(table_name, [])
                        ):
                            raise PostgresSchemaNotReady(
                                "PostgreSQL runtime foreign keys are incompatible"
                            )

            migration_tables = [
                name for name in relation_names if name.endswith("_schema_migrations")
            ]
            for migration_table in migration_tables:
                cursor.execute(
                    f'SELECT checksum, transaction_mode FROM "{migration_table}" '
                    "WHERE migration_id = %s",
                    (LATEST_RUNTIME_MIGRATION.migration_id,),
                )
                migration = cursor.fetchone()
                if migration != (
                    LATEST_RUNTIME_MIGRATION.checksum,
                    LATEST_RUNTIME_MIGRATION.transaction_mode,
                ):
                    raise PostgresSchemaNotReady(
                        "PostgreSQL runtime migration is incompatible"
                    )
