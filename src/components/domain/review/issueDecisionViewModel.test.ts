import assert from "node:assert/strict";
import test from "node:test";

import { canRunAIRevision, canSelectMachineRevision, type RevisionDecisionState } from "./issueDecisionViewModel.ts";

const base: RevisionDecisionState = {
  current_decision: "pending",
  recommendation: "补充执行、复核和关闭判据。",
  revision_instruction: null,
  risk_acceptance_ref: null,
  machine_revision_available: true,
  revision_candidate_ready: false,
};

test("accepted machine recommendation becomes executable only after its candidate is ready", () => {
  assert.equal(canSelectMachineRevision(base), true);
  assert.equal(canRunAIRevision({ ...base, current_decision: "accepted" }), false);
  assert.equal(canRunAIRevision({ ...base, current_decision: "accepted", revision_candidate_ready: true }), true);
});

test("accepted risk never enables AI revision", () => {
  const acceptedRisk = {
    ...base,
    current_decision: "accepted",
    revision_candidate_ready: true,
    risk_acceptance_ref: "risk-accepted",
  };
  assert.equal(canSelectMachineRevision(acceptedRisk), false);
  assert.equal(canRunAIRevision(acceptedRisk), false);
});

test("modified remains executable while rejected does not", () => {
  assert.equal(canRunAIRevision({
    ...base,
    current_decision: "modified",
    revision_instruction: "按人工要求补充。",
    revision_candidate_ready: true,
  }), true);
  assert.equal(canRunAIRevision({ ...base, current_decision: "rejected", revision_candidate_ready: true }), false);
});

test("accepted without an executable recommendation is not offered or executed", () => {
  const unavailable = { ...base, current_decision: "accepted", recommendation: null, machine_revision_available: false, revision_candidate_ready: true };
  assert.equal(canSelectMachineRevision(unavailable), false);
  assert.equal(canRunAIRevision(unavailable), false);
});
