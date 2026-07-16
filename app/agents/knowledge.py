import inspect

from app.ports.runtime import KnowledgeRepository
from app.services.job_tags import extract_job_tags
from app.services.knowledge_grounding import (
    attach_grounded_prep_context,
    degraded_grounding,
    provider_knowledge_context,
    retrieve_grounding,
)
from app.services.knowledge_profile import build_role_profile
from app.services.knowledge_query import build_knowledge_queries
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan, attach_prep_context


class KnowledgeAgent:
    def __init__(
        self,
        llm: InterviewLLM | None = None,
        vector_store: KnowledgeRepository | None = None,
    ) -> None:
        self.llm = llm
        self.vector_store = vector_store

    def generate_plan(
        self,
        *,
        job_description: str,
        resume_text: str,
        prep_run_id: str | None = None,
    ) -> InterviewPlan:
        llm = self.llm or self._default_llm()
        vector_store = self.vector_store
        if vector_store is None and self.llm is not None:
            plan = llm.generate_plan(job_description, resume_text)
            return attach_prep_context(
                plan,
                job_description=job_description,
                resume_text=resume_text,
                job_tags=extract_job_tags(job_description),
            )

        role_profile = build_role_profile(job_description, resume_text)
        queries = build_knowledge_queries(role_profile)
        try:
            repository = vector_store or self._default_vector_store()
            grounding = retrieve_grounding(queries, repository)
        except Exception:
            grounding = degraded_grounding(queries, "knowledge_unavailable")
        plan = self._generate_provider_plan(
            llm,
            job_description=job_description,
            resume_text=resume_text,
            knowledge_context=provider_knowledge_context(grounding),
        )
        grounded_plan = attach_grounded_prep_context(
            plan,
            role_profile=role_profile,
            result=grounding,
            prep_run_id=prep_run_id,
        )
        self._record_grounding_trace(grounded_plan, grounding)
        return grounded_plan

    @staticmethod
    def _generate_provider_plan(
        llm,
        *,
        job_description: str,
        resume_text: str,
        knowledge_context: list[dict],
    ) -> InterviewPlan:
        try:
            signature = inspect.signature(llm.generate_plan)
            supports_context = "knowledge_context" in signature.parameters or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
        except (TypeError, ValueError):
            supports_context = False
        if supports_context:
            return llm.generate_plan(
                job_description,
                resume_text,
                knowledge_context=knowledge_context,
            )
        return llm.generate_plan(job_description, resume_text)

    @staticmethod
    def _default_vector_store() -> KnowledgeRepository:
        from app.services.vector_store import get_knowledge_store

        return get_knowledge_store()

    @staticmethod
    def _record_grounding_trace(plan: InterviewPlan, grounding) -> None:
        snapshot = plan.prep_context.binding_snapshot if plan.prep_context else None
        if snapshot is None:
            return
        try:
            from app.services.knowledge_trace import KnowledgeTraceRecorder

            KnowledgeTraceRecorder.from_env().record(
                prep_run_id=snapshot.prep_run_id,
                stage="prep_retrieval",
                payload={
                    "status": grounding.status,
                    "degraded_reason": grounding.degraded_reason,
                    "queries": [
                        {
                            "query_id": retrieval.query.query_id,
                            "topic_id": retrieval.query.topic_id,
                            "query_text": retrieval.query.query_text,
                            "filters": retrieval.query.filters,
                            "source_types": retrieval.query.source_types,
                            "top_k": retrieval.query.top_k,
                            "latency_ms": retrieval.latency_ms,
                            "hit_ids": [
                                chunk.chunk_id for chunk in retrieval.chunks
                            ],
                            "scores": {
                                chunk.chunk_id: chunk.score
                                for chunk in retrieval.chunks
                            },
                            "status": retrieval.status,
                            "degraded_reason": retrieval.degraded_reason,
                        }
                        for retrieval in grounding.retrievals
                    ],
                },
            )
        except Exception:
            return

    @staticmethod
    def _default_llm() -> InterviewLLM:
        from app.services.llm import OpenAIInterviewLLM

        return OpenAIInterviewLLM()
