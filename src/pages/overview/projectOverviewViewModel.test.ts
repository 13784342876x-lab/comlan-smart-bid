import assert from "node:assert/strict";
import test from "node:test";

import type { ProjectOverviewDTO } from "../../types/api";
import { overviewActionTarget, reviewMetrics, userStage } from "./projectOverviewViewModel.ts";
import { projectBusinessIdentity } from "../../utils/projectPresentation.ts";

const overview = {
  project: { project_id: "prj-1", workflow_status: "等待人工审核", release_status: "尚未发布", blocking_count: 2, primary_action_url: null },
  current_pipeline_run: null,
  human_review_ready: true,
  machine_audit_ready: true,
  fulltext_ready: true,
  revision_ready: false,
  review_progress: { issue_decisions_total: 5, issue_decisions_completed: 2, fact_confirmations_total: 3, fact_confirmations_completed: 1, material_verifications_total: 2, material_verifications_completed: 2 },
  actionability_counts: { must_decide: 2 },
} as ProjectOverviewDTO;

test("overview stage and action depend on workflow state, not catalog source", () => {
  assert.equal(userStage(overview), "人工审核");
  assert.equal(overviewActionTarget(overview), "/projects/prj-1/review");
  assert.equal(userStage({ ...overview, project: { ...overview.project, catalog_source: "legacy_artifact" } }), "人工审核");
});

test("review progress combines existing issue, fact, and material totals", () => {
  assert.deepEqual(reviewMetrics(overview), { started: true, total: 10, completed: 5, pendingDecisions: 3, blocking: 2 });
});

test("procuring entity is never used as the supplier fallback", () => {
  assert.deepEqual(projectBusinessIdentity({ bid_subject: null, supplier_name: null, procuring_entity: "某采购医院" }), { bidSubject: "未识别", supplierName: "未填写" });
  assert.equal(projectBusinessIdentity({ bid_subject: "远程医疗系统", supplier_name: "某投标公司", procuring_entity: "某采购医院" }).supplierName, "某投标公司");
});
