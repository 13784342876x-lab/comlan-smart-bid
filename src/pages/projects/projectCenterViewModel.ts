import type { ProjectSummaryDTO } from "../../types/api";

export function projectOverviewTarget(projectId: string) {
  return `/projects/${encodeURIComponent(projectId)}`;
}

export function activatesProjectCard(key: string) {
  return key === "Enter" || key === " ";
}

export function withoutProject(projects: ProjectSummaryDTO[], projectId: string) {
  return projects.filter((project) => project.project_id !== projectId);
}
