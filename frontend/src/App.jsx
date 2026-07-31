import { HelpPage } from "./pages/HelpPage";
import { InterviewPage } from "./pages/InterviewPage";
import { StartPage } from "./pages/StartPage";
import { ReportDetailPage } from "./pages/ReportDetailPage";
import { ReportProcessingPage } from "./pages/ReportProcessingPage";
import { ReportsPage } from "./pages/ReportsPage";
import { NotFoundPage } from "./pages/NotFoundPage";

const routes = {
  "/": StartPage,
  "/prep": StartPage,
  "/interview": InterviewPage,
  "/report-processing": ReportProcessingPage,
  "/report-detail": ReportDetailPage,
  "/reports": ReportsPage,
  "/help": HelpPage,
};

export default function App() {
  const Page = routes[window.location.pathname] || NotFoundPage;
  return <Page />;
}
