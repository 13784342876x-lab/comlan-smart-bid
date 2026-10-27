import { getJson, requestJson } from "./client";
import type {
  AIWritingReadinessDTO,
  PipelineRunDTO,
  WritingGenerationDTO,
  WritingGenerationListDTO,
  WritingPlanDTO,
} from "../types/api";

export const getWritingPlan = (projectId: string, signal?: AbortSignal) =>
  getJson<WritingPlanDTO>(`/api/projects/${encodeURIComponent(projectId)}/writing-plan`, signal);

export const getAIWritingReadiness = (projectId: string, signal?: AbortSignal) =>
  getJson<AIWritingReadinessDTO>(
    `/api/projects/${encodeURIComponent(projectId)}/writing-readiness`,
    signal,
  );

export const generateWritingPlan = (projectId: string) =>
  requestJson<PipelineRunDTO>(
    `/api/projects/${encodeURIComponent(projectId)}/writing-plan/generate`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
  ).then((value) => value.data);

export const getWritingGenerations = (projectId: string, signal?: AbortSignal) =>
  getJson<WritingGenerationListDTO>(
    `/api/projects/${encodeURIComponent(projectId)}/writing/generations`,
    signal,
  );

export const getWritingGeneration = (
  projectId: string,
  generationId: string,
  signal?: AbortSignal,
) => getJson<WritingGenerationDTO>(
  `/api/projects/${encodeURIComponent(projectId)}/writing/generations/${encodeURIComponent(generationId)}`,
  signal,
);

export const generateWritingTask = (
  projectId: string,
  taskId: string,
  expectedInputFingerprint: string,
) => requestJson<WritingGenerationDTO>(
  `/api/projects/${encodeURIComponent(projectId)}/writing/tasks/${encodeURIComponent(taskId)}/generate`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_input_fingerprint: expectedInputFingerprint }),
  },
).then((value) => value.data);
