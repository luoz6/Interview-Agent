from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable, Mapping

from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import validate_runtime_table_prefix
from app.services.principal_memory_contracts import (
    derive_principal_fact_taxonomy_keys,
)


SCAN_SCHEMA_VERSION = "principal-memory-exclusive-scan-v1"
SCAN_CATEGORIES = (
    "NO_CONFLICT",
    "UNAMBIGUOUS_SUPERSEDES_CHAIN",
    "AMBIGUOUS_MULTIPLE_ACTIVE",
    "INVALID_TAXONOMY_PAYLOAD",
    "CROSS_SCOPE_CHAIN",
)


@dataclass(frozen=True)
class ExclusiveFactScanCase:
    category: str
    deployment_ref: str
    scope_ref: str
    taxonomy_key: str | None
    fact_refs: tuple[str, ...]
    chain_valid: bool
    resolution_required: bool
    proposed_supersede_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "deployment_ref": self.deployment_ref,
            "scope_ref": self.scope_ref,
            "taxonomy_key": self.taxonomy_key,
            "fact_refs": list(self.fact_refs),
            "chain_valid": self.chain_valid,
            "resolution_required": self.resolution_required,
            "proposed_supersede_refs": list(self.proposed_supersede_refs),
        }


@dataclass(frozen=True)
class ExclusiveFactScanReport:
    active_fact_count: int
    cases: tuple[ExclusiveFactScanCase, ...]

    @property
    def repair_required(self) -> bool:
        return any(case.resolution_required for case in self.cases)

    def as_dict(self) -> dict[str, object]:
        counts = Counter(case.category for case in self.cases)
        return {
            "schema_version": SCAN_SCHEMA_VERSION,
            "exclusive_fact_scan": (
                "REPAIR_REQUIRED" if self.repair_required else "PASS"
            ),
            "schema_install": (
                "BLOCKED" if self.repair_required else "AUTHORIZED"
            ),
            "active_fact_count": self.active_fact_count,
            "category_counts": {
                category: counts.get(category, 0)
                for category in SCAN_CATEGORIES
            },
            "cases": [case.as_dict() for case in self.cases],
        }


def _opaque_ref(domain: str, *values: str) -> str:
    payload = "\0".join((domain, *values)).encode("utf-8")
    return sha256(payload).hexdigest()


def _field(row, name: str):
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name)


def _case(
    category: str,
    rows: Iterable[object],
    *,
    taxonomy_key: str | None,
    chain_valid: bool,
    resolution_required: bool,
    proposed_supersede_ids: Iterable[str] = (),
) -> ExclusiveFactScanCase:
    values = tuple(rows)
    first = values[0]
    deployment_id = str(_field(first, "deployment_id"))
    principal_id = str(_field(first, "principal_id"))
    fact_ids = tuple(sorted(str(_field(row, "fact_id")) for row in values))
    proposed = set(proposed_supersede_ids)
    return ExclusiveFactScanCase(
        category=category,
        deployment_ref=_opaque_ref("deployment", deployment_id),
        scope_ref=_opaque_ref("scope", deployment_id, principal_id),
        taxonomy_key=taxonomy_key,
        fact_refs=tuple(_opaque_ref("fact", fact_id) for fact_id in fact_ids),
        chain_valid=chain_valid,
        resolution_required=resolution_required,
        proposed_supersede_refs=tuple(
            _opaque_ref("fact", fact_id)
            for fact_id in sorted(proposed)
        ),
    )


def _is_single_chain(rows: tuple[object, ...]) -> tuple[bool, str | None]:
    ids = {str(_field(row, "fact_id")) for row in rows}
    predecessor_by_id = {
        str(_field(row, "fact_id")): _field(row, "supersedes_fact_id")
        for row in rows
    }
    internal_edges = {
        fact_id: str(predecessor)
        for fact_id, predecessor in predecessor_by_id.items()
        if predecessor is not None and str(predecessor) in ids
    }
    if len(internal_edges) != len(rows) - 1:
        return False, None
    if len(set(internal_edges.values())) != len(internal_edges):
        return False, None
    successors = set(internal_edges.values())
    heads = ids - successors
    if len(heads) != 1:
        return False, None
    head = next(iter(heads))
    visited: set[str] = set()
    current: str | None = head
    while current is not None and current in ids:
        if current in visited:
            return False, None
        visited.add(current)
        predecessor = predecessor_by_id[current]
        current = str(predecessor) if predecessor is not None else None
    if visited != ids:
        return False, None
    return True, head


def scan_exclusive_facts(rows: Iterable[object]) -> ExclusiveFactScanReport:
    values = tuple(rows)
    by_id = {str(_field(row, "fact_id")): row for row in values}
    active = tuple(row for row in values if _field(row, "status") == "active")
    parsed: dict[str, tuple[str, str | None]] = {}
    cases: list[ExclusiveFactScanCase] = []
    invalid_ids: set[str] = set()

    for row in active:
        fact_id = str(_field(row, "fact_id"))
        try:
            parsed[fact_id] = derive_principal_fact_taxonomy_keys(
                fact_type=str(_field(row, "fact_type")),
                normalized_fact=str(_field(row, "normalized_fact")),
            )
        except (TypeError, ValueError):
            invalid_ids.add(fact_id)
            cases.append(
                _case(
                    "INVALID_TAXONOMY_PAYLOAD",
                    (row,),
                    taxonomy_key=None,
                    chain_valid=False,
                    resolution_required=True,
                )
            )

    cross_scope_ids: set[str] = set()
    broken_chain_ids: set[str] = set()
    reported_invalid_ids = set(invalid_ids)
    for row in active:
        fact_id = str(_field(row, "fact_id"))
        if fact_id in invalid_ids:
            continue
        predecessor_id = _field(row, "supersedes_fact_id")
        if predecessor_id is None:
            continue
        predecessor = by_id.get(str(predecessor_id))
        if predecessor is None:
            broken_chain_ids.add(fact_id)
            cases.append(
                _case(
                    "AMBIGUOUS_MULTIPLE_ACTIVE",
                    (row,),
                    taxonomy_key=parsed[fact_id][0],
                    chain_valid=False,
                    resolution_required=True,
                )
            )
            continue
        try:
            predecessor_keys = derive_principal_fact_taxonomy_keys(
                fact_type=str(_field(predecessor, "fact_type")),
                normalized_fact=str(_field(predecessor, "normalized_fact")),
            )
        except (TypeError, ValueError):
            broken_chain_ids.add(fact_id)
            cases.append(
                _case(
                    "AMBIGUOUS_MULTIPLE_ACTIVE",
                    (row,),
                    taxonomy_key=parsed[fact_id][0],
                    chain_valid=False,
                    resolution_required=True,
                )
            )
            predecessor_fact_id = str(_field(predecessor, "fact_id"))
            if predecessor_fact_id not in reported_invalid_ids:
                reported_invalid_ids.add(predecessor_fact_id)
                cases.append(
                    _case(
                        "INVALID_TAXONOMY_PAYLOAD",
                        (predecessor,),
                        taxonomy_key=None,
                        chain_valid=False,
                        resolution_required=True,
                    )
                )
            continue
        current_scope = (
            _field(row, "deployment_id"),
            _field(row, "principal_id"),
            parsed[fact_id][0],
        )
        predecessor_scope = (
            _field(predecessor, "deployment_id"),
            _field(predecessor, "principal_id"),
            predecessor_keys[0],
        )
        if current_scope != predecessor_scope:
            cross_scope_ids.add(fact_id)
            cases.append(
                _case(
                    "CROSS_SCOPE_CHAIN",
                    (row,),
                    taxonomy_key=parsed[fact_id][0],
                    chain_valid=False,
                    resolution_required=True,
                )
            )

    groups: dict[tuple[str, str, str], list[object]] = defaultdict(list)
    for row in active:
        fact_id = str(_field(row, "fact_id"))
        if (
            fact_id in invalid_ids
            or fact_id in cross_scope_ids
            or fact_id in broken_chain_ids
        ):
            continue
        taxonomy_key, exclusive_scope_key = parsed[fact_id]
        if exclusive_scope_key is None:
            continue
        groups[
            (
                str(_field(row, "deployment_id")),
                str(_field(row, "principal_id")),
                taxonomy_key,
            )
        ].append(row)

    for (_, _, taxonomy_key), group in sorted(groups.items()):
        grouped_rows = tuple(group)
        if len(grouped_rows) == 1 and _field(
            grouped_rows[0], "supersedes_fact_id"
        ) != _field(grouped_rows[0], "fact_id"):
            cases.append(
                _case(
                    "NO_CONFLICT",
                    grouped_rows,
                    taxonomy_key=taxonomy_key,
                    chain_valid=True,
                    resolution_required=False,
                )
            )
            continue
        chain_valid, head = _is_single_chain(grouped_rows)
        if chain_valid and head is not None:
            cases.append(
                _case(
                    "UNAMBIGUOUS_SUPERSEDES_CHAIN",
                    grouped_rows,
                    taxonomy_key=taxonomy_key,
                    chain_valid=True,
                    resolution_required=True,
                    proposed_supersede_ids=(
                        str(_field(row, "fact_id"))
                        for row in grouped_rows
                        if str(_field(row, "fact_id")) != head
                    ),
                )
            )
        else:
            cases.append(
                _case(
                    "AMBIGUOUS_MULTIPLE_ACTIVE",
                    grouped_rows,
                    taxonomy_key=taxonomy_key,
                    chain_valid=False,
                    resolution_required=True,
                )
            )

    return ExclusiveFactScanReport(
        active_fact_count=len(active),
        cases=tuple(
            sorted(
                cases,
                key=lambda item: (
                    item.category,
                    item.deployment_ref,
                    item.scope_ref,
                    item.taxonomy_key or "",
                    item.fact_refs,
                ),
            )
        ),
    )


def scan_postgres_exclusive_facts(
    *,
    table_prefix: str,
    dsn: str | None = None,
    connection_provider: ConnectionProvider | None = None,
) -> ExclusiveFactScanReport:
    validate_runtime_table_prefix(table_prefix)
    if connection_provider is None:
        if not dsn:
            raise ValueError("dsn or connection_provider is required")
        connection_provider = DirectPsycopg2ConnectionProvider(dsn)
    table = f"{table_prefix}_principal_memory_facts"
    from psycopg2 import sql

    with connection_provider.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT to_regclass(%s)", (f"public.{table}",))
            if cursor.fetchone()[0] is None:
                return scan_exclusive_facts(())
            cursor.execute(
                sql.SQL(
                    "SELECT fact_id,deployment_id,principal_id,fact_type,"
                    "normalized_fact,status,supersedes_fact_id FROM {}"
                ).format(sql.Identifier(table))
            )
            names = (
                "fact_id",
                "deployment_id",
                "principal_id",
                "fact_type",
                "normalized_fact",
                "status",
                "supersedes_fact_id",
            )
            rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    return scan_exclusive_facts(rows)
