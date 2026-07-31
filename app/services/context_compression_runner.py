from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Event, Lock, Thread
from typing import Any, Callable, Literal, Protocol, Sequence

from app.ports.context_artifacts import ContextArtifactStore
from app.services.context_artifacts import (
    ArtifactPurpose,
    ArtifactPayload,
    CompressionSourceSegment,
    ContextArtifactClaim,
    ContextArtifactConflict,
    ContextArtifactIdentity,
    ContextArtifactIdentityMaterial,
    ContextArtifactLeaseLost,
    ContextArtifactMissing,
    ContextArtifactProviderFailed,
    ContextArtifactRecord,
    ContextArtifactRef,
    ContextCompressionPolicy,
    OwnerType,
)
from app.services.context_compression_validation import (
    CompressionValidationStats,
    ValidatedCompressionArtifact,
    validate_compression_artifact,
)
from app.services.token_estimation import TokenEstimator


class ContextCompressionParentOwnership(Protocol):
    def ensure_owned(self) -> None:
        """Synchronously verify authoritative parent workflow ownership."""


@dataclass(frozen=True)
class ContextCompressionResolution:
    route: Literal["artifact_reused", "artifact_created"]
    ref: ContextArtifactRef
    record: ContextArtifactRecord
    payload: ArtifactPayload
    stats: CompressionValidationStats


class ContextArtifactHeartbeat:
    """Renew and synchronously prove one fenced Artifact claim."""

    def __init__(
        self,
        store: ContextArtifactStore,
        claim: ContextArtifactClaim,
        *,
        lease_seconds: int,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.store = store
        self.claim = claim
        self.lease_seconds = lease_seconds
        self.interval_seconds = max(0.1, lease_seconds / 3)
        self._stop = Event()
        self._lost = Event()
        self._failure_lock = Lock()
        self._failure: Exception | None = None
        self._thread: Thread | None = None

    def __enter__(self) -> "ContextArtifactHeartbeat":
        self.ensure_owned()
        self._thread = Thread(
            target=self._run,
            name="context-artifact-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds * 2))

    def ensure_owned(self) -> None:
        if self._lost.is_set():
            self._raise_lost()
        try:
            owned = self.store.heartbeat(
                self.claim,
                lease_seconds=self.lease_seconds,
            )
        except Exception as exc:
            self._mark_lost(exc)
            self._raise_lost(
                "context artifact ownership could not be verified"
            )
        if not owned:
            self._mark_lost()
            self._raise_lost()
        if self._lost.is_set():
            self._raise_lost()

    def _run(self) -> None:
        try:
            while not self._stop.wait(self.interval_seconds):
                if not self.store.heartbeat(
                    self.claim,
                    lease_seconds=self.lease_seconds,
                ):
                    self._mark_lost()
                    return
        except Exception as exc:
            self._mark_lost(exc)

    def _mark_lost(self, failure: Exception | None = None) -> None:
        with self._failure_lock:
            if self._lost.is_set():
                return
            self._failure = failure
            self._lost.set()

    def _raise_lost(
        self,
        message: str = "context artifact claim was lost",
    ) -> None:
        error = ContextArtifactLeaseLost(message)
        with self._failure_lock:
            failure = self._failure
        if failure is not None:
            raise error from failure
        raise error


class ContextCompressionRunner:
    def __init__(
        self,
        store: ContextArtifactStore,
        *,
        lease_seconds: int = 60,
        heartbeat_factory: Callable[..., ContextArtifactHeartbeat] = (
            ContextArtifactHeartbeat
        ),
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.store = store
        self.lease_seconds = lease_seconds
        self.heartbeat_factory = heartbeat_factory

    def resolve(
        self,
        *,
        identity_material: ContextArtifactIdentityMaterial,
        policy: ContextCompressionPolicy,
        source_segments: Sequence[CompressionSourceSegment],
        estimator: TokenEstimator,
        model: str,
        compressor: Callable[[], dict[str, Any]],
        worker_id: str,
        owner_type: OwnerType,
        owner_key: str,
        purpose: ArtifactPurpose,
        parent_ownership: ContextCompressionParentOwnership | None = None,
        retain_until: datetime | None = None,
        expected_question_id_sha256: str | None = None,
        expected_evidence_content_sha256: str | None = None,
        expected_session_scope_sha256: str | None = None,
        expected_question_focus_sha256: str | None = None,
        expected_source_manifest_sha256: str | None = None,
    ) -> ContextCompressionResolution:
        identity = ContextArtifactIdentity.from_material(identity_material)
        claim = self.store.claim(
            identity,
            worker_id=worker_id,
            lease_seconds=self.lease_seconds,
        )
        if claim.status == "completed":
            return self._reuse_completed(
                identity=identity,
                policy=policy,
                source_segments=source_segments,
                estimator=estimator,
                model=model,
                owner_type=owner_type,
                owner_key=owner_key,
                purpose=purpose,
                parent_ownership=parent_ownership,
                retain_until=retain_until,
                expected_question_id_sha256=expected_question_id_sha256,
                expected_evidence_content_sha256=(
                    expected_evidence_content_sha256
                ),
                expected_session_scope_sha256=expected_session_scope_sha256,
                expected_question_focus_sha256=expected_question_focus_sha256,
                expected_source_manifest_sha256=expected_source_manifest_sha256,
            )

        try:
            with self.heartbeat_factory(
                self.store,
                claim,
                lease_seconds=self.lease_seconds,
            ) as heartbeat:
                self._ensure_parent(parent_ownership)
                try:
                    raw_payload = compressor()
                except ContextArtifactProviderFailed:
                    raise
                except Exception as exc:
                    raise ContextArtifactProviderFailed(
                        "context artifact provider failed"
                    ) from exc
                validated = self._validate(
                    policy=policy,
                    payload=raw_payload,
                    source_segments=source_segments,
                    estimator=estimator,
                    model=model,
                    expected_question_id_sha256=expected_question_id_sha256,
                    expected_evidence_content_sha256=(
                        expected_evidence_content_sha256
                    ),
                    expected_session_scope_sha256=expected_session_scope_sha256,
                    expected_question_focus_sha256=expected_question_focus_sha256,
                    expected_source_manifest_sha256=expected_source_manifest_sha256,
                )
                heartbeat.ensure_owned()
                self._ensure_parent(parent_ownership)
                record = self.store.complete(
                    claim,
                    validated.payload.model_dump(mode="json"),
                )
        except Exception as exc:
            if isinstance(exc, ContextArtifactLeaseLost):
                recovered = self._recover_completed(
                    identity=identity,
                    policy=policy,
                    source_segments=source_segments,
                    estimator=estimator,
                    model=model,
                    owner_type=owner_type,
                    owner_key=owner_key,
                    purpose=purpose,
                    parent_ownership=parent_ownership,
                    retain_until=retain_until,
                    expected_question_id_sha256=expected_question_id_sha256,
                    expected_evidence_content_sha256=(
                        expected_evidence_content_sha256
                    ),
                    expected_session_scope_sha256=expected_session_scope_sha256,
                    expected_question_focus_sha256=expected_question_focus_sha256,
                    expected_source_manifest_sha256=expected_source_manifest_sha256,
                )
                if recovered is not None:
                    return recovered
                raise
            try:
                self.store.fail(
                    claim,
                    error_code=self._failure_code(exc),
                )
            except ContextArtifactLeaseLost:
                recovered = self._recover_completed(
                    identity=identity,
                    policy=policy,
                    source_segments=source_segments,
                    estimator=estimator,
                    model=model,
                    owner_type=owner_type,
                    owner_key=owner_key,
                    purpose=purpose,
                    parent_ownership=parent_ownership,
                    retain_until=retain_until,
                    expected_question_id_sha256=expected_question_id_sha256,
                    expected_evidence_content_sha256=(
                        expected_evidence_content_sha256
                    ),
                    expected_session_scope_sha256=expected_session_scope_sha256,
                    expected_question_focus_sha256=expected_question_focus_sha256,
                    expected_source_manifest_sha256=expected_source_manifest_sha256,
                )
                if recovered is not None:
                    return recovered
            except Exception:
                # Failure recording is best effort and cannot replace the
                # original provider/validation/parent-ownership exception.
                pass
            raise

        ref = self.store.create_owner_ref(
            record,
            owner_type=owner_type,
            owner_key=owner_key,
            purpose=purpose,
            retain_until=retain_until,
        )
        authoritative = self.store.load_ref(
            ref,
            owner_type=owner_type,
            owner_key=owner_key,
            purpose=purpose,
            expected_identity=identity,
        )
        self._ensure_parent(parent_ownership)
        return ContextCompressionResolution(
            route="artifact_created",
            ref=ref,
            record=authoritative,
            payload=validated.payload,
            stats=validated.stats,
        )

    def _reuse_completed(
        self,
        *,
        identity: ContextArtifactIdentity,
        policy: ContextCompressionPolicy,
        source_segments: Sequence[CompressionSourceSegment],
        estimator: TokenEstimator,
        model: str,
        owner_type: OwnerType,
        owner_key: str,
        purpose: ArtifactPurpose,
        parent_ownership: ContextCompressionParentOwnership | None,
        retain_until: datetime | None,
        expected_question_id_sha256: str | None,
        expected_evidence_content_sha256: str | None,
        expected_session_scope_sha256: str | None,
        expected_question_focus_sha256: str | None,
        expected_source_manifest_sha256: str | None,
    ) -> ContextCompressionResolution:
        record = self.store.get_terminal_by_key(identity.artifact_key)
        if record is None:
            raise ContextArtifactMissing("context artifact record is missing")
        if record.status != "completed" or record.identity != identity:
            raise ContextArtifactConflict(
                "context artifact completed claim conflicts with stored state"
            )
        validated = self._validate(
            policy=policy,
            payload=record.payload or {},
            source_segments=source_segments,
            estimator=estimator,
            model=model,
            expected_question_id_sha256=expected_question_id_sha256,
            expected_evidence_content_sha256=expected_evidence_content_sha256,
            expected_session_scope_sha256=expected_session_scope_sha256,
            expected_question_focus_sha256=expected_question_focus_sha256,
            expected_source_manifest_sha256=expected_source_manifest_sha256,
        )
        ref = self.store.create_owner_ref(
            record,
            owner_type=owner_type,
            owner_key=owner_key,
            purpose=purpose,
            retain_until=retain_until,
        )
        authoritative = self.store.load_ref(
            ref,
            owner_type=owner_type,
            owner_key=owner_key,
            purpose=purpose,
            expected_identity=identity,
        )
        self._ensure_parent(parent_ownership)
        return ContextCompressionResolution(
            route="artifact_reused",
            ref=ref,
            record=authoritative,
            payload=validated.payload,
            stats=validated.stats,
        )

    def _recover_completed(self, **kwargs) -> ContextCompressionResolution | None:
        record = self.store.get_terminal_by_key(kwargs["identity"].artifact_key)
        if record is None or record.status != "completed":
            return None
        return self._reuse_completed(**kwargs)

    @staticmethod
    def _validate(**kwargs) -> ValidatedCompressionArtifact:
        return validate_compression_artifact(**kwargs)

    @staticmethod
    def _ensure_parent(
        parent_ownership: ContextCompressionParentOwnership | None,
    ) -> None:
        if parent_ownership is not None:
            parent_ownership.ensure_owned()

    @staticmethod
    def _failure_code(exc: Exception) -> str:
        from app.services.context_artifacts import (
            ContextArtifactValidationFailed,
        )

        if isinstance(exc, ContextArtifactValidationFailed):
            return "validation_failed"
        if isinstance(exc, ContextArtifactProviderFailed):
            return "provider_failed"
        return "parent_or_runtime_failed"
