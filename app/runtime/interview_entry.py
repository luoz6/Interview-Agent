from __future__ import annotations

from app.application.interview.launch_prepared_interview import (
    LaunchPreparedInterview,
)
def build_launch_prepared_interview(
    *,
    session_repository=None,
    prep_plan_store=None,
    launch_repository=None,
    scheduler_entry=None,
) -> LaunchPreparedInterview:
    from app.adapters.memory.interview_entry import (
        MemoryClock,
        MemoryDurableExecutionAdapter,
        MemoryIdGenerator,
        MemoryInterviewLaunchRepositoryAdapter,
        MemoryInterviewSessionRepositoryAdapter,
        MemoryPrepPlanRepositoryAdapter,
    )
    from app.adapters.persistence.postgres.interview_entry import (
        PostgresClock,
        PostgresDurableExecutionAdapter,
        PostgresIdGenerator,
        PostgresInterviewLaunchRepositoryAdapter,
        PostgresInterviewSessionRepositoryAdapter,
        PostgresPrepPlanRepositoryAdapter,
    )
    from app.runtime.composition import (
        get_interview_launch_repository,
        get_interview_workflow_service,
        get_prep_plan_store,
        get_session_store,
        get_scheduler_production_entry,
    )

    session_repository = session_repository or get_session_store()
    prep_plan_store = prep_plan_store or get_prep_plan_store()
    launch_repository = launch_repository or get_interview_launch_repository()
    scheduler_entry = scheduler_entry or get_scheduler_production_entry()

    if getattr(session_repository, "durability", None) == "postgres":
        session_adapter = PostgresInterviewSessionRepositoryAdapter(
            session_repository,
            scheduler_entry=scheduler_entry,
        )
        return LaunchPreparedInterview(
            prep_plan_repository=PostgresPrepPlanRepositoryAdapter(
                prep_plan_store
            ),
            launch_repository=PostgresInterviewLaunchRepositoryAdapter(
                launch_repository
            ),
            session_repository=session_adapter,
            durable_execution=PostgresDurableExecutionAdapter(
                workflow_service=get_interview_workflow_service(),
                session_repository=session_adapter,
            ),
            clock=PostgresClock(),
            id_generator=PostgresIdGenerator(),
        )

    session_adapter = MemoryInterviewSessionRepositoryAdapter(
        session_repository,
        scheduler_entry=scheduler_entry,
    )
    return LaunchPreparedInterview(
        prep_plan_repository=MemoryPrepPlanRepositoryAdapter(prep_plan_store),
        launch_repository=MemoryInterviewLaunchRepositoryAdapter(
            launch_repository
        ),
        session_repository=session_adapter,
        durable_execution=MemoryDurableExecutionAdapter(),
        clock=MemoryClock(),
        id_generator=MemoryIdGenerator(),
    )


__all__ = ["build_launch_prepared_interview"]
