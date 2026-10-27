from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.company_writing_outline import load_company_outline
from app.tender_writing_format_analyzer import load_response_outline


SCHEMA_VERSION = "technical_section_writing_tasks_v0"
SOURCE_PRIORITY = [
    "tender_requirement",
    "confirmed_project_fact",
    "local_kb_evidence",
    "official_web_source",
    "general_explanatory_knowledge",
]

FACT_DEFINITIONS = [
    ("bidder_name", "投标人名称"),
    ("project_name_and_code", "项目名称和编号"),
    ("offered_product_brand", "拟投产品品牌"),
    ("offered_product_model", "拟投产品型号"),
    ("manufacturer", "制造商"),
    ("origin", "产地"),
    ("actual_product_configuration", "实际产品配置"),
    ("actual_technical_parameters", "实际技术参数"),
    ("delivery_commitment", "交货承诺"),
    ("installation_period", "安装周期"),
    ("warranty_period", "保修期限"),
    ("service_response_time", "服务响应时间"),
    ("manufacturer_service_network", "制造商服务机构"),
    ("project_manager", "项目负责人"),
    ("project_team", "项目团队"),
    ("staffing_plan", "人员投入计划"),
    ("selected_performance", "采用的业绩"),
    ("selected_company_qualifications", "采用的企业资质"),
    ("selected_manufacturer_qualifications", "采用的制造商资质"),
    ("technical_deviation_status", "是否存在技术偏离"),
]

EVIDENCE_DEFINITIONS = {
    "company_qualification": ("投标人主体资质与体系证书", "bidder", True),
    "manufacturer_qualification": ("制造商能力与体系证书", "manufacturer", True),
    "product_certificate": ("产品认证证书", "offered_product", True),
    "product_test_report": ("产品检测、检验或测试报告", "offered_product", True),
    "product_parameter_proof": ("产品参数证明材料", "offered_product", False),
    "manufacturer_official_document": ("制造商正式技术资料", "offered_product", False),
    "company_performance": ("类似项目业绩证明", "bidder", False),
    "person_resume": ("项目人员简历", "project_person", False),
    "person_certificate": ("项目人员证书", "project_person", True),
    "service_network": ("服务机构与服务网络材料", "manufacturer", False),
    "service_commitment": ("保修、响应及服务承诺", "bidder", False),
    "implementation_experience": ("项目实施经验材料", "bidder", False),
    "quality_management_material": ("质量管理制度与过程材料", "bidder", False),
    "training_material": ("培训课程、教材与培训记录", "bidder", False),
    "green_supply_chain_evidence": ("绿色供应链、节能环保相关证明", "manufacturer", True),
}


def build_section_task_plan(
    response_outline: dict[str, Any],
    writing_outline: dict[str, Any],
    company_outline: dict[str, Any],
    project_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project_context = project_context if isinstance(project_context, dict) else {}
    point_index = _point_index(response_outline)
    score_index = {
        str(item.get("score_item_id") or ""): item
        for item in writing_outline.get("score_items") or []
    }
    tasks: list[dict[str, Any]] = []
    task_chapter: dict[str, str] = {}

    for chapter in writing_outline.get("chapters") or []:
        leaves = list(chapter.get("children") or []) or [chapter]
        for section in leaves:
            task_id = f"TASK-{len(tasks) + 1:03d}"
            mappings = _dedupe_mappings(list(section.get("response_point_mappings") or []))
            task = _make_task(
                task_id,
                chapter,
                section,
                mappings,
                point_index,
                score_index,
                project_context,
            )
            tasks.append(task)
            task_chapter[task_id] = str(chapter.get("chapter_id") or "")

    _assign_unplaced_chapter_mappings(tasks, writing_outline.get("chapters") or [], task_chapter, point_index)
    _refresh_task_requirements(tasks, point_index, score_index, project_context)

    fact_requests = _build_fact_requests(tasks, project_context)
    evidence_catalog = _build_evidence_catalog(tasks)
    local_queries = _build_local_query_plan(tasks, evidence_catalog, fact_requests)
    web_queries = _build_web_plan(tasks)
    all_response_ids = sorted(point_index)
    mandatory_ids = sorted(
        point_id
        for point_id, context in point_index.items()
        if "★" in str(context["point"].get("mandatory_marker") or "")
    )
    mapped_task_ids = sorted(
        {
            mapping["response_point_id"]
            for task in tasks
            for mapping in task["response_point_mappings"]
        }
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source_priority": SOURCE_PRIORITY,
        "source_usage_constraints": {
            "general_explanatory_knowledge": "仅用于解释和过渡，不得用于产品、人员、业绩、资质或承诺事实。",
            "enterprise_facts": "只能来自经确认项目事实或本地知识库证据，不得通过互联网补全。",
            "tender_minimums": "不得反向推断为实际投标配置、参数或承诺。",
        },
        "sources": {
            "response_outline_schema": str(response_outline.get("schema_version") or ""),
            "writing_outline_schema": str(writing_outline.get("schema_version") or ""),
            "company_outline_schema": str(company_outline.get("schema_version") or ""),
            "project_context_supplied": bool(project_context),
        },
        "execution_guard": {
            "body_generated": False,
            "local_kb_queried": False,
            "web_research_executed": False,
            "reference_only_files_read": False,
        },
        "tasks": tasks,
        "project_fact_requests": fact_requests,
        "evidence_catalog": evidence_catalog,
        "local_evidence_query_plan": local_queries,
        "web_research_plan": web_queries,
        "coverage": {
            "all_response_point_ids": all_response_ids,
            "task_mapped_response_point_ids": mapped_task_ids,
            "task_unmapped_response_point_ids": sorted(set(all_response_ids) - set(mapped_task_ids)),
            "mandatory_response_point_ids": mandatory_ids,
            "mandatory_unmapped_ids": sorted(set(mandatory_ids) - set(mapped_task_ids)),
            "response_point_coverage_rate": _rate(len(mapped_task_ids), len(all_response_ids)),
            "mandatory_coverage_rate": _rate(
                len(set(mandatory_ids) & set(mapped_task_ids)), len(mandatory_ids)
            ),
        },
        "summary": {
            "task_count": len(tasks),
            "project_fact_slot_count": len(fact_requests),
            "local_evidence_slot_count": len(evidence_catalog),
            "local_query_count": len(local_queries),
            "web_research_plan_count": len(web_queries),
            "readiness_counts": _count_by(tasks, "readiness"),
        },
    }


def build_section_task_plan_files(
    response_outline_path: str | Path,
    writing_outline_path: str | Path,
    company_outline_path: str | Path,
    *,
    output_key: str,
    output_dir: str | Path = "outputs",
    project_context_path: str | Path | None = None,
) -> dict[str, Any]:
    response_path = Path(response_outline_path).expanduser().resolve()
    writing_path = Path(writing_outline_path).expanduser().resolve()
    company_path = Path(company_outline_path).expanduser().resolve()
    response = load_response_outline(response_path)
    writing = json.loads(writing_path.read_text(encoding="utf-8-sig"))
    company = load_company_outline(company_path)
    project_context: dict[str, Any] = {}
    context_path = None
    if project_context_path:
        context_path = Path(project_context_path).expanduser().resolve()
        project_context = json.loads(context_path.read_text(encoding="utf-8-sig"))

    plan = build_section_task_plan(response, writing, company, project_context)
    plan["sources"].update(
        {
            "response_outline_path": str(response_path),
            "writing_outline_path": str(writing_path),
            "company_outline_path": str(company_path),
            "project_context_path": str(context_path) if context_path else "",
        }
    )
    task_dir = Path(output_dir).expanduser().resolve() / _safe_key(output_key) / "writing" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "section_writing_tasks_json": task_dir / "section_writing_tasks.json",
        "section_writing_tasks_markdown": task_dir / "section_writing_tasks.md",
        "project_fact_request": task_dir / "project_fact_request.json",
        "local_evidence_query_plan": task_dir / "local_evidence_query_plan.json",
        "web_research_plan": task_dir / "web_research_plan.json",
    }
    files["section_writing_tasks_json"].write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files["section_writing_tasks_markdown"].write_text(
        render_section_tasks_markdown(plan), encoding="utf-8"
    )
    files["project_fact_request"].write_text(
        json.dumps(
            {
                "schema_version": "project_fact_request_v0",
                "source_policy": "confirmed_project_fact_only",
                "reverse_inference_from_tender_prohibited": True,
                "slots": plan["project_fact_requests"],
                "summary": {"slot_count": len(plan["project_fact_requests"])},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    files["local_evidence_query_plan"].write_text(
        json.dumps(
            {
                "schema_version": "local_evidence_query_plan_v0",
                "execution_status": "planned_not_executed",
                "local_kb_queried": False,
                "queries": plan["local_evidence_query_plan"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    files["web_research_plan"].write_text(
        json.dumps(
            {
                "schema_version": "web_research_plan_v0",
                "execution_status": "planned_not_executed",
                "web_research_executed": False,
                "prohibited_uses": [
                    "证明本公司资质",
                    "补全本公司人员或业绩",
                    "猜测实际产品配置",
                    "生成服务承诺",
                ],
                "queries": plan["web_research_plan"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"plan": plan, "files": {key: str(path) for key, path in files.items()}}


def render_section_tasks_markdown(plan: dict[str, Any]) -> str:
    lines = [
        "# TechnicalSectionTaskPlanner V0",
        "",
        "本文件仅包含写作任务与资料准备要求，不包含投标正文。",
        "",
        f"- 写作任务：{plan['summary']['task_count']}",
        f"- 项目事实槽位：{plan['summary']['project_fact_slot_count']}",
        f"- 本地证据槽位：{plan['summary']['local_evidence_slot_count']}",
        f"- 外部检索计划：{plan['summary']['web_research_plan_count']}（未执行）",
        "",
    ]
    for task in plan["tasks"]:
        lines.extend(
            [
                f"## {task['task_id']} {' / '.join(task['target_path'])}",
                "",
                f"- 就绪状态：{task['readiness']}",
                f"- 关联模块：{'、'.join(task['module_ids']) or '无'}",
                "- 关联响应点："
                + (
                    "、".join(
                        f"{item['response_point_id']}({item['mapping_role']})"
                        for item in task["response_point_mappings"]
                    )
                    or "无"
                ),
                f"- 关联评分项：{'、'.join(task['score_item_refs']) or '无'}",
                f"- 写作目标：{task['writing_objective']}",
                "- 必须覆盖：" + ("；".join(task["required_content_points"]) or "按章节标题形成结构化说明"),
                "- 项目事实槽位：" + ("、".join(item["name"] for item in task["project_fact_slots"]) or "无"),
                "- 本地证据槽位：" + ("、".join(item["evidence_type"] for item in task["evidence_slots"]) or "无"),
                "- 阻断条件：" + ("；".join(task["blocking_conditions"]) or "无"),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _make_task(
    task_id: str,
    chapter: dict[str, Any],
    section: dict[str, Any],
    mappings: list[dict[str, Any]],
    point_index: dict[str, dict[str, Any]],
    score_index: dict[str, dict[str, Any]],
    project_context: dict[str, Any],
) -> dict[str, Any]:
    target_path = [str(chapter.get("title") or "")]
    if section is not chapter:
        target_path.append(str(section.get("title") or ""))
    module_ids = list(dict.fromkeys(section.get("module_ids") or chapter.get("module_ids") or []))
    # Root chapters carry aggregated coverage for reporting.  Their score refs
    # must not be copied to every child task: task-level refs are reserved for
    # a directly scored section or a response point explicitly mapped by that
    # score item.
    score_refs = set(section.get("score_item_refs") or [])
    if section is chapter:
        score_refs.update(chapter.get("score_item_refs") or [])
    score_refs = sorted(score_refs)
    display_ids = sorted(set(section.get("display_block_ids") or []))
    facts = _fact_slots_for_task(" / ".join(target_path), module_ids, project_context)
    evidence = _evidence_slots_for_task(" / ".join(target_path), module_ids)
    constraints = _output_constraints(chapter, section, mappings)
    task = {
        "task_id": task_id,
        "chapter_id": str(chapter.get("chapter_id") or ""),
        "section_id": str(section.get("chapter_id") or ""),
        "target_path": target_path,
        "module_ids": module_ids,
        "response_point_mappings": mappings,
        "display_block_ids": display_ids,
        "score_item_refs": score_refs,
        "writing_objective": f"形成“{' / '.join(target_path)}”的可审查技术响应内容，覆盖关联要求且不虚构项目事实。",
        "required_content_points": _required_points(mappings, point_index, score_refs, score_index),
        "tender_source_refs": _source_refs(mappings, point_index),
        "project_fact_slots": facts,
        "evidence_slots": evidence,
        "kb_query_plan": [],
        "web_research_plan": [],
        "output_constraints": constraints,
        "blocking_conditions": [],
        "readiness": "ready",
        "body_generated": False,
    }
    _set_readiness(task)
    return task


def _assign_unplaced_chapter_mappings(
    tasks: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    task_chapter: dict[str, str],
    point_index: dict[str, dict[str, Any]],
) -> None:
    for chapter in chapters:
        chapter_id = str(chapter.get("chapter_id") or "")
        chapter_tasks = [task for task in tasks if task_chapter[task["task_id"]] == chapter_id]
        if not chapter_tasks:
            continue
        already = {
            mapping["response_point_id"]
            for task in chapter_tasks
            for mapping in task["response_point_mappings"]
        }
        for mapping in chapter.get("response_point_mappings") or []:
            if mapping["response_point_id"] not in already:
                chapter_tasks[0]["response_point_mappings"].append(dict(mapping))
                already.add(mapping["response_point_id"])


def _refresh_task_requirements(
    tasks: list[dict[str, Any]],
    point_index: dict[str, dict[str, Any]],
    score_index: dict[str, dict[str, Any]],
    project_context: dict[str, Any],
) -> None:
    for task in tasks:
        task["response_point_mappings"] = _dedupe_mappings(task["response_point_mappings"])
        task["display_block_ids"] = sorted(
            {
                block_id
                for mapping in task["response_point_mappings"]
                for block_id in point_index.get(mapping["response_point_id"], {}).get("display_block_ids", [])
            }
        )
        task["required_content_points"] = _required_points(
            task["response_point_mappings"], point_index, task["score_item_refs"], score_index
        )
        task["tender_source_refs"] = _source_refs(task["response_point_mappings"], point_index)
        task["project_fact_slots"] = _fact_slots_for_task(
            " / ".join(task["target_path"]), task["module_ids"], project_context
        )
        task["evidence_slots"] = _evidence_slots_for_task(
            " / ".join(task["target_path"]), task["module_ids"]
        )
        _set_readiness(task)


def _fact_slots_for_task(
    path_text: str,
    module_ids: list[str],
    project_context: dict[str, Any],
) -> list[dict[str, Any]]:
    keys: list[str] = []
    if any(term in path_text for term in ("需求理解", "项目背景", "建设目标")):
        keys += ["project_name_and_code"]
    if any(term in path_text for term in ("超融合", "产品", "技术参数", "点对点", "兼容", "可靠性")):
        keys += [
            "offered_product_brand",
            "offered_product_model",
            "manufacturer",
            "origin",
            "actual_product_configuration",
            "actual_technical_parameters",
        ]
    if any(term in path_text for term in ("交付", "实施周期", "安装部署")):
        keys += ["delivery_commitment", "installation_period"]
    if "MODULE-SERVICE-WARRANTY" in module_ids:
        keys += ["warranty_period", "service_response_time", "manufacturer_service_network"]
    if any(term in path_text for term in ("项目组织", "项目负责人", "主要人员", "人员投入")):
        keys += ["project_manager", "project_team", "staffing_plan"]
    if "MODULE-EVIDENCE-APPENDIX" in module_ids:
        keys += [
            "bidder_name",
            "selected_performance",
            "selected_company_qualifications",
            "selected_manufacturer_qualifications",
        ]
    if "MODULE-PARAMETER-RESPONSE" in module_ids:
        keys += ["technical_deviation_status"]
    result = []
    definitions = dict(FACT_DEFINITIONS)
    for key in dict.fromkeys(keys):
        value = project_context.get(key)
        result.append(
            {
                "fact_slot_id": f"FACT-{list(definitions).index(key) + 1:03d}",
                "fact_key": key,
                "name": definitions[key],
                "status": "confirmed" if value not in (None, "", []) else "missing",
                "value": value if value not in (None, "", []) else None,
                "source_policy": "confirmed_project_fact",
                "prohibit_tender_reverse_inference": True,
            }
        )
    return result


def _evidence_slots_for_task(path_text: str, module_ids: list[str]) -> list[dict[str, Any]]:
    types: list[str] = []
    if "MODULE-DETAILED-SOLUTION" in module_ids or "技术参数" in path_text:
        types += [
            "product_parameter_proof",
            "product_test_report",
            "product_certificate",
            "manufacturer_official_document",
        ]
    if "MODULE-PROJECT-MANAGEMENT" in module_ids and any(term in path_text for term in ("人员", "组织")):
        types += ["person_resume", "person_certificate"]
    if "MODULE-IMPLEMENTATION" in module_ids:
        types += ["implementation_experience"]
    if "MODULE-QUALITY-ACCEPTANCE" in module_ids and any(term in path_text for term in ("质量", "测试", "验收")):
        types += ["quality_management_material"]
    if "MODULE-TRAINING" in module_ids:
        types += ["training_material"]
    if "MODULE-SERVICE-WARRANTY" in module_ids:
        types += ["service_network", "service_commitment"]
    if "MODULE-SPECIAL-TOPICS" in module_ids:
        types += ["green_supply_chain_evidence"]
    if "MODULE-EVIDENCE-APPENDIX" in module_ids:
        if "制造商" in path_text:
            types += ["manufacturer_qualification"]
        elif "投标人" in path_text:
            types += ["company_qualification"]
        elif "业绩" in path_text:
            types += ["company_performance"]
        elif "团队" in path_text:
            types += ["person_resume", "person_certificate"]
        elif "检测" in path_text or "认证" in path_text:
            types += ["product_certificate", "product_test_report"]
        elif "技术资料" in path_text:
            types += ["manufacturer_official_document", "product_parameter_proof"]
        elif "服务" in path_text:
            types += ["service_commitment"]
    result = []
    for evidence_type in dict.fromkeys(types):
        description, entity, validity = EVIDENCE_DEFINITIONS[evidence_type]
        result.append(
            {
                "evidence_type": evidence_type,
                "required": True,
                "source_policy": "local_kb_evidence",
                "query_hints": [description, path_text.split(" / ")[-1]],
                "blocking": True,
                "applicable_entity": entity,
                "validity_required": validity,
                "status": "missing",
            }
        )
    return result


def _build_fact_requests(tasks: list[dict[str, Any]], project_context: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for index, (key, name) in enumerate(FACT_DEFINITIONS, start=1):
        value = project_context.get(key)
        target_task_ids = [
            task["task_id"]
            for task in tasks
            if any(slot["fact_key"] == key for slot in task["project_fact_slots"])
        ]
        result.append(
            {
                "fact_slot_id": f"FACT-{index:03d}",
                "fact_key": key,
                "name": name,
                "required": bool(target_task_ids),
                "status": "confirmed" if value not in (None, "", []) else "missing",
                "value": value if value not in (None, "", []) else None,
                "source_policy": "confirmed_project_fact",
                "target_task_ids": target_task_ids,
                "prohibited_inference": "不得从招标最低要求反向生成实际投标事实",
            }
        )
    return result


def _build_evidence_catalog(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for evidence_type, (description, entity, validity) in EVIDENCE_DEFINITIONS.items():
        target_ids = [
            task["task_id"]
            for task in tasks
            if any(slot["evidence_type"] == evidence_type for slot in task["evidence_slots"])
        ]
        result.append(
            {
                "evidence_type": evidence_type,
                "required": bool(target_ids),
                "source_policy": "local_kb_evidence",
                "query_hints": [description],
                "blocking": bool(target_ids),
                "applicable_entity": entity,
                "validity_required": validity,
                "target_task_ids": target_ids,
            }
        )
    return result


def _build_local_query_plan(
    tasks: list[dict[str, Any]],
    evidence_catalog: list[dict[str, Any]],
    fact_requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    queries = []
    task_by_id = {task["task_id"]: task for task in tasks}
    for slot in evidence_catalog:
        query = {
                "query_id": f"KBQ-{len(queries) + 1:03d}",
                "query_kind": "evidence",
                "evidence_type": slot["evidence_type"],
                "query": "；".join(slot["query_hints"]),
                "purpose": "为目标章节定位经确认的本地证明材料",
                "target_task_ids": slot["target_task_ids"],
                "required": slot["required"],
                "execution_status": "planned_not_executed",
            }
        queries.append(query)
        for task_id in query["target_task_ids"]:
            task_by_id[task_id]["kb_query_plan"].append(dict(query))
    enterprise_fact_keys = {
        "bidder_name",
        "manufacturer_service_network",
        "project_manager",
        "project_team",
        "selected_performance",
        "selected_company_qualifications",
        "selected_manufacturer_qualifications",
    }
    for fact in fact_requests:
        if fact["fact_key"] not in enterprise_fact_keys:
            continue
        query = {
                "query_id": f"KBQ-{len(queries) + 1:03d}",
                "query_kind": "fact_candidate_for_manual_confirmation",
                "fact_slot_id": fact["fact_slot_id"],
                "query": fact["name"],
                "purpose": "仅从本地资料定位候选事实，仍需项目确认后方可用于正文",
                "target_task_ids": fact["target_task_ids"],
                "required": fact["required"],
                "execution_status": "planned_not_executed",
            }
        queries.append(query)
        for task_id in query["target_task_ids"]:
            task_by_id[task_id]["kb_query_plan"].append(dict(query))
    return queries


def _build_web_plan(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for task in tasks:
        title = task["target_path"][-1]
        query = ""
        purpose = ""
        allowed: list[str] = []
        if title == "项目背景":
            query = "医院信息化建设 主管部门政策 公开背景"
            purpose = "补充公开政策与行业背景，不替代项目确认事实"
            allowed = ["government_official", "health_authority_official", "hospital_official"]
        elif any(term in title for term in ("可靠", "安全", "兼容", "测试", "质量", "验收")):
            query = f"{title} 国家标准 行业标准"
            purpose = "定位可引用的国家或行业标准"
            allowed = ["government_official", "standards_authority_official"]
        elif "绿色供应链" in title:
            query = "绿色供应链管理 节能环保 政府政策 国家标准"
            purpose = "定位政府政策及国家行业标准"
            allowed = ["government_official", "standards_authority_official"]
        elif "超融合" in title:
            query = f"{title} 通用技术原理"
            purpose = "解释通用技术原理，不用于猜测拟投产品配置"
            allowed = ["standards_authority_official", "manufacturer_official"]
        if query:
            item = {
                "query": query,
                "purpose": purpose,
                "allowed_source_types": allowed,
                "required": False,
                "target_section_id": task["section_id"],
                "execution_status": "planned_not_executed",
            }
            task["web_research_plan"].append(item)
            result.append(item)
    return result


def _output_constraints(
    chapter: dict[str, Any],
    section: dict[str, Any],
    mappings: list[dict[str, Any]],
) -> dict[str, Any]:
    title = str(section.get("title") or "")
    chapter_type = str(chapter.get("chapter_type") or "narrative")
    if "点对点应答表" in title:
        content_type = "table"
    elif chapter_type in {"table", "appendix", "mixed"}:
        content_type = chapter_type
    else:
        content_type = "narrative"
    table_response = any(item["mapping_role"] == "table_response" for item in mappings)
    return {
        "content_type": content_type,
        "must_use_specific_values": table_response or any(term in title for term in ("参数", "周期", "保修", "响应")),
        "prohibit_generic_compliance_only": table_response,
        "required_tables": ["技术要求点对点应答表"] if "点对点应答表" in title else [],
    }


def _set_readiness(task: dict[str, Any]) -> None:
    missing_facts = [slot["name"] for slot in task["project_fact_slots"] if slot["status"] != "confirmed"]
    missing_evidence = [slot["evidence_type"] for slot in task["evidence_slots"] if slot["blocking"]]
    conditions = []
    if missing_facts:
        conditions.append("缺少经确认的项目事实：" + "、".join(missing_facts))
    if missing_evidence:
        conditions.append("缺少本地证据：" + "、".join(missing_evidence))
    task["blocking_conditions"] = conditions
    if missing_facts:
        task["readiness"] = "needs_project_fact"
    elif missing_evidence:
        task["readiness"] = "needs_local_evidence"
    elif any(item.get("required") for item in task["web_research_plan"]):
        task["readiness"] = "needs_web_research"
    else:
        task["readiness"] = "ready"


def _required_points(
    mappings: list[dict[str, Any]],
    point_index: dict[str, dict[str, Any]],
    score_refs: list[str],
    score_index: dict[str, dict[str, Any]],
) -> list[str]:
    points = []
    for mapping in mappings:
        context = point_index.get(mapping["response_point_id"])
        if context:
            heading = str(context["point"].get("original_heading") or "")
            text = _shorten(str(context["point"].get("original_text") or ""), 220)
            points.append(f"{mapping['response_point_id']} {heading}：{text}".strip("："))
    for score_ref in score_refs:
        if score_ref in score_index:
            item = score_index[score_ref]
            points.append(f"评分项“{item.get('title', '')}”：{item.get('criteria_summary', '')}")
    return list(dict.fromkeys(points))


def _source_refs(
    mappings: list[dict[str, Any]],
    point_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mapping in mappings:
        context = point_index.get(mapping["response_point_id"], {})
        for source in context.get("point", {}).get("source_refs") or []:
            summarized = {
                "response_point_id": mapping["response_point_id"],
                "file_id": source.get("file_id"),
                "file_name": source.get("file_name"),
                "clause_path": source.get("clause_path") or [],
                "page_start": source.get("page_start"),
                "page_end": source.get("page_end"),
                "row_or_cell": source.get("row_or_cell"),
            }
            key = json.dumps(summarized, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                result.append(summarized)
                seen.add(key)
    return result


def _point_index(response_outline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    block_ids: dict[str, list[str]] = {}
    for block in response_outline.get("display_blocks") or []:
        for point_id in block.get("response_point_ids") or []:
            block_ids.setdefault(str(point_id), []).append(str(block.get("block_id") or ""))
    return {
        str(point.get("point_id") or ""): {
            "point": point,
            "chapter_id": str(chapter.get("chapter_id") or ""),
            "display_block_ids": block_ids.get(str(point.get("point_id") or ""), []),
        }
        for chapter in response_outline.get("chapters") or []
        for point in chapter.get("response_points") or []
    }


def _dedupe_mappings(mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        {
            f"{item.get('response_point_id')}|{item.get('mapping_role')}": {
                "response_point_id": str(item.get("response_point_id") or ""),
                "mapping_role": str(item.get("mapping_role") or "supporting"),
            }
            for item in mappings
            if item.get("response_point_id")
        }.values(),
        key=lambda item: (item["response_point_id"], item["mapping_role"]),
    )


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "")
        result[value] = result.get(value, 0) + 1
    return result


def _rate(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else round(numerator / denominator, 6)


def _safe_key(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip()).strip("._")
    if not cleaned:
        raise ValueError("output_key must contain a safe path component")
    return cleaned


def _shorten(text: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"
