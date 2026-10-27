import type { FactMaterialItemDTO } from "../../types/api";

const placeholderIdentityPattern = /PH-FIELD-[A-Z0-9-]+/i;

export type FactsMaterialsGroup = "confirmation" | "project_fact" | "material";

export function factsMaterialsGroup(item: FactMaterialItemDTO): FactsMaterialsGroup {
  if (item.category === "confirmation_required") return "confirmation";
  if (item.category === "missing_project_fact") return "project_fact";
  return "material";
}

export function isFactsMaterialsComplete(status: string) {
  return ["confirmed", "verified", "completed", "resolved", "provided"].includes(status);
}

export function safeBusinessTitle(item: Pick<FactMaterialItemDTO, "title" | "semantic_resolved">) {
  if (!item.semantic_resolved || placeholderIdentityPattern.test(item.title)) return "待确认信息（名称未记录）";
  return item.title;
}

export function safeBusinessDescription(item: Pick<FactMaterialItemDTO, "description" | "semantic_resolved">) {
  if (!item.semantic_resolved || placeholderIdentityPattern.test(item.description)) {
    return "该占位字段没有保存可读的业务名称，请到关联章节核对并确认。";
  }
  return item.description;
}
