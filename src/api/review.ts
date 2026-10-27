import { requestJson, type ApiResponse } from "./client";
import type {
  FactConfirmationCommandDTO,
  IssueDecisionCommandDTO,
  MaterialVerificationCommandDTO,
  ReviewMutationResultDTO,
  RevisionOperationDTO,
  ReviewWorkspaceDTO,
  RiskAcceptanceCommandDTO,
} from "../types/api";

const reviewPath = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/review`;
const sourcePath = (projectId: string, kind: string, sourceId: string) =>
  `${reviewPath(projectId)}/${kind}/${encodeURIComponent(sourceId)}`;

export const getVersionedReview = (projectId: string, signal?: AbortSignal): Promise<ApiResponse<ReviewWorkspaceDTO>> =>
  requestJson(reviewPath(projectId), { method: "GET", signal });

function mutate<T>(path: string, token: string, body: T, method: "PATCH" | "POST") {
  return requestJson<ReviewMutationResultDTO>(path, {
    method,
    headers: { "Content-Type": "application/json", "If-Match": token },
    body: JSON.stringify(body),
  });
}

export const decideIssue = (projectId: string, sourceId: string, token: string, body: IssueDecisionCommandDTO) =>
  mutate(sourcePath(projectId, "issues", sourceId), token, body, "PATCH");

export const confirmFact = (projectId: string, sourceId: string, token: string, body: FactConfirmationCommandDTO) =>
  mutate(sourcePath(projectId, "facts", sourceId), token, body, "PATCH");

export const verifyMaterial = (projectId: string, sourceId: string, token: string, body: MaterialVerificationCommandDTO) =>
  mutate(sourcePath(projectId, "materials", sourceId), token, body, "PATCH");

export const acceptRisk = (projectId: string, sourceId: string, token: string, body: RiskAcceptanceCommandDTO) =>
  mutate(`${sourcePath(projectId, "issues", sourceId)}/risk-acceptances`, token, body, "POST");

export const getCurrentAIModify = (projectId: string, signal?: AbortSignal) =>
  requestJson<RevisionOperationDTO | null>(`${reviewPath(projectId)}/ai-modify`, {
    method: "GET",
    signal,
  }).then((value) => value.data);

export const startAIModify = (
  projectId: string,
  sourceId: string,
  sourceType: string,
  token: string,
) => requestJson<RevisionOperationDTO>(
  `${sourcePath(projectId, "issues", sourceId)}/ai-modify`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json", "If-Match": token },
    body: JSON.stringify({ source_type: sourceType }),
  },
).then((value) => value.data);
