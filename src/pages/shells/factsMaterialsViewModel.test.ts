import assert from "node:assert/strict";
import test from "node:test";
import type { FactMaterialItemDTO } from "../../types/api.ts";
import { factsMaterialsGroup, safeBusinessDescription, safeBusinessTitle } from "./factsMaterialsViewModel.ts";

function item(value: Partial<FactMaterialItemDTO>): FactMaterialItemDTO {
  return {
    category: "confirmation_required",
    title: "项目经理姓名",
    status: "confirmation_required",
    status_label: "待确认",
    description: "请确认本项目项目经理姓名。",
    required: true,
    related_task_count: 1,
    trace_id: "internal",
    current_value: null,
    related_locations: [],
    requirement_context: [],
    source_label: null,
    action_label: "确认",
    action_url: "/projects/demo/writing-preparation?item=internal",
    semantic_resolved: true,
    ...value,
  };
}

test("uses canonical fact, confirmation and material business titles", () => {
  assert.equal(safeBusinessTitle(item({ category: "missing_project_fact", title: "项目经理姓名" })), "项目经理姓名");
  assert.equal(safeBusinessTitle(item({ title: "本项目是否承诺提供 7×24 小时技术支持？" })), "本项目是否承诺提供 7×24 小时技术支持？");
  assert.equal(safeBusinessTitle(item({ category: "material_preparation", title: "ISO 27001 信息安全管理体系认证证书" })), "ISO 27001 信息安全管理体系认证证书");
});

test("never exposes an unresolved placeholder token or invents its meaning", () => {
  const unresolved = item({
    title: "待确认占位事实：{PH-FIELD-7D8D757412FD}",
    description: "核对 {PH-FIELD-7D8D757412FD}",
    semantic_resolved: false,
  });
  assert.equal(safeBusinessTitle(unresolved), "待确认信息（名称未记录）");
  assert.equal(safeBusinessDescription(unresolved), "该占位字段没有保存可读的业务名称，请到关联章节核对并确认。");
  assert.ok(!safeBusinessTitle(unresolved).includes("PH-FIELD"));
});

test("keeps existing action target and separates the three user groups", () => {
  const fact = item({ category: "missing_project_fact", action_url: "/projects/demo/writing-preparation?item=FACT-X" });
  assert.equal(fact.action_url, "/projects/demo/writing-preparation?item=FACT-X");
  assert.equal(factsMaterialsGroup(fact), "project_fact");
  assert.equal(factsMaterialsGroup(item({ category: "confirmation_required" })), "confirmation");
  assert.equal(factsMaterialsGroup(item({ category: "material_preparation" })), "material");
});
