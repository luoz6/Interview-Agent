from __future__ import annotations

from app.application.knowledge.retrieval_profiles import (
    compatibility_profile,
    resolve_runtime_profile,
)
from app.domain.knowledge.retrieval import (
    RetrievalAvailability,
    RetrievalHardConstraints,
    RetrievalIntent,
    RetrievalRequest,
    RetrievalRoutingHints,
)
from app.domain.knowledge.source_scope import (
    KnowledgeSourceScope,
    build_knowledge_source_scope,
)


class RuntimeKnowledgeRepository:
    """Compatibility repository backed by the application retrieval coordinator."""

    def __init__(
        self,
        repository,
        coordinator,
        settings,
        *,
        session_store_factory=None,
        principal_identity_resolver_factory=None,
        materials_settings_factory=None,
    ) -> None:
        self._repository = repository
        self._coordinator = coordinator
        self._settings = settings
        self._session_store_factory = session_store_factory
        self._principal_identity_resolver_factory = (
            principal_identity_resolver_factory
        )
        self._materials_settings_factory = materials_settings_factory

    def close(self) -> None:
        self._coordinator.close()

    def search(
        self,
        query_text,
        *,
        job_tags,
        source_types=None,
        domains=None,
        limit=5,
    ):
        kwargs = {
            "job_tags": job_tags,
            "source_types": source_types,
            "limit": limit,
        }
        if domains is not None:
            kwargs["domains"] = domains
        return self._repository.search(query_text, **kwargs)

    def search_runtime(
        self,
        query_text: str,
        *,
        intent: RetrievalIntent,
        job_tags: list[str],
        source_types: list[str] | None = None,
        limit: int = 5,
        session_id: str | None = None,
        question_id: str | None = None,
        prep_run_id: str | None = None,
        source_scope: KnowledgeSourceScope | None = None,
    ):
        resolved_source_scope = self._resolve_source_scope(
            intent=intent,
            session_id=session_id,
            source_scope=source_scope,
        )
        candidate_profile = resolve_runtime_profile(
            intent,
            self._settings,
            evidence_limit=limit,
        )
        legacy_profile = compatibility_profile(
            minimum_score=self._settings.minimum_score,
            evidence_limit=limit,
        )
        request = RetrievalRequest(
            query_text=query_text,
            intent=intent,
            hard_constraints=RetrievalHardConstraints(
                source_types=tuple(source_types or ())
            ),
            routing_hints=RetrievalRoutingHints(
                canonical_tags=tuple(job_tags)
            ),
            profile_id=candidate_profile.profile_id,
            session_id=session_id,
            question_id=question_id,
            prep_run_id=prep_run_id,
            source_scope=resolved_source_scope,
        )
        outcome = self._coordinator.retrieve(
            request,
            legacy_profile=legacy_profile,
            candidate_profile=candidate_profile,
        )
        if outcome.result.availability == RetrievalAvailability.UNAVAILABLE:
            raise RuntimeError("knowledge retrieval is unavailable")
        return outcome

    def _resolve_source_scope(
        self,
        *,
        intent: RetrievalIntent,
        session_id: str | None,
        source_scope: KnowledgeSourceScope | None,
    ) -> KnowledgeSourceScope | None:
        if session_id is None:
            if source_scope is None:
                return None
            resolved = KnowledgeSourceScope.model_validate(
                source_scope.model_dump(mode="json")
            )
            self._require_user_materials_enabled(resolved.selected_documents)
            return resolved
        if self._session_store_factory is None:
            raise RuntimeError("session-scoped knowledge retrieval is unavailable")
        from app.services.interview_plan_revision import InterviewPlanV2
        from app.services.session_plan_binding import session_plan_binding_from_state

        state = self._session_store_factory().get(session_id)
        binding = session_plan_binding_from_state(state)
        if binding.plan_origin != "plan_revision":
            return None
        plan = InterviewPlanV2.model_validate(binding.plan_snapshot)
        snapshot = plan.knowledge_scope
        self._require_user_materials_enabled(snapshot.selected_documents)
        owner_principal_id = None
        if snapshot.selected_documents:
            if self._principal_identity_resolver_factory is None:
                raise RuntimeError("session principal is unavailable")
            identity = self._principal_identity_resolver_factory().resolve()
            if identity is None:
                raise RuntimeError("session principal is unavailable")
            owner_principal_id = identity.principal_id
        return build_knowledge_source_scope(
            snapshot,
            owner_principal_id=owner_principal_id,
            usage=_usage_for_intent(intent),
        )

    def _require_user_materials_enabled(self, selected_documents) -> None:
        if not selected_documents:
            return
        try:
            settings = (
                self._materials_settings_factory()
                if self._materials_settings_factory is not None
                else None
            )
            enabled = (
                settings
                if isinstance(settings, bool)
                else bool(getattr(settings, "enabled", False))
            )
        except Exception:
            enabled = False
        if not enabled:
            raise RuntimeError("user materials retrieval is disabled")

    def inspect_retrieval(self, request, *, profile, engine: str):
        """Run an explicitly selected diagnostic engine without rollout mutation."""

        return self._coordinator.inspect(request, profile=profile, engine=engine)

    def get_by_ids(self, ids, *, expected_hashes=None):
        return self._repository.get_by_ids(ids, expected_hashes=expected_hashes)

    def __getattr__(self, name):
        return getattr(self._repository, name)


def _usage_for_intent(intent: RetrievalIntent):
    if intent == RetrievalIntent.PREP:
        return "question"
    if intent == RetrievalIntent.FOLLOWUP:
        return "follow_up"
    return "feedback"
