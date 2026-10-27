import type { WritingPlanCoverageDTO } from "../../types/api";

export function technicalCoverageSummary(values: WritingPlanCoverageDTO[]) {
  const pending = values.filter((item) => item.status === "unassigned").length;
  return { total: values.length, covered: values.length - pending, pending };
}
