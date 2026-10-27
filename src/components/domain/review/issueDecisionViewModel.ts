export interface RevisionDecisionState {
  current_decision: string | null;
  recommendation: string | null;
  revision_instruction: string | null;
  risk_acceptance_ref: string | null;
  machine_revision_available: boolean;
  revision_candidate_ready: boolean;
}

export function canSelectMachineRevision(item: RevisionDecisionState): boolean {
  return !item.risk_acceptance_ref
    && item.machine_revision_available
    && Boolean(item.recommendation?.trim());
}

export function canRunAIRevision(item: RevisionDecisionState): boolean {
  if (!item.revision_candidate_ready || item.risk_acceptance_ref) return false;
  if (item.current_decision === "modified") {
    return Boolean(item.revision_instruction?.trim());
  }
  return item.current_decision === "accepted" && canSelectMachineRevision(item);
}
