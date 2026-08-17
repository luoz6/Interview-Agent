export const RAG_LAB_ROUTES = Object.freeze({
  overview: "/rag/lab",
  retrieval: "/rag/lab/retrieval",
  evaluation: "/rag/lab/evaluation",
  evidenceTrace: "/rag/lab/evidence-trace",
  corpus: "/rag/lab/corpus",
});

export const RAG_LAB_PATHS = Object.freeze(Object.values(RAG_LAB_ROUTES));

const LEGACY_RAG_ROUTES = Object.freeze({
  "/rag": RAG_LAB_ROUTES.overview,
  "/rag/retrieval": RAG_LAB_ROUTES.retrieval,
  "/rag/evaluation": RAG_LAB_ROUTES.evaluation,
  "/rag/evidence-trace": RAG_LAB_ROUTES.evidenceTrace,
  "/rag/corpus": RAG_LAB_ROUTES.corpus,
});

export function canonicalRagLabPath(pathname) {
  return LEGACY_RAG_ROUTES[pathname] || null;
}
