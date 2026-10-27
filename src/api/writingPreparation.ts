import { getJson, requestJson } from "./client";
import type { HumanConfirmationCommandDTO, HumanConfirmationListDTO, HumanConfirmationMutationResultDTO, HumanConfirmationRequestDTO, ProjectFactCommandDTO, ProjectFactMutationResultDTO, WritingContextItemDTO, WritingPreparationDTO, WritingPreparationItemDTO } from "../types/api";

const base = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/writing-preparation`;

export const getWritingPreparation = (projectId: string, signal?: AbortSignal) =>
  getJson<WritingPreparationDTO>(base(projectId), signal);

export const getWritingPreparationItem = (projectId: string, itemId: string, signal?: AbortSignal) =>
  getJson<WritingPreparationItemDTO>(`${base(projectId)}/items/${encodeURIComponent(itemId)}`, signal);

export const getWritingContext = (projectId: string, signal?: AbortSignal) =>
  getJson<WritingContextItemDTO[]>(`/api/projects/${encodeURIComponent(projectId)}/writing-context`, signal);

export const confirmProjectFact = (projectId: string, factId: string, token: string, body: ProjectFactCommandDTO) =>
  requestJson<ProjectFactMutationResultDTO>(`${base(projectId)}/facts/${encodeURIComponent(factId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "If-Match": token },
    body: JSON.stringify(body),
  });

export const getHumanConfirmations = (projectId: string, signal?: AbortSignal) =>
  getJson<HumanConfirmationListDTO>(`/api/projects/${encodeURIComponent(projectId)}/confirmations`, signal);

export const getHumanConfirmation = (projectId: string, confirmationId: string, signal?: AbortSignal) =>
  getJson<HumanConfirmationRequestDTO>(`/api/projects/${encodeURIComponent(projectId)}/confirmations/${encodeURIComponent(confirmationId)}`, signal);

export const decideHumanConfirmation = (projectId: string, confirmationId: string, token: string, body: HumanConfirmationCommandDTO) =>
  requestJson<HumanConfirmationMutationResultDTO>(`/api/projects/${encodeURIComponent(projectId)}/confirmations/${encodeURIComponent(confirmationId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "If-Match": token },
    body: JSON.stringify(body),
  });
