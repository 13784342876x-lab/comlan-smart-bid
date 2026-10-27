import assert from "node:assert/strict";
import test from "node:test";

import { activatesProjectCard, projectOverviewTarget, withoutProject } from "./projectCenterViewModel.ts";

test("project cards always target the unified overview", () => {
  assert.equal(projectOverviewTarget("prj-a/b"), "/projects/prj-a%2Fb");
  assert.equal(activatesProjectCard("Enter"), true);
  assert.equal(activatesProjectCard(" "), true);
  assert.equal(activatesProjectCard("Escape"), false);
});

test("successful deletion removes only the selected project from local list", () => {
  const projects = [{ project_id: "a" }, { project_id: "b" }] as never[];
  assert.deepEqual(withoutProject(projects, "a").map((item) => item.project_id), ["b"]);
});
