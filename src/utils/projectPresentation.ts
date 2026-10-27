import type { ProjectSummaryDTO } from "../types/api";

export function projectBusinessIdentity(project: Pick<ProjectSummaryDTO, "bid_subject" | "supplier_name" | "procuring_entity">) {
  return {
    bidSubject: project.bid_subject ?? "未识别",
    supplierName: project.supplier_name ?? "未填写",
  };
}
