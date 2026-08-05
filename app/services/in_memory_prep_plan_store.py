from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable, Iterator

from app.services.prep import InterviewPlan
from app.services.prep_plans import (
    PrepPlanError,
    apply_plan_operations,
    build_regenerated_state,
    build_prep_plan_record,
    plan_expired,
    plan_not_found,
    public_from_record,
    regeneration_context_from_record,
    version_snapshot,
)


class InMemoryPrepPlanStore:
    durability = "memory"

    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(hours=24),
        expired_grace: timedelta = timedelta(hours=24),
        consumed_retention: timedelta = timedelta(days=7),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl.total_seconds() <= 0:
            raise ValueError("prep plan ttl must be positive")
        if expired_grace.total_seconds() <= 0:
            raise ValueError("prep plan expired grace must be positive")
        if consumed_retention.total_seconds() <= 0:
            raise ValueError("prep plan consumed retention must be positive")
        self._ttl = ttl
        self._expired_grace = expired_grace
        self._consumed_retention = consumed_retention
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._records: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, RLock] = {}
        self._registry_lock = RLock()

    def create(
        self,
        *,
        plan: InterviewPlan,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
        source_draft_id: str | None = None,
        practice_provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = self._aware(self._clock())
        record = build_prep_plan_record(
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
            durability=self.durability,
            created_at=now,
            expires_at=now + self._ttl,
            source_draft_id=source_draft_id,
            practice_provenance=practice_provenance,
        )
        plan_id = record["public"]["plan_id"]
        with self._registry_lock:
            self._records[plan_id] = record
            self._locks[plan_id] = RLock()
        return public_from_record(record)

    def get(self, plan_id: str) -> dict[str, Any]:
        self.cleanup()
        with self._lock_for(plan_id):
            record = self._record(plan_id)
            self._assert_available(record, plan_id)
            return public_from_record(record)

    def apply_operations(
        self,
        plan_id: str,
        *,
        expected_version: int,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self.transaction(plan_id) as record:
            self._assert_editable(record)
            next_public = apply_plan_operations(
                record["public"],
                expected_version=expected_version,
                operations=operations,
            )
            record["public"] = next_public
            record["updated_at"] = self._aware(self._clock()).isoformat()
            record["versions"][next_public["plan_version"]] = version_snapshot(
                next_public,
                change_type="patched",
            )
            return public_from_record(record)

    def get_regeneration_context(
        self,
        plan_id: str,
        *,
        question_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        with self.transaction(plan_id) as record:
            self._assert_editable(record)
            return regeneration_context_from_record(
                record,
                expected_version=expected_version,
                question_id=question_id,
            )

    def replace_question(
        self,
        plan_id: str,
        *,
        question_id: str,
        expected_version: int,
        replacement: dict[str, Any],
    ) -> dict[str, Any]:
        with self.transaction(plan_id) as record:
            self._assert_editable(record)
            next_public, contexts, catalog = build_regenerated_state(
                record,
                expected_version=expected_version,
                replaced_question_id=question_id,
                replacement=replacement,
            )
            replacement_id = replacement["public_question"]["question_id"]
            record["public"] = next_public
            record["question_contexts"] = contexts
            record["context_catalog"] = catalog
            record["updated_at"] = self._aware(self._clock()).isoformat()
            record["versions"][next_public["plan_version"]] = version_snapshot(
                next_public,
                change_type="regenerated",
                replaced_question_id=question_id,
                replacement_question_id=replacement_id,
            )
            response = public_from_record(record)
            response["replaced_question_id"] = question_id
            response["replacement_question_id"] = replacement_id
            return response

    @contextmanager
    def transaction(self, plan_id: str) -> Iterator[dict[str, Any]]:
        self.cleanup()
        lock = self._lock_for(plan_id)
        with lock:
            record = self._record(plan_id)
            self._assert_available(record, plan_id)
            before = deepcopy(record)
            try:
                yield record
            except BaseException:
                self._records[plan_id] = before
                raise

    def delete_by_source_draft(self, draft_id: str) -> int:
        deleted = 0
        with self._registry_lock:
            for plan_id in list(self._records):
                lock = self._locks[plan_id]
                with lock:
                    record = self._records.get(plan_id)
                    if (
                        record is not None
                        and record.get("source_draft_id") == draft_id
                        and record.get("state") != "consumed"
                    ):
                        self._records.pop(plan_id, None)
                        self._locks.pop(plan_id, None)
                        deleted += 1
        return deleted

    def cleanup(self) -> int:
        now = self._aware(self._clock())
        removed = 0
        with self._registry_lock:
            for plan_id in list(self._records):
                lock = self._locks[plan_id]
                with lock:
                    record = self._records.get(plan_id)
                    if record is None:
                        continue
                    expires_at = self._aware(datetime.fromisoformat(record["expires_at"]))
                    if record["state"] == "editable" and expires_at <= now:
                        record["state"] = "expired"
                        record["public"]["state"] = "expired"
                    expired_removal_at = expires_at + self._expired_grace
                    updated_at = self._aware(datetime.fromisoformat(record["updated_at"]))
                    consumed_removal_at = updated_at + self._consumed_retention
                    should_remove = (
                        record["state"] == "expired" and expired_removal_at <= now
                    ) or (
                        record["state"] == "consumed" and consumed_removal_at <= now
                    )
                    if should_remove:
                        self._records.pop(plan_id, None)
                        self._locks.pop(plan_id, None)
                        removed += 1
        return removed

    def version_count(self, plan_id: str) -> int:
        with self._lock_for(plan_id):
            return len(self._record(plan_id)["versions"])

    def clear(self) -> None:
        with self._registry_lock:
            self._records.clear()
            self._locks.clear()

    def _lock_for(self, plan_id: str) -> RLock:
        with self._registry_lock:
            if plan_id not in self._records:
                raise plan_not_found(plan_id)
            return self._locks[plan_id]

    def _record(self, plan_id: str) -> dict[str, Any]:
        try:
            return self._records[plan_id]
        except KeyError as exc:
            raise plan_not_found(plan_id) from exc

    def _assert_available(self, record: dict[str, Any], plan_id: str) -> None:
        if record["state"] == "expired" or self._aware(datetime.fromisoformat(record["expires_at"])) <= self._aware(self._clock()):
            record["state"] = "expired"
            record["public"]["state"] = "expired"
            raise plan_expired(plan_id)

    @staticmethod
    def _assert_editable(record: dict[str, Any]) -> None:
        if record["state"] == "consumed":
            raise PrepPlanError(
                "PREP_PLAN_ALREADY_CONSUMED",
                "计划已用于创建面试，不能继续编辑。",
                status_code=409,
                details={"session_id": record.get("consumed_session_id")},
            )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
