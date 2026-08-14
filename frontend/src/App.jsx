import { lazy, Suspense } from "react";
import { RouteLoadBoundary, RouteLoadingFallback } from "./components/RouteLoadBoundary";
import { NotFoundPage } from "./pages/NotFoundPage";

function lazyNamedPage(loader, exportName) {
  return lazy(() => loader().then((module) => ({ default: module[exportName] })));
}

const StartPage = lazyNamedPage(() => import("./pages/StartPage"), "StartPage");
const InterviewPage = lazyNamedPage(() => import("./pages/InterviewPage"), "InterviewPage");
const ReportProcessingPage = lazyNamedPage(
  () => import("./pages/ReportProcessingPage"),
  "ReportProcessingPage",
);
const ReportDetailPage = lazyNamedPage(() => import("./pages/ReportDetailPage"), "ReportDetailPage");
const ReportsPage = lazyNamedPage(() => import("./pages/ReportsPage"), "ReportsPage");
const HelpPage = lazyNamedPage(() => import("./pages/HelpPage"), "HelpPage");
const MemoryCenterPage = lazyNamedPage(
  () => import("./pages/MemoryCenterPage"),
  "MemoryCenterPage",
);
const RagConsolePage = lazyNamedPage(() => import("./pages/RagConsolePage"), "RagConsolePage");

const routes = {
  "/": StartPage,
  "/prep": StartPage,
  "/interview": InterviewPage,
  "/report-processing": ReportProcessingPage,
  "/report-detail": ReportDetailPage,
  "/reports": ReportsPage,
  "/help": HelpPage,
  "/memory-center": MemoryCenterPage,
  "/memory-center.html": MemoryCenterPage,
};

export default function App() {
  const Page = window.location.pathname.startsWith("/rag")
    ? RagConsolePage
    : routes[window.location.pathname] || NotFoundPage;
  return (
    <RouteLoadBoundary>
      <Suspense fallback={<RouteLoadingFallback />}>
        <Page />
      </Suspense>
    </RouteLoadBoundary>
  );
}
