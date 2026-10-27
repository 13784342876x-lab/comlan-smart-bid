import assert from "node:assert/strict";
import test from "node:test";

import type { WritingPlanCoverageDTO } from "../../types/api";
import { technicalCoverageSummary } from "./writingPlanViewModel.ts";

test("technical coverage reports total, covered, and pending requirements", () => {
  const values = [
    { trace_id: "internal-a", status: "planned" },
    { trace_id: "internal-b", status: "multiple_sections" },
    { trace_id: "internal-c", status: "unassigned" },
  ] as WritingPlanCoverageDTO[];
  assert.deepEqual(technicalCoverageSummary(values), { total: 3, covered: 2, pending: 1 });
});
