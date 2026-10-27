import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { getProjectOverview } from "../../api/projects";
import { useApiQuery } from "../../api/useApiQuery";
import type { ProjectOverviewDTO, VersionRefDTO } from "../../types/api";

interface ProjectContextValue {
  projectId: string;
  projectName: string;
  currentRevision: VersionRefDTO;
  currentReviewedVersion: VersionRefDTO;
  workflowStatus: string;
  releaseStatus: string;
  overview: ProjectOverviewDTO;
}

interface ProjectLoadContextValue {
  projectId: string;
  data: ProjectContextValue | null;
  loading: boolean;
  error: Error | null;
  reload: () => void;
}

const ProjectContext = createContext<ProjectLoadContextValue | null>(null);

export function ProjectProvider({ children }: { children: ReactNode }) {
  const { projectId = "" } = useParams();
  const query = useApiQuery((signal) => getProjectOverview(projectId, signal), [projectId]);
  const generationActive = query.data?.current_pipeline_run?.full_bid_generation_active ?? false;
  useEffect(() => {
    if (!generationActive) return;
    const timer = window.setInterval(query.reload, 3000);
    return () => window.clearInterval(timer);
  }, [generationActive, query.reload]);
  const value = useMemo<ProjectLoadContextValue>(() => {
    if (!query.data) return { projectId, data: null, loading: query.loading, error: query.error, reload: query.reload };
    const project = query.data.project;
    return {
      projectId,
      loading: query.loading,
      error: query.error,
      reload: query.reload,
      data: {
        projectId,
        projectName: project.project_name,
        currentRevision: project.current_revision,
        currentReviewedVersion: project.current_reviewed_version,
        workflowStatus: project.workflow_status,
        releaseStatus: project.release_status,
        overview: query.data,
      },
    };
  }, [projectId, query.data, query.error, query.loading, query.reload]);
  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

export function useProjectLoad() {
  const context = useContext(ProjectContext);
  if (!context) throw new Error("ProjectProvider is missing");
  return context;
}

export function useProject() {
  const context = useProjectLoad();
  if (!context.data) throw new Error("Project data is not ready");
  return context.data;
}
