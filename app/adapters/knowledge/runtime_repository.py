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


class RuntimeKnowledgeRepository:
    """Compatibility repository backed by the application retrieval coordinator."""

    def __init__(self, repository, coordinator, settings) -> None:
        self._repository = repository
        self._coordinator = coordinator
        self._settings = settings

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
    ):
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
        )
        outcome = self._coordinator.retrieve(
            request,
            legacy_profile=legacy_profile,
            candidate_profile=candidate_profile,
        )
        if outcome.result.availability == RetrievalAvailability.UNAVAILABLE:
            raise RuntimeError("knowledge retrieval is unavailable")
        return outcome

    def inspect_retrieval(self, request, *, profile, engine: str):
        """Run an explicitly selected diagnostic engine without rollout mutation."""

        return self._coordinator.inspect(request, profile=profile, engine=engine)

    def get_by_ids(self, ids, *, expected_hashes=None):
        return self._repository.get_by_ids(ids, expected_hashes=expected_hashes)

    def __getattr__(self, name):
        return getattr(self._repository, name)
