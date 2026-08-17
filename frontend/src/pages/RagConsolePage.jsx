import { LegacyRouteRedirect } from "../components/LegacyRouteRedirect";
import { NotFoundPage } from "./NotFoundPage";
import { RagCorpusPage } from "./RagCorpusPage";
import { RagEvaluationPage } from "./RagEvaluationPage";
import { RagEvidenceTracePage } from "./RagEvidenceTracePage";
import { RagOverviewPage } from "./RagOverviewPage";
import { RagRetrievalPage } from "./RagRetrievalPage";
import { canonicalRagLabPath, RAG_LAB_ROUTES } from "../rag/ragRoutes";

const pages = {
  [RAG_LAB_ROUTES.overview]: RagOverviewPage,
  [RAG_LAB_ROUTES.retrieval]: RagRetrievalPage,
  [RAG_LAB_ROUTES.evaluation]: RagEvaluationPage,
  [RAG_LAB_ROUTES.evidenceTrace]: RagEvidenceTracePage,
  [RAG_LAB_ROUTES.corpus]: RagCorpusPage,
};

export function RagConsolePage() {
  const pathname = window.location.pathname;
  const redirectTo = canonicalRagLabPath(pathname);
  if (redirectTo) return <LegacyRouteRedirect to={redirectTo} />;

  const Page = pages[pathname] || NotFoundPage;
  return <Page />;
}
