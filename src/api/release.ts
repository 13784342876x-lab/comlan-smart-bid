import { apiUrl, getJson, requestJson } from "./client";
import type { ReleaseStatusDTO } from "../types/api";

export function getRelease(projectId: string, signal?: AbortSignal) {
  return getJson<ReleaseStatusDTO>(`/api/projects/${encodeURIComponent(projectId)}/release`, signal);
}

export function publishRelease(projectId: string) {
  return requestJson<ReleaseStatusDTO>(`/api/projects/${encodeURIComponent(projectId)}/release`, {
    method: "POST",
  });
}

export function releaseDownloadUrl(projectId: string, kind: "docx" | "pdf") {
  return apiUrl(`/api/projects/${encodeURIComponent(projectId)}/release/${kind}`);
}
