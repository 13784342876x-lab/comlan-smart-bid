import { getJson } from "./client";
import type { AuditedFulltextDTO } from "../types/api";

export const getAuditedFulltext = (projectId: string, signal?: AbortSignal) =>
  getJson<AuditedFulltextDTO>(`/api/projects/${encodeURIComponent(projectId)}/fulltext`, signal);
