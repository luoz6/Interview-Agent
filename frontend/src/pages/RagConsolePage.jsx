import { RagCorpusPage } from "./RagCorpusPage";
import { RagEvaluationPage } from "./RagEvaluationPage";
import { RagEvidenceTracePage } from "./RagEvidenceTracePage";
import { RagOverviewPage } from "./RagOverviewPage";
import { RagRetrievalPage } from "./RagRetrievalPage";

const pages = {
  "/rag": RagOverviewPage,
  "/rag/retrieval": RagRetrievalPage,
  "/rag/evaluation": RagEvaluationPage,
  "/rag/evidence-trace": RagEvidenceTracePage,
  "/rag/corpus": RagCorpusPage,
};

export function RagConsolePage() {
  const Page = pages[window.location.pathname] || RagOverviewPage;
  return <Page />;
}
