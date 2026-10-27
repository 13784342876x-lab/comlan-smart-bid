import { getJson, requestJson } from "./client";
import type {
  FactMaterialSummaryDTO,
  ProjectListDTO,
  ProjectOverviewDTO,
  RequirementDetailDTO,
  RequirementListDTO,
  ReviewWorkspaceDTO,
  ScoringSummaryDTO,
  VersionSummaryDTO,
  ProjectCreateDTO,
  ProjectDetailDTO,
  SourceFileDTO,
  SourceFileListDTO,
  SourceFileRole,
  PipelineRunDTO,
  PipelineRunListDTO,
  TenderAnalysisSummaryDTO,
} from "../types/api";

const projectPath = (projectId: string, resource: string) =>
  `/api/projects/${encodeURIComponent(projectId)}/${resource}`;

export const getProjects = (signal?: AbortSignal) => getJson<ProjectListDTO>("/api/projects", signal);
export const createProject = (body: ProjectCreateDTO) => requestJson<ProjectDetailDTO>("/api/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((value) => value.data);
export const getProject = (projectId: string, signal?: AbortSignal) => getJson<ProjectDetailDTO>(`/api/projects/${encodeURIComponent(projectId)}`, signal);
export const deleteProject = (projectId: string) => requestJson<null>(`/api/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" }).then(() => undefined);
export const getProjectFiles = (projectId: string, signal?: AbortSignal) => getJson<SourceFileListDTO>(projectPath(projectId, "files"), signal);
export const uploadProjectFile = (projectId: string, file: File) => requestJson<SourceFileDTO>(`${projectPath(projectId, "files")}?filename=${encodeURIComponent(file.name)}`, { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file }).then((value) => value.data);
export const updateProjectFileRole = (projectId: string, fileId: string, role: SourceFileRole) => requestJson<SourceFileDTO>(`${projectPath(projectId, "files")}/${encodeURIComponent(fileId)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role }) }).then((value) => value.data);
export const deleteProjectFile = (projectId: string, fileId: string) => requestJson<null>(`${projectPath(projectId, "files")}/${encodeURIComponent(fileId)}`, { method: "DELETE" }).then(() => undefined);
export const startProjectRun = (projectId: string, lotName?: string) => requestJson<PipelineRunDTO>(projectPath(projectId, "runs"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lot_name: lotName?.trim() || null }) }).then((value) => value.data);
export const getProjectRuns = (projectId: string, signal?: AbortSignal) => getJson<PipelineRunListDTO>(projectPath(projectId, "runs"), signal);
export const getProjectRun = (projectId: string, runId: string, signal?: AbortSignal) => getJson<PipelineRunDTO>(`${projectPath(projectId, "runs")}/${encodeURIComponent(runId)}`, signal);
export const resumeProjectRun = (projectId: string, runId: string) => requestJson<PipelineRunDTO>(`${projectPath(projectId, "runs")}/${encodeURIComponent(runId)}/resume`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then((value) => value.data);
export const startFullBidGeneration = (projectId: string) => requestJson<PipelineRunDTO>(projectPath(projectId, "full-bid-generation"), { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then((value) => value.data);
export const getTenderAnalysisSummary = (projectId: string, signal?: AbortSignal) => getJson<TenderAnalysisSummaryDTO>(projectPath(projectId, "analysis-summary"), signal);
export const getProjectOverview = (projectId: string, signal?: AbortSignal) =>
  getJson<ProjectOverviewDTO>(projectPath(projectId, "overview"), signal);
export const getProjectReview = (projectId: string, signal?: AbortSignal) =>
  getJson<ReviewWorkspaceDTO>(projectPath(projectId, "review"), signal);
export const getProjectRequirements = (projectId: string, signal?: AbortSignal) =>
  getJson<RequirementListDTO>(projectPath(projectId, "requirements"), signal);
export const getProjectRequirement = (projectId: string, requirementId: string, signal?: AbortSignal) =>
  getJson<RequirementDetailDTO>(
    `${projectPath(projectId, "requirements")}/${encodeURIComponent(requirementId)}`,
    signal,
  );
export const getProjectScoring = (projectId: string, signal?: AbortSignal) =>
  getJson<ScoringSummaryDTO>(projectPath(projectId, "scoring"), signal);
export const getProjectFactsMaterials = (projectId: string, signal?: AbortSignal) =>
  getJson<FactMaterialSummaryDTO>(projectPath(projectId, "facts-materials"), signal);
export const getProjectVersions = (projectId: string, signal?: AbortSignal) =>
  getJson<VersionSummaryDTO>(projectPath(projectId, "versions"), signal);
