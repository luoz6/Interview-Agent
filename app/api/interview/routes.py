from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from uuid import UUID, uuid5

from app.application.interview.session_commands import (
    DurableSessionStream,
    InterviewApplicationService,
    LegacySessionStream,
    StreamingTurnService,
    publish_round_closed_event as _publish_round_closed_event,
    turn_to_dict as _turn_to_dict,
)
from app.application.interview.interview_start import (
    InterviewPlanNotLaunchable,
    InterviewStartService,
)
from app.api.shared import dependencies
from app.api.shared.dependencies import get_draft_store, get_prep_plan_store
from app.api.shared.errors import raise_if_deleting as _raise_if_deleting
from app.api.shared.errors import raise_prep_plan_error as _raise_prep_plan_error
from app.api.shared.errors import (
    raise_session_deleting_error as _raise_session_deleting_error,
)
from app.api.shared.errors import raise_value_error as _raise_value_error
from app.api.shared.errors import version_conflict_response as _version_conflict_response
from app.api.shared.models import (
    AnswerRequest,
    DraftRequest,
    SessionCommandRequest,
    StartInterviewRequest,
)
from app.domain.interview.commands import SessionCommand
from app.domain.interview.drafts import DraftWriteConflict
from app.domain.interview.errors import SessionDeletingError, SessionVersionConflict
from app.services.agent_runtime import correlation_id_from_plan
from app.services.interview_launch import InterviewLaunchCoordinator
from app.services.interview_plan_revision import v2_plan_to_legacy
from app.services.interview_plan_revision_store import (
    PlanRevisionNotFound,
    PlanSourceUnavailable,
)
from app.services.job_tags import extract_job_tags
from app.ports.runtime import InterviewSessionRepository
from app.services.prep_plans import PrepPlanError
from app.services.session_plan_binding import (
    session_plan_binding_from_revision,
    session_plan_binding_from_state,
)
from app.services.principal_memory_session_choice import (
    PrincipalMemorySessionChoiceBinder,
    PrincipalMemorySessionChoiceConflict,
)
from app.runtime.config.compatibility import (
    get_interview_langgraph_rollout_percent,
    get_runtime_store,
)


router = APIRouter()

_SESSION_START_REQUEST_NAMESPACE = UUID(
    "d27df012-60f1-4df7-b8a0-c44f5209b06b"
)
_SESSION_START_REQUEST_CONFLICT = "session_start_request_conflict"


@router.post("/interview-drafts")
def save_interview_draft(
    payload: DraftRequest,
    draft_store=Depends(get_draft_store),
    revision_store=Depends(dependencies.get_plan_revision_store),
):
    with revision_store.source_reference_recovery_lock():
        return _save_interview_draft_locked(
            payload,
            draft_store,
            revision_store,
        )


def _save_interview_draft_locked(payload, draft_store, revision_store):
    try:
        if not hasattr(draft_store, "prepare_save"):
            if payload.plan_family_id is not None or payload.clear_plan:
                raise ValueError(
                    "revision-bound drafts require a revision-aware draft store"
                )
            return draft_store.save(
                draft_id=payload.draft_id,
                job_description=payload.job_description,
                resume_text=payload.resume_text,
                title=payload.title,
                job_tags=(
                    payload.job_tags
                    if payload.job_tags is not None
                    else extract_job_tags(payload.job_description)
                ),
            )
        _reconcile_draft_source_references(draft_store, revision_store)
        previous_revision = None
        if payload.draft_id is not None:
            try:
                existing_draft = draft_store.get(payload.draft_id)
                previous_revision_id = existing_draft.get(
                    "latest_plan_revision_id"
                )
                if previous_revision_id:
                    previous_revision = revision_store.get_by_id(
                        previous_revision_id
                    )
            except ValueError:
                pass
        plan_source_sha256 = None
        revision = None
        if payload.latest_plan_revision_id is not None:
            revision = revision_store.get_by_id(
                payload.latest_plan_revision_id
            )
            if revision.plan_family_id != payload.plan_family_id:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "draft_plan_family_mismatch"},
                )
            plan_source_sha256 = revision.source_sha256
        prepared = draft_store.prepare_save(
            draft_id=payload.draft_id,
            job_description=payload.job_description,
            resume_text=payload.resume_text,
            title=payload.title,
            job_tags=(
                payload.job_tags
                if payload.job_tags is not None
                else extract_job_tags(payload.job_description)
            ),
            plan_family_id=payload.plan_family_id,
            latest_plan_revision_id=payload.latest_plan_revision_id,
            plan_source_sha256=plan_source_sha256,
            clear_plan=payload.clear_plan,
        )
        target_revision_id = prepared.get("latest_plan_revision_id")
        target_revision = (
            revision_store.get_by_id(target_revision_id)
            if target_revision_id is not None
            else None
        )
        old_source_id = (
            previous_revision.source_id if previous_revision else None
        )
        new_source_id = target_revision.source_id if target_revision else None
        revision_store.replace_source_reference(
            old_source_id=old_source_id,
            new_source_id=new_source_id,
            owner_type="draft",
            owner_id=prepared["draft_id"],
        )
        try:
            return draft_store.commit_save(prepared)
        except Exception:
            revision_store.replace_source_reference(
                old_source_id=new_source_id,
                new_source_id=old_source_id,
                owner_type="draft",
                owner_id=prepared["draft_id"],
            )
            raise
    except PlanRevisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanSourceUnavailable as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "plan_source_unavailable"},
        ) from exc
    except DraftWriteConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "draft_write_conflict"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/interview-drafts/{draft_id}")
def get_interview_draft(draft_id: str, draft_store=Depends(get_draft_store)):
    try:
        return draft_store.get(draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/interview-drafts/{draft_id}", status_code=204)
def delete_interview_draft(
    draft_id: str,
    draft_store=Depends(get_draft_store),
    plan_store=Depends(get_prep_plan_store),
    revision_store=Depends(dependencies.get_plan_revision_store),
):
    with revision_store.source_reference_recovery_lock():
        return _delete_interview_draft_locked(
            draft_id,
            draft_store,
            plan_store,
            revision_store,
        )


def _delete_interview_draft_locked(
    draft_id,
    draft_store,
    plan_store,
    revision_store,
):
    try:
        _reconcile_draft_source_references(draft_store, revision_store)
        draft = draft_store.get(draft_id)
        revision_id = draft.get("latest_plan_revision_id")
        revision = (
            revision_store.get_by_id(revision_id) if revision_id else None
        )
        if revision is not None:
            revision_store.replace_source_reference(
                old_source_id=revision.source_id,
                new_source_id=None,
                owner_type="draft",
                owner_id=draft_id,
            )
        try:
            deleted = draft_store.delete(draft_id)
            if deleted is False:
                raise ValueError("draft not found")
            if getattr(draft_store, "durability", None) != "postgres":
                plan_store.delete_by_source_draft(draft_id)
        except Exception:
            if revision is not None:
                revision_store.replace_source_reference(
                    old_source_id=None,
                    new_source_id=revision.source_id,
                    owner_type="draft",
                    owner_id=draft_id,
                )
            raise
    except (ValueError, PlanRevisionNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


# Compatibility dependency exports. Runtime calls below resolve through the
# shared module so replacements are observed without import-time store capture.
get_agent_execution_runner = dependencies.get_agent_execution_runner
get_event_publisher = dependencies.get_event_publisher
get_interview_launch_coordinator = dependencies.get_interview_launch_coordinator
get_interview_workflow_service = dependencies.get_interview_workflow_service
get_report_job_store = dependencies.get_report_job_store
get_runtime_control_store = dependencies.get_runtime_control_store
get_session_store = dependencies.get_session_store
get_legacy_launch_session_store = dependencies.get_legacy_launch_session_store
get_request_interview_launch_coordinator = (
    dependencies.get_request_interview_launch_coordinator
)
@router.post("/interviews")
def start_interview(
    payload: StartInterviewRequest,
    start_service: InterviewStartService | None = Depends(
        dependencies.get_legacy_interview_start_service
    ),
    launch_coordinator: InterviewLaunchCoordinator | None = Depends(
        get_request_interview_launch_coordinator
    ),
    revision_store=Depends(dependencies.get_request_plan_revision_store),
    principal_memory_control_store=Depends(
        dependencies.get_request_principal_memory_control_store
    ),
    principal_identity_resolver=Depends(
        dependencies.get_request_principal_identity_resolver
    ),
):
    if payload.plan_revision_id is not None:
        if start_service is None:
            raise HTTPException(
                status_code=503,
                detail="session store is unavailable",
            )
        with revision_store.source_reference_recovery_lock():
            return _start_interview_locked(
                payload,
                start_service.store,
                revision_store,
                principal_memory_control_store=principal_memory_control_store,
                principal_identity_resolver=principal_identity_resolver,
            )

    if payload.plan_id is not None:
        if payload.expected_plan_version is None or not payload.command_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INVALID_LAUNCH_REQUEST",
                    "message": "缺少计划版本或安全启动标识。",
                    "retryable": False,
                },
            )
        if launch_coordinator is None:
            raise HTTPException(status_code=503, detail="launch coordinator is unavailable")
        try:
            return launch_coordinator.launch(
                plan_id=payload.plan_id,
                expected_plan_version=payload.expected_plan_version,
                command_id=payload.command_id,
            )
        except PrepPlanError as exc:
            _raise_prep_plan_error(exc)

    if not payload.job_description or not payload.resume_text:
        raise HTTPException(status_code=422, detail="legacy launch input is incomplete")
    if start_service is None:
        raise HTTPException(status_code=503, detail="interview start is unavailable")
    try:
        turn = start_service.start(
            job_description=payload.job_description,
            resume_text=payload.resume_text,
        )
    except InterviewPlanNotLaunchable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _turn_to_dict(turn)


def _start_interview_locked(
    payload,
    store,
    revision_store,
    *,
    principal_memory_control_store=None,
    principal_identity_resolver=None,
):
    choice_binder = PrincipalMemorySessionChoiceBinder(
        identity_resolver=principal_identity_resolver,
        control_store=principal_memory_control_store,
    )
    choice_created = False
    try:
        revision_store.reconcile_session_source_references()
        revision = revision_store.get_by_id(payload.plan_revision_id)
        plan_binding = session_plan_binding_from_revision(
            revision,
            principal_memory_mode=payload.principal_memory_mode,
        )
        session_id = _session_id_for_start_request(
            revision.plan_family_id,
            payload.request_id,
        )
        turn = _load_session_start_replay(
            store,
            session_id,
            plan_binding,
            expected_revision=payload.expected_revision,
            plan_sha256=payload.plan_sha256,
        )
        if turn is not None:
            revision_store.add_source_reference(
                revision.source_id,
                owner_type="session",
                owner_id=turn.session_id,
            )
            return _turn_to_dict(turn)
        latest = revision_store.get_latest(revision.plan_family_id)
        if latest.plan_revision_id != revision.plan_revision_id:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "plan_revision_conflict",
                    "current_revision": {
                        "plan_revision_id": latest.plan_revision_id,
                        "revision": latest.revision,
                        "plan_sha256": latest.plan_sha256,
                    },
                },
            )
        if payload.expected_revision != revision.revision:
            raise HTTPException(
                status_code=409,
                detail="plan revision conflict",
            )
        if payload.plan_sha256 != revision.plan_sha256:
            raise HTTPException(status_code=409, detail="plan hash conflict")
        source = revision_store.get_source(revision.source_id)
        if source.protected_payload is None:
            raise HTTPException(
                status_code=422,
                detail="plan source unavailable",
            )
        source_payload = source.protected_payload
        plan = v2_plan_to_legacy(revision.plan)
        revision_store.add_source_reference(
            revision.source_id,
            owner_type="session",
            owner_id=session_id,
        )
        try:
            choice_created = choice_binder.prepare(
                session_id=session_id,
                mode=payload.principal_memory_mode,
            )
            if (
                get_runtime_store() == "postgres"
                and get_interview_langgraph_rollout_percent() > 0
            ):
                turn = dependencies.get_interview_workflow_service().start(
                    plan,
                    job_description=source_payload.job_description,
                    resume_text=source_payload.resume_text,
                    job_tags=list(source_payload.job_tags),
                    plan_binding=plan_binding,
                    session_id=session_id,
                )
            else:
                turn = store.start(
                    plan,
                    job_description=source_payload.job_description,
                    resume_text=source_payload.resume_text,
                    job_tags=list(source_payload.job_tags),
                    plan_binding=plan_binding,
                    session_id=session_id,
                )
        except Exception:
            turn = _load_session_start_replay(
                store,
                session_id,
                plan_binding,
                expected_revision=payload.expected_revision,
                plan_sha256=payload.plan_sha256,
            )
            if turn is None:
                choice_binder.rollback(
                    session_id=session_id,
                    created=choice_created,
                )
                revision_store.remove_source_reference(
                    revision.source_id,
                    owner_type="session",
                    owner_id=session_id,
                )
                raise
    except (SessionStartRequestConflict, PrincipalMemorySessionChoiceConflict):
        return JSONResponse(
            status_code=409,
            content={"code": _SESSION_START_REQUEST_CONFLICT},
        )
    except PlanRevisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _turn_to_dict(turn)


class SessionStartRequestConflict(Exception):
    pass


def _session_id_for_start_request(
    plan_family_id: str,
    request_id: str,
) -> str:
    return str(
        uuid5(
            _SESSION_START_REQUEST_NAMESPACE,
            f"{plan_family_id}:{request_id}",
        )
    )


def _load_session_start_replay(
    store,
    session_id: str,
    plan_binding,
    *,
    expected_revision: int,
    plan_sha256: str,
):
    try:
        state = store.get(session_id)
    except ValueError as exc:
        if str(exc) == "session not found":
            return None
        raise
    existing_binding = session_plan_binding_from_state(state)
    if (
        existing_binding != plan_binding
        or existing_binding.revision != expected_revision
        or existing_binding.plan_sha256 != plan_sha256
    ):
        raise SessionStartRequestConflict()
    return store._to_turn(state, follow_up=None)


def _reconcile_draft_source_references(draft_store, revision_store) -> None:
    if not hasattr(draft_store, "plan_revision_bindings"):
        return
    expected = {
        draft_id: revision_store.get_by_id(revision_id).source_id
        for draft_id, revision_id in (
            draft_store.plan_revision_bindings().items()
        )
    }
    revision_store.reconcile_source_references(
        owner_type="draft",
        expected=expected,
    )


@router.get("/interviews/{session_id}")
def get_interview_session(
    session_id: str,
    application: InterviewApplicationService = Depends(
        dependencies.get_interview_application_service
    ),
):
    try:
        return application.snapshot(session_id)
    except SessionDeletingError as exc:
        _raise_session_deleting_error(exc)
    except ValueError as exc:
        _raise_value_error(exc)


@router.get("/interviews/{session_id}/agent-runs")
def list_agent_runs(
    session_id: str,
    agent: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    store: InterviewSessionRepository = Depends(dependencies.get_session_store),
    control=Depends(dependencies.get_runtime_control_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        _raise_value_error(exc)
    if control is None:
        return {"session_id": session_id, "items": []}
    correlation_id = correlation_id_from_plan(
        state["plan"],
        session_id=session_id,
    )
    return {
        "session_id": session_id,
        "items": control.list_agent_runs(
            session_id=session_id,
            correlation_id=correlation_id,
            agent=agent,
            status=status,
            limit=limit,
        ),
    }


@router.get("/interviews/{session_id}/runtime-events")
def list_runtime_events(
    session_id: str,
    status: str | None = None,
    event_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    store: InterviewSessionRepository = Depends(dependencies.get_session_store),
    control=Depends(dependencies.get_runtime_control_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        _raise_value_error(exc)
    if control is None:
        return {"session_id": session_id, "items": []}
    return {
        "session_id": session_id,
        "items": control.list_runtime_events(
            session_id=session_id,
            status=status,
            event_type=event_type,
            limit=limit,
        ),
    }


@router.post("/interviews/{session_id}/answer")
def submit_answer(
    session_id: str,
    payload: AnswerRequest,
    application: InterviewApplicationService = Depends(
        dependencies.get_interview_application_service
    ),
):
    try:
        result = application.execute(
            SessionCommand.answer(
                session_id,
                payload.answer,
                expected_version=payload.expected_version,
                command_id=payload.command_id,
            )
        )
        if result.accepted is not None:
            return JSONResponse(
                status_code=202,
                content=result.accepted.model_dump(mode="json"),
            )
        if result.turn is None:
            raise RuntimeError("legacy session command did not return a turn")
        return _turn_to_dict(result.turn)
    except SessionDeletingError as exc:
        _raise_session_deleting_error(exc)
    except SessionVersionConflict as exc:
        return _version_conflict_response(exc)
    except ValueError as exc:
        _raise_value_error(exc)


@router.post("/interviews/{session_id}/answer/stream")
def submit_answer_stream(
    session_id: str,
    payload: AnswerRequest,
    streaming: StreamingTurnService = Depends(
        dependencies.get_streaming_turn_service
    ),
):
    try:
        result = streaming.prepare(
            SessionCommand.answer(
                session_id,
                payload.answer,
                expected_version=payload.expected_version,
                command_id=payload.command_id,
            )
        )
        if isinstance(result, DurableSessionStream):
            return StreamingResponse(
                result.events,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
    except SessionDeletingError as exc:
        _raise_session_deleting_error(exc)
    except SessionVersionConflict as exc:
        return _version_conflict_response(exc)
    except ValueError as exc:
        _raise_value_error(exc)

    if not isinstance(result, LegacySessionStream):
        raise RuntimeError("unknown interview stream result")

    def event_stream():
        for event in result.events:
            yield event.to_sse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/interviews/{session_id}/finish")
def finish_interview(
    session_id: str,
    payload: SessionCommandRequest | None = None,
    application: InterviewApplicationService = Depends(
        dependencies.get_interview_application_service
    ),
):
    payload = payload or SessionCommandRequest()
    try:
        result = application.execute(
            SessionCommand(
                session_id=session_id,
                command_type="finish",
                expected_version=payload.expected_version,
                command_id=payload.command_id,
            )
        )
        if result.accepted is not None:
            return JSONResponse(
                status_code=202,
                content=result.accepted.model_dump(mode="json"),
            )
        if result.turn is None:
            raise RuntimeError("legacy session command did not return a turn")
        return _turn_to_dict(result.turn)
    except SessionDeletingError as exc:
        _raise_session_deleting_error(exc)
    except SessionVersionConflict as exc:
        return _version_conflict_response(exc)
    except ValueError as exc:
        _raise_value_error(exc)


@router.post("/interviews/{session_id}/skip")
def skip_interview_question(
    session_id: str,
    payload: SessionCommandRequest | None = None,
    application: InterviewApplicationService = Depends(
        dependencies.get_interview_application_service
    ),
):
    payload = payload or SessionCommandRequest()
    try:
        result = application.execute(
            SessionCommand(
                session_id=session_id,
                command_type="skip",
                expected_version=payload.expected_version,
                command_id=payload.command_id,
            )
        )
        if result.accepted is not None:
            return JSONResponse(
                status_code=202,
                content=result.accepted.model_dump(mode="json"),
            )
        if result.turn is None:
            raise RuntimeError("legacy session command did not return a turn")
        return _turn_to_dict(result.turn)
    except SessionDeletingError as exc:
        _raise_session_deleting_error(exc)
    except SessionVersionConflict as exc:
        return _version_conflict_response(exc)
    except ValueError as exc:
        _raise_value_error(exc)


@router.get(
    "/interviews/{session_id}/commands/{command_id}/stream"
)
def stream_interview_command(
    session_id: str,
    command_id: str,
    request: Request,
    store: InterviewSessionRepository = Depends(dependencies.get_session_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        _raise_value_error(exc)
    workflow = dependencies.get_interview_workflow_service()
    return StreamingResponse(
        workflow.event_stream.iter_sse(
            session_id,
            command_id,
            after_event_id=request.headers.get("Last-Event-ID"),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
__all__ = [
    "_publish_round_closed_event",
    "delete_interview_draft",
    "finish_interview",
    "get_interview_draft",
    "get_interview_session",
    "get_legacy_launch_session_store",
    "get_request_interview_launch_coordinator",
    "list_agent_runs",
    "list_runtime_events",
    "router",
    "save_interview_draft",
    "skip_interview_question",
    "start_interview",
    "stream_interview_command",
    "submit_answer",
    "submit_answer_stream",
]
