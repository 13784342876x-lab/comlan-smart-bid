import { getJson } from "./client";
import type {
  AnalysisSourceFileDTO,
  TenderAnalysisOverviewDTO,
  TenderParametersDTO,
  TenderRisksPreparationDTO,
  TenderScoringRuleDTO,
  TenderScoringRulesDTO,
} from "../types/api";

const analysisPath = (projectId: string, resource = "") => {
  const base = `/api/projects/${encodeURIComponent(projectId)}/analysis`;
  return resource ? `${base}/${resource}` : base;
};

export const getTenderAnalysis = (projectId: string, signal?: AbortSignal) =>
  getJson<TenderAnalysisOverviewDTO>(analysisPath(projectId), signal);

export const getTenderAnalysisFiles = (projectId: string, signal?: AbortSignal) =>
  getJson<AnalysisSourceFileDTO[]>(analysisPath(projectId, "files"), signal);

export const getTenderScoringRules = (projectId: string, signal?: AbortSignal) =>
  getJson<TenderScoringRulesDTO>(analysisPath(projectId, "scoring"), signal);

export const getTenderScoringRule = (projectId: string, scoreId: string, signal?: AbortSignal) =>
  getJson<TenderScoringRuleDTO>(analysisPath(projectId, `scoring/${encodeURIComponent(scoreId)}`), signal);

export const getTenderParameters = (projectId: string, signal?: AbortSignal) =>
  getJson<TenderParametersDTO>(analysisPath(projectId, "parameters"), signal);

export const getTenderRisksPreparation = (projectId: string, signal?: AbortSignal) =>
  getJson<TenderRisksPreparationDTO>(analysisPath(projectId, "risks"), signal);
