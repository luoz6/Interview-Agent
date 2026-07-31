from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.services.session_deletion_tombstones import (
    SessionDeletionTombstone,
    validate_tombstone_integrity,
)


def load_tombstones(path: Path) -> list[SessionDeletionTombstone]:
    items: list[SessionDeletionTombstone] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                item = SessionDeletionTombstone.model_validate_json(text)
                validate_tombstone_integrity(item)
            except Exception as exc:
                raise ValueError(
                    f"invalid deletion tombstone at line {line_number}"
                ) from exc
            items.append(item)
    if not items:
        raise ValueError("deletion tombstone ledger is empty")
    return items


def replay_tombstones(
    tombstones,
    *,
    service,
    worker,
    tombstone_store,
    max_worker_steps: int = 10_000,
) -> dict[str, int]:
    replayed = 0
    already_absent = 0
    worker_steps = 0
    for tombstone in tombstones:
        validate_tombstone_integrity(tombstone)
        importer = getattr(tombstone_store, "import_tombstone", None)
        if importer is not None:
            importer(tombstone)
        try:
            service.request(tombstone.session_id)
        except ValueError:
            already_absent += 1
            tombstone_store.mark_replayed(tombstone)
            replayed += 1
            continue
        while True:
            job = service.get(tombstone.session_id)
            if job.status == "completed":
                break
            if worker_steps >= max_worker_steps:
                raise RuntimeError("deletion tombstone replay exceeded step bound")
            worker.run_once()
            worker_steps += 1
        current = tombstone_store.get_for_session(tombstone.session_id)
        tombstone_store.mark_replayed(current or tombstone)
        replayed += 1
    return {
        "validated": len(tombstones),
        "replayed": replayed,
        "already_absent": already_absent,
        "worker_steps": worker_steps,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or replay a protected session deletion ledger."
    )
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tombstones = load_tombstones(args.ledger)
    if not args.execute:
        print("mode=VALIDATE_ONLY")
        print(f"validated={len(tombstones)}")
        return 0
    if (
        os.getenv("MEMORY_TRUSTED_LOCAL_DELETION_ENABLED", "").strip().lower()
        != "true"
    ):
        raise RuntimeError("trusted local deletion gate is required")
    from app.services.runtime import (
        get_session_deletion_service,
        get_session_deletion_worker,
    )

    service = get_session_deletion_service()
    worker = get_session_deletion_worker()
    if service.tombstone_store is None:
        raise RuntimeError("session deletion tombstone store is unavailable")
    result = replay_tombstones(
        tombstones,
        service=service,
        worker=worker,
        tombstone_store=service.tombstone_store,
    )
    print("mode=EXECUTE")
    for key in ("validated", "replayed", "already_absent", "worker_steps"):
        print(f"{key}={result[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
