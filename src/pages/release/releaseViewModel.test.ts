import assert from "node:assert/strict";
import test from "node:test";

import type { ReleaseStatusDTO } from "../../types/api";
import { releaseView } from "./releaseViewModel.ts";

const ready: ReleaseStatusDTO = {
  project_id: "project-a",
  current_output_key: "revision-b",
  releaseable: true,
  blocking_reasons: [],
  status: "ready",
  generated_at: null,
  docx: null,
  pdf: null,
  required_submission_items: [],
};

test("pending review disables publish with user-facing reasons", () => {
  const view = releaseView({ ...ready, releaseable: false, status: "blocked", blocking_reasons: ["pending_issue_decisions", "pending_fact_confirmations"] }, false);
  assert.equal(view.canPublish, false);
  assert.deepEqual(view.blockers, ["仍有审核项待处理", "仍有事实待确认"]);
});

test("ready release can publish and publishing prevents a duplicate action", () => {
  assert.equal(releaseView(ready, false).canPublish, true);
  assert.equal(releaseView(ready, true).canPublish, false);
  assert.equal(releaseView(ready, true).actionLabel, "正在生成技术标正文…");
  assert.equal(releaseView(ready, false).statusLabel, "待生成");
});

test("downloads are visible only for the current published projection", () => {
  const published: ReleaseStatusDTO = {
    ...ready,
    status: "published",
    docx: { filename: "项目_技术标正文.docx", sha256: "a", size_bytes: 1 },
    pdf: { filename: "项目_技术标正文.pdf", sha256: "b", size_bytes: 1 },
  };
  assert.equal(releaseView(published, false).showDownloads, true);
  assert.equal(releaseView(published, false).statusLabel, "已生成");
  assert.equal(releaseView({ ...published, status: "ready", docx: null, pdf: null }, false).showDownloads, false);
});
