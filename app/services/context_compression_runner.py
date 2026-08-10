from __future__ import annotations

from dataclasses import dataclass
from asyncio import CancelledError
from datetime import datetime
from hashlib import sha256
from threading import Event, Lock, Thread
from typing import Any, Callable, Literal, Protocol

from app.ports.context_artifacts import ContextArtifactStore
from app.services.context_artifacts import (
    ArtifactPurpose,
    ArtifactPayload,
    ContextArtifactClaim,
    ContextArtifactConflict,
    ContextArtifactIdentity,
    ContextArtifactIdentityMaterial,
    ContextArtifactLeaseLost,
    ContextArtifactMissing,
    ContextArtifactProviderFailed,
    ContextArtifactValidationFailed,
    ContextArtifactRecord,
    ContextArtifactRef,
    OwnerType,
)
from app.services.context_compression_request import (
    ResolvedCompressionRequest,
    bind_resolved_target_to_identity,
)
from app.services.context_compression_validation import (
    CompressionValidationStats,
    ValidatedCompressionArtifact,
    validate_compression_artifact,
)
from app.services.context_compression_intent import (
    CompressionIntent,
    validate_compression_intent_digest,
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
        interval_seconds: float | None = None,
        failure_containment=None,
        failure_authorization=None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.store = store
        self.claim = claim
        self.lease_seconds = lease_seconds
        self.interval_seconds = (
            max(0.001, interval_seconds)
            if interval_seconds is not None
            else max(0.1, lease_seconds / 3)
        )
        self.failure_containment = failure_containment
        self.failure_authorization = failure_authorization
        self._authorization_lock = Lock()
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
        self._ensure_failure_state_owned()
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
                self._ensure_failure_state_owned()
        except Exception as exc:
            self._mark_lost(exc)

    def _ensure_failure_state_owned(self) -> None:
        with self._authorization_lock:
            authorization = self.failure_authorization
            if self.failure_containment is None or authorization is None:
                return
            try:
                owned = self.failure_containment.heartbeat_attempt(authorization)
            except Exception as exc:
                self._mark_lost(exc)
                self._raise_lost(
                    "context compression failure-state ownership could not be verified"
                )
            if owned is False or owned is None:
                self._mark_lost()
                self._raise_lost(
                    "context compression failure-state ownership was lost"
                )
            if owned is not True:
                # Durable stores return a refreshed dual capability because the
                # state version advances with every successful lease renewal.
                self.failure_authorization = owned

    def detach_failure_authorization(self):
        """Atomically stop renewal and return the latest fenced capability."""
        with self._authorization_lock:
            authorization = self.failure_authorization
            self.failure_authorization = None
            return authorization

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
        failure_containment=None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.store = store
        self.lease_seconds = lease_seconds
        self.heartbeat_factory = heartbeat_factory
        self.failure_containment = failure_containment

    def resolve(
        self,
        *,
        identity_material: ContextArtifactIdentityMaterial,
        request: ResolvedCompressionRequest,
        estimator: TokenEstimator,
        model: str,
        compressor: Callable[[ResolvedCompressionRequest], dict[str, Any]],
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
        if not isinstance(request, ResolvedCompressionRequest):
            raise TypeError("request must be a ResolvedCompressionRequest")
        heartbeat = None
        artifact_completed = False
        try:
            bound_identity_material = bind_resolved_target_to_identity(
                identity_material,
                request,
            )
        except ValueError as exc:
            raise ContextArtifactConflict(str(exc)) from exc
        self._validate_intent_identity(
            intent=request.intent,
            identity_material=bound_identity_material,
        )
        identity = ContextArtifactIdentity.from_material(
            bound_identity_material
        )
        reuse_kwargs = {
            "identity": identity,
            "request": request,
            "estimator": estimator,
            "model": model,
            "owner_type": owner_type,
            "owner_key": owner_key,
            "purpose": purpose,
            "parent_ownership": parent_ownership,
            "retain_until": retain_until,
            "expected_question_id_sha256": expected_question_id_sha256,
            "expected_evidence_content_sha256": expected_evidence_content_sha256,
            "expected_session_scope_sha256": expected_session_scope_sha256,
            "expected_question_focus_sha256": expected_question_focus_sha256,
            "expected_source_manifest_sha256": expected_source_manifest_sha256,
        }

        # Completed, immutable Artifacts remain authoritative even while a
        # matching circuit is open.  This read also keeps blocked attempts from
        # creating an unnecessary running Artifact claim.
        terminal = self.store.get_terminal_by_key(identity.artifact_key)
        if terminal is not None and terminal.status == "completed":
            return self._reuse_completed(**reuse_kwargs)

        authorization = None
        containment_finished = False
        if self.failure_containment is not None:
            self._ensure_parent(parent_ownership)
            try:
                authorization = self._authorize_attempt(
                    material=bound_identity_material,
                    owner_type=owner_type,
                    owner_key=owner_key,
                    worker_id=worker_id,
                )
            except Exception as exc:
                raise ContextArtifactProviderFailed(
                    "context compression failure state is unavailable",
                    failure_code="failure_state_unavailable",
                ) from exc
            if not getattr(authorization, "allow_provider_call", False):
                reason = getattr(authorization, "reason", "failure_state_blocked")
                error_type = (
                    ContextArtifactValidationFailed
                    if str(reason).startswith("validation_quarantine")
                    else ContextArtifactProviderFailed
                )
                raise error_type(
                    "context compression is temporarily unavailable",
                    failure_code=str(reason),
                )

        try:
            claim = self.store.claim(
                identity,
                worker_id=worker_id,
                lease_seconds=self.lease_seconds,
            )
        except BaseException as exc:
            self._abort_attempt(authorization, self._abort_reason(exc))
            raise
        if claim.status == "completed":
            self._abort_attempt(authorization, "artifact_reused")
            return self._reuse_completed(**reuse_kwargs)

        try:
            heartbeat_kwargs = {"lease_seconds": self.lease_seconds}
            if (
                authorization is not None
                and hasattr(self.failure_containment, "heartbeat_attempt")
            ):
                heartbeat_kwargs.update(
                    failure_containment=self.failure_containment,
                    failure_authorization=authorization,
                )
            with self.heartbeat_factory(
                self.store,
                claim,
                **heartbeat_kwargs,
            ) as heartbeat:
                try:
                    self._ensure_parent(parent_ownership)
                except ContextArtifactLeaseLost:
                    authorization = self._detach_failure_authorization(
                        heartbeat,
                        authorization,
                    )
                    self._abort_attempt(authorization, "parent_lease_lost")
                    containment_finished = authorization is not None
                    raise
                try:
                    raw_payload = compressor(request)
                except CancelledError:
                    authorization = self._detach_failure_authorization(
                        heartbeat,
                        authorization,
                    )
                    self._abort_attempt(authorization, "cancelled")
                    containment_finished = True
                    raise
                except BaseException as exc:
                    authorization = self._detach_failure_authorization(
                        heartbeat,
                        authorization,
                    )
                    failure_code = self._provider_failure_code(exc)
                    if failure_code is None:
                        self._abort_attempt(authorization, "provider_unclassified")
                    else:
                        self._finish_attempt(
                            authorization,
                            outcome="provider_failed",
                            failure_code=failure_code,
                        )
                    containment_finished = authorization is not None
                    raise ContextArtifactProviderFailed(
                        "context artifact provider failed",
                        failure_code=failure_code or "provider_failed",
                    ) from exc
                try:
                    validated = self._validate(
                        request=request,
                        payload=raw_payload,
                        estimator=estimator,
                        model=model,
                        expected_question_id_sha256=expected_question_id_sha256,
                        expected_evidence_content_sha256=(
                            expected_evidence_content_sha256
                        ),
                        expected_session_scope_sha256=(
                            expected_session_scope_sha256
                        ),
                        expected_question_focus_sha256=(
                            expected_question_focus_sha256
                        ),
                        expected_source_manifest_sha256=(
                            expected_source_manifest_sha256
                        ),
                    )
                except ContextArtifactValidationFailed as exc:
                    authorization = self._detach_failure_authorization(
                        heartbeat,
                        authorization,
                    )
                    failure_code = self._validation_failure_code(exc)
                    exc.failure_code = failure_code
                    self._finish_attempt(
                        authorization,
                        outcome="validation_failed",
                        failure_code=failure_code,
                    )
                    containment_finished = authorization is not None
                    raise
                heartbeat.ensure_owned()
                authorization = self._detach_failure_authorization(
                    heartbeat,
                    authorization,
                )
                if self.failure_containment is None:
                    self._ensure_parent(parent_ownership)
                record = self.store.complete(
                    claim,
                    validated.payload.model_dump(mode="json"),
                )
                artifact_completed = True
                self._finish_attempt(
                    authorization,
                    outcome="success",
                    failure_code=None,
                )
                containment_finished = authorization is not None
        except BaseException as exc:
            if not containment_finished:
                authorization = self._detach_failure_authorization(
                    heartbeat,
                    authorization,
                )
                self._abort_attempt(authorization, self._abort_reason(exc))
            if artifact_completed:
                # The Artifact is already authoritative and must remain
                # completed, but failure-state unavailability is fail-closed
                # and must not be hidden behind completed-Artifact recovery.
                raise
            if isinstance(exc, ContextArtifactLeaseLost):
                recovered = self._recover_completed(**reuse_kwargs)
                if recovered is not None:
                    return recovered
                raise
            try:
                self.store.fail(
                    claim,
                    error_code=self._failure_code(exc),
                )
            except ContextArtifactLeaseLost:
                recovered = self._recover_completed(**reuse_kwargs)
                if recovered is not None:
                    return recovered
            except Exception:
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
        request: ResolvedCompressionRequest,
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
            request=request,
            payload=record.payload or {},
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

    def _authorize_attempt(
        self,
        *,
        material: ContextArtifactIdentityMaterial,
        owner_type: OwnerType,
        owner_key: str,
        worker_id: str,
    ):
        from app.services.context_compression_failure_containment import (
            build_provider_circuit_scope,
            build_validation_quarantine_scope,
        )

        provider_scope = build_provider_circuit_scope(
            privacy_scope_sha256=material.privacy_scope_sha256,
            owner_type=owner_type,
            owner_key=owner_key,
            provider=material.compressor_provider,
            model=material.compressor_model,
            artifact_type=material.artifact_type,
            policy_version=material.compression_policy_version,
        )
        intent_sha256 = material.compression_intent_sha256 or sha256(
            b"context-compression-intent:none-v0"
        ).hexdigest()
        validation_scope = build_validation_quarantine_scope(
            privacy_scope_sha256=material.privacy_scope_sha256,
            owner_type=owner_type,
            owner_key=owner_key,
            artifact_type=material.artifact_type,
            source_manifest_sha256=(
                material.source_manifest_sha256 or material.source_sha256
            ),
            compression_intent_sha256=intent_sha256,
            prompt_contract_version=material.prompt_contract_version,
            output_schema_version=material.output_schema_version,
            policy_version=material.compression_policy_version,
            provider=material.compressor_provider,
            model=material.compressor_model,
        )
        return self.failure_containment.authorize_attempt(
            provider_scope=provider_scope,
            validation_scope=validation_scope,
            worker_id=worker_id,
        )

    def _finish_attempt(
        self,
        authorization,
        *,
        outcome: str,
        failure_code: str | None,
    ) -> None:
        if authorization is None:
            return
        try:
            self.failure_containment.finish_attempt(
                authorization,
                outcome=outcome,
                failure_code=failure_code,
            )
        except Exception as exc:
            raise ContextArtifactProviderFailed(
                "context compression failure state is unavailable",
                failure_code="failure_state_unavailable",
            ) from exc

    def _abort_attempt(self, authorization, reason: str) -> None:
        if authorization is None or self.failure_containment is None:
            return
        abort = getattr(self.failure_containment, "abort_attempt", None)
        if abort is None:
            return
        try:
            abort(authorization, reason=reason)
        except Exception:
            # Abort is cleanup-only. A stale capability is already harmless and
            # must not replace the authoritative business/ownership exception.
            return

    @staticmethod
    def _detach_failure_authorization(heartbeat, authorization):
        if heartbeat is None:
            return authorization
        detach = getattr(heartbeat, "detach_failure_authorization", None)
        if detach is None:
            return authorization
        refreshed = detach()
        return refreshed if refreshed is not None else authorization

    @staticmethod
    def _provider_failure_code(exc: BaseException) -> str | None:
        if isinstance(exc, TimeoutError):
            return "provider_timeout"
        if isinstance(exc, ConnectionError):
            return "provider_connection"
        if isinstance(exc, ContextArtifactProviderFailed):
            code = getattr(exc, "failure_code", None)
            if code in {
                "provider_timeout",
                "provider_connection",
                "provider_unavailable",
            }:
                return code
            return "provider_unavailable"
        return None

    @staticmethod
    def _validation_failure_code(
        exc: ContextArtifactValidationFailed,
    ) -> str:
        message = str(exc).lower()
        grounding_markers = (
            "ground",
            "source anchor",
            "source excerpt",
            "supporting excerpt",
            "required number",
            "required identifier",
        )
        if any(marker in message for marker in grounding_markers):
            return "grounding_failed"
        return "invalid_schema"

    @staticmethod
    def _abort_reason(exc: BaseException) -> str:
        from app.services.context_artifacts import ContextArtifactBusy

        if isinstance(exc, CancelledError):
            return "cancelled"
        if isinstance(exc, ContextArtifactBusy):
            return "artifact_busy"
        if isinstance(exc, ContextArtifactLeaseLost):
            return "artifact_lease_lost"
        if isinstance(exc, ContextArtifactConflict):
            return "identity_conflict"
        return "parent_or_runtime_failed"

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

    @staticmethod
    def _validate_intent_identity(
        *,
        intent: CompressionIntent | None,
        identity_material: ContextArtifactIdentityMaterial,
    ) -> None:
        if intent is None:
            if identity_material.identity_schema_version is not None:
                raise ContextArtifactConflict(
                    "identity-v1 requires compression intent"
                )
            return
        if (
            identity_material.identity_schema_version != "identity-v1"
            or identity_material.compression_intent_sha256 is None
        ):
            raise ContextArtifactConflict(
                "compression intent requires identity-v1 material"
            )
        try:
            validate_compression_intent_digest(
                intent,
                identity_material.compression_intent_sha256,
            )
        except ValueError as exc:
            raise ContextArtifactConflict(
                "compression intent digest conflicts with identity"
            ) from exc
