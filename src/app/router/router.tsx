import { Navigate, createBrowserRouter } from "react-router-dom";
import { ProjectProvider } from "../providers/ProjectProvider";
import { AppLayout } from "../../layouts/AppLayout";
import { ProjectLayout } from "../../layouts/ProjectLayout";
import { ProjectCenterPage } from "../../pages/projects/ProjectCenterPage";
import { NewProjectPage } from "../../pages/projects/NewProjectPage";
import { ProjectOverviewPage } from "../../pages/overview/ProjectOverviewPage";
import { RequirementMatrixPage } from "../../pages/requirements/RequirementMatrixPage";
import { TenderAnalysisPage } from "../../pages/requirements/TenderAnalysisPage";
import { ReviewWorkspacePage } from "../../pages/review/ReviewWorkspacePage";
import { FactsMaterialsSummaryPage } from "../../pages/shells/FactsMaterialsSummaryPage";
import { EnterpriseKnowledgePage, MyWorkPage } from "../../pages/shells/GlobalShellPage";
import { ScoringSummaryPage } from "../../pages/shells/ScoringSummaryPage";
import { SectionShellPage } from "../../pages/shells/SectionShellPage";
import { VersionsSummaryPage } from "../../pages/shells/VersionsSummaryPage";
import { AuditedFulltextPage } from "../../pages/fulltext/AuditedFulltextPage";
import { TenderImportPage } from "../../pages/setup/TenderImportPage";
import { PipelineProgressPage } from "../../pages/setup/PipelineProgressPage";
import { AnalysisLayout } from "../../layouts/AnalysisLayout";
import { AnalysisFilesPage } from "../../pages/analysis/AnalysisFilesPage";
import { AnalysisScoringPage } from "../../pages/analysis/AnalysisScoringPage";
import { AnalysisParametersPage } from "../../pages/analysis/AnalysisParametersPage";
import { AnalysisRisksPage } from "../../pages/analysis/AnalysisRisksPage";
import { WritingPreparationPage } from "../../pages/preparation/WritingPreparationPage";
import { WritingPlanPage } from "../../pages/writing/WritingPlanPage";
import { AIWritingBoundaryPage } from "../../pages/writing/AIWritingBoundaryPage";
import { ReleasePage } from "../../pages/release/ReleasePage";
import { RouterErrorPage } from "./ErrorPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppLayout />,
    errorElement: <RouterErrorPage />,
    children: [
      { index: true, element: <Navigate to="/projects" replace /> },
      { path: "projects", element: <ProjectCenterPage /> },
      { path: "projects/new", element: <NewProjectPage /> },
      { path: "work", element: <MyWorkPage /> },
      { path: "knowledge", element: <EnterpriseKnowledgePage /> },
      {
        path: "projects/:projectId",
        element: <ProjectProvider><ProjectLayout /></ProjectProvider>,
        children: [
          { index: true, element: <ProjectOverviewPage /> },
          { path: "setup", element: <TenderImportPage /> },
          { path: "runs/:runId", element: <PipelineProgressPage /> },
          { path: "writing-preparation", element: <WritingPreparationPage /> },
          {
            path: "analysis",
            element: <AnalysisLayout />,
            children: [
              { index: true, element: <TenderAnalysisPage /> },
              { path: "files", element: <AnalysisFilesPage /> },
              { path: "requirements", element: <RequirementMatrixPage /> },
              { path: "requirements/:requirementId", element: <RequirementMatrixPage /> },
              { path: "scoring", element: <AnalysisScoringPage /> },
              { path: "parameters", element: <AnalysisParametersPage /> },
              { path: "risks", element: <AnalysisRisksPage /> },
            ],
          },
          { path: "writing-plan", element: <WritingPlanPage /> },
          { path: "writing", element: <AIWritingBoundaryPage /> },
          { path: "fulltext", element: <AuditedFulltextPage /> },
          { path: "review", element: <ReviewWorkspacePage /> },
          { path: "scoring", element: <ScoringSummaryPage /> },
          { path: "facts-materials", element: <FactsMaterialsSummaryPage /> },
          { path: "revisions", element: <SectionShellPage title="修改中心" description="审核完成后的多轮修改工作区。" boundary="Revision Round / Task / Result / Diff 等待正式 contract，本轮不发明生产字段。" /> },
          { path: "versions", element: <VersionsSummaryPage /> },
          { path: "release", element: <ReleasePage /> },
        ],
      },
      { path: "*", element: <Navigate to="/projects" replace /> },
    ],
  },
]);
