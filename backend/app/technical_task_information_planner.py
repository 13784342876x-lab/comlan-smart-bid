from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.company_writing_outline import load_company_outline
from app.tender_writing_format_analyzer import load_response_outline


SCHEMA_VERSION = "technical_task_information_requirements_v0"
MANUAL_FACT_LABELS = {
    "bidder_name": "投标人名称",
    "project_name_and_code": "项目名称和编号",
    "offered_product_brand": "拟投产品品牌",
    "offered_product_model": "拟投产品型号",
    "manufacturer": "制造商",
    "origin": "产地",
    "actual_product_configuration": "实际产品配置（含CPU、内存、硬盘、网络及软件授权）",
    "actual_technical_parameters": "实际技术参数",
    "delivery_commitment": "最终交货承诺",
    "installation_period": "实际安装周期",
    "warranty_period": "最终保修期限",
    "service_response_time": "最终服务响应时间",
    "manufacturer_service_network": "制造商服务机构",
    "project_manager": "项目负责人",
    "project_team": "项目团队成员",
    "staffing_plan": "实际实施人员安排",
    "selected_performance": "最终选用的业绩",
    "selected_company_qualifications": "最终选用的企业资质",
    "selected_manufacturer_qualifications": "最终选用的制造商资质",
    "technical_deviation_status": "是否存在技术偏离",
}


def plan_task_information_requirements(
    response_outline: dict[str, Any],
    writing_outline: dict[str, Any],
    writing_tasks: dict[str, Any],
    company_outline: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    module_policies = {
        str(module.get("module_id") or ""): {
            "evidence_types": list(module.get("evidence_types") or []),
            "web_policy": str(module.get("web_policy") or "forbidden"),
        }
        for module in company_outline.get("modules") or []
    }
    task_results = []
    for task in writing_tasks.get("tasks") or []:
        manual = _manual_requirement(task)
        local = _local_requirement(task, module_policies)
        web = _web_requirement(task, module_policies)
        draftability = _draftability(task, manual, local, web)
        task_results.append(
            {
                "task_id": str(task.get("task_id") or ""),
                "chapter_id": str(task.get("chapter_id") or ""),
                "section_id": str(task.get("section_id") or ""),
                "target_path": list(task.get("target_path") or []),
                "response_point_ids": sorted(
                    {
                        str(item.get("response_point_id") or "")
                        for item in task.get("response_point_mappings") or []
                        if item.get("response_point_id")
                    }
                ),
                "score_item_refs": list(task.get("score_item_refs") or []),
                "information_requirements": {
                    "manual_project_fact": manual,
                    "local_kb": local,
                    "web": web,
                },
                "draftability": draftability,
                "legacy_status_audit": {
                    "previous_readiness": str(task.get("readiness") or ""),
                    "superseded_as_single_overall_status": True,
                },
            }
        )

    matrix = _readiness_matrix(task_results)
    result = {
        "schema_version": SCHEMA_VERSION,
        "source_schemas": {
            "response_outline": str(response_outline.get("schema_version") or ""),
            "writing_outline": str(writing_outline.get("schema_version") or ""),
            "writing_tasks": str(writing_tasks.get("schema_version") or ""),
            "company_outline": str(company_outline.get("schema_version") or ""),
        },
        "separation_guards": {
            "writing_outline_modified": False,
            "atomic_tasks_modified_or_removed": False,
            "draft_units_are_directory_nodes": False,
            "body_generated": False,
            "local_kb_queried": False,
            "web_research_executed": False,
            "reference_only_files_read": False,
            "tender_minimums_used_as_offered_facts": False,
        },
        "company_policy_usage": {
            "evidence_types": "用于限定未来本地RAG的证据类别，不代表材料当前存在或缺失。",
            "web_policy": "用于限定任务可规划的官方来源类型，不触发联网执行。",
        },
        "tasks": task_results,
        "summary": matrix,
    }
    return result, matrix


def build_task_information_files(
    response_outline_path: str | Path,
    writing_outline_path: str | Path,
    writing_tasks_path: str | Path,
    company_outline_path: str | Path,
    *,
    output_key: str,
    output_dir: str | Path = "outputs",
) -> dict[str, Any]:
    paths = {
        "response": Path(response_outline_path).expanduser().resolve(),
        "writing": Path(writing_outline_path).expanduser().resolve(),
        "tasks": Path(writing_tasks_path).expanduser().resolve(),
        "company": Path(company_outline_path).expanduser().resolve(),
    }
    hashes_before = {key: _sha256(path) for key, path in paths.items()}
    response = load_response_outline(paths["response"])
    writing = json.loads(paths["writing"].read_text(encoding="utf-8-sig"))
    tasks = json.loads(paths["tasks"].read_text(encoding="utf-8-sig"))
    company = load_company_outline(paths["company"])
    requirements, matrix = plan_task_information_requirements(response, writing, tasks, company)
    requirements["sources"] = {key + "_path": str(path) for key, path in paths.items()}
    requirements["source_integrity"] = {
        key: {"sha256_before": value, "sha256_after": _sha256(paths[key]), "unchanged": value == _sha256(paths[key])}
        for key, value in hashes_before.items()
    }

    task_dir = Path(output_dir).expanduser().resolve() / _safe_key(output_key) / "writing" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "task_information_requirements_json": task_dir / "task_information_requirements.json",
        "task_information_requirements_markdown": task_dir / "task_information_requirements.md",
        "task_readiness_matrix": task_dir / "task_readiness_matrix.json",
    }
    files["task_information_requirements_json"].write_text(
        json.dumps(requirements, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files["task_information_requirements_markdown"].write_text(
        render_task_information_markdown(requirements), encoding="utf-8"
    )
    files["task_readiness_matrix"].write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if any(value != _sha256(paths[key]) for key, value in hashes_before.items()):
        raise RuntimeError("an information-planning source file changed during processing")
    return {
        "requirements": requirements,
        "readiness_matrix": matrix,
        "files": {key: str(path) for key, path in files.items()},
    }


def render_task_information_markdown(requirements: dict[str, Any]) -> str:
    lines = [
        "# 原子写作任务信息需求矩阵",
        "",
        "本文件只说明信息需求和当前可生成程度，不包含正文。",
        "",
    ]
    current_chapter = ""
    for task in requirements.get("tasks") or []:
        chapter = task["target_path"][0]
        if chapter != current_chapter:
            lines.extend([f"## {chapter}", ""])
            current_chapter = chapter
        info = task["information_requirements"]
        manual = info["manual_project_fact"]
        local = info["local_kb"]
        web = info["web"]
        draftability = task["draftability"]
        lines.extend(
            [
                f"### {task['task_id']} {' / '.join(task['target_path'][1:]) or chapter}",
                "",
                f"- 人工项目事实：{manual['need']}；{'、'.join(_labels(manual['fact_types'])) or '无'}",
                f"- 本地知识库：{local['need']}；未来RAG查询：{'、'.join(local['evidence_types']) or '无'}",
                f"- 联网：{web['need']}；允许来源：{'、'.join(web['allowed_source_types']) or '无'}",
                f"- 联网用途：{'；'.join(web['purposes']) or '无'}",
                f"- 当前可生成程度：{draftability['status']}",
                f"- 不能定稿或占位原因：{draftability['reason']}",
                "- 缺失输入：" + ("、".join(draftability["missing_inputs"]) or "无"),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _manual_requirement(task: dict[str, Any]) -> dict[str, Any]:
    title = str((task.get("target_path") or [""])[-1])
    chapter = str((task.get("target_path") or [""])[0])
    fact_types: list[str] = []
    need = "none"

    if title == "项目背景":
        need, fact_types = "optional", ["project_name_and_code"]
    elif any(term in title for term in ("超融合系统详细", "超融合互联交换机", "超融合节点")):
        need, fact_types = "required", _product_fact_types()
    elif title in {"可靠性设计", "兼容与扩展设计"}:
        need, fact_types = "required", [
            "offered_product_brand",
            "offered_product_model",
            "manufacturer",
            "actual_product_configuration",
            "actual_technical_parameters",
        ]
    elif title in {"项目设计实施周期", "安装部署", "交付成果"}:
        need, fact_types = "required", ["delivery_commitment", "installation_period"]
    elif title in {"项目组织架构", "项目负责人及主要人员"}:
        need, fact_types = "required", ["project_manager", "project_team", "staffing_plan"]
    elif chapter == "实施部署方案" and title in {"实施总体规划", "项目建设技术方案"}:
        need, fact_types = "optional", ["delivery_commitment", "installation_period", "staffing_plan"]
    elif chapter == "售后服务与运行保障方案":
        fact_types = ["warranty_period", "service_response_time", "manufacturer_service_network"]
        need = "required" if title in {"服务机构与人员", "服务响应机制", "保修与维修方案", "服务承诺"} else "optional"
    elif chapter == "技术参数及点对点应答":
        need, fact_types = "required", [*_product_fact_types(), "technical_deviation_status"]
    elif chapter == "技术证明材料及附件索引":
        need = "required"
        if "产品技术" in title or "检测" in title:
            fact_types = _product_fact_types()
        elif "制造商" in title:
            fact_types = ["manufacturer", "selected_manufacturer_qualifications"]
        elif "投标人" in title:
            fact_types = ["bidder_name", "selected_company_qualifications"]
        elif "团队" in title:
            fact_types = ["project_manager", "project_team"]
        elif "业绩" in title:
            fact_types = ["bidder_name", "selected_performance"]
        elif "服务" in title:
            fact_types = ["bidder_name", "warranty_period", "service_response_time"]

    reason = {
        "none": "该任务可仅依据招标文件要求和已确定的文章结构进行规划。",
        "optional": "当前可形成框架；补充项目事实可提升项目针对性，但不得从招标最低要求反推。",
        "required": "最终版本必须使用本次投标已确认事实，缺失时只能保留占位或不能定稿。",
    }[need]
    return {"need": need, "fact_types": list(dict.fromkeys(fact_types)), "reason": reason}


def _local_requirement(
    task: dict[str, Any],
    module_policies: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    title = str((task.get("target_path") or [""])[-1])
    chapter = str((task.get("target_path") or [""])[0])
    task_types = [str(slot.get("evidence_type") or "") for slot in task.get("evidence_slots") or []]
    types: list[str] = []
    need = "none"

    if any(term in title for term in ("超融合系统详细", "超融合互联交换机", "超融合节点", "可靠性设计", "兼容与扩展设计")):
        need = "required"
        types = ["product_parameter_proof", "product_test_report", "product_certificate", "manufacturer_official_document"]
    elif chapter == "方案完整性及系统架构设计":
        need, types = "optional", ["manufacturer_official_document"]
    elif title in {"项目组织架构", "项目负责人及主要人员"}:
        need, types = "required", ["person_resume", "person_certificate"]
    elif chapter == "实施部署方案":
        need = "optional"
        types = [item for item in task_types if item in {"implementation_experience", "quality_management_material"}]
    elif chapter == "培训方案":
        need, types = "optional", ["training_material"]
    elif chapter == "售后服务与运行保障方案":
        types = ["service_network", "service_commitment"]
        need = "required" if title in {"服务机构与人员", "服务承诺"} else "optional"
    elif chapter == "绿色供应链管理方案":
        need, types = "required", ["green_supply_chain_evidence"]
    elif chapter == "技术参数及点对点应答":
        need, types = "required", ["product_parameter_proof", "product_test_report", "product_certificate", "manufacturer_official_document"]
    elif chapter == "技术证明材料及附件索引":
        need, types = "required", task_types

    types = list(dict.fromkeys(item for item in types if item))
    if not types:
        need = "none"
    policies = [module_policies.get(module_id, {}) for module_id in task.get("module_ids") or []]
    declared = sorted({item for policy in policies for item in policy.get("evidence_types", [])})
    reason = {
        "none": "该任务当前不要求本地证明材料即可形成招标要求层面的初稿。",
        "optional": "未来RAG可检索相关材料增强针对性；本轮不判断材料存在或缺失。",
        "required": "最终版本必须引用或索引本地证据；本轮仅规划未来RAG检索。",
    }[need]
    if declared and need != "none":
        reason += " 公司大纲声明的证据策略已用于限定候选类别。"
    return {"need": need, "evidence_types": types, "reason": reason}


def _web_requirement(
    task: dict[str, Any],
    module_policies: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    plans = list(task.get("web_research_plan") or [])
    policies = [
        str(module_policies.get(module_id, {}).get("web_policy") or "forbidden")
        for module_id in task.get("module_ids") or []
    ]
    if not plans:
        return {
            "need": "forbidden",
            "allowed_source_types": [],
            "purposes": [],
            "reason": "本任务没有必要的外部公开信息用途；企业和投标事实禁止通过互联网补全。",
        }
    allowed = sorted(
        {
            str(source_type)
            for plan in plans
            for source_type in plan.get("allowed_source_types") or []
            if source_type != "manufacturer_official"
        }
    )
    purposes = list(dict.fromkeys(str(plan.get("purpose") or "") for plan in plans if plan.get("purpose")))
    if not allowed:
        return {
            "need": "forbidden",
            "allowed_source_types": [],
            "purposes": [],
            "reason": "制造商尚未确认，禁止生成具体制造商官网检索目标。",
        }
    policy_note = "、".join(sorted(set(policies)))
    return {
        "need": "optional",
        "allowed_source_types": allowed,
        "purposes": purposes,
        "reason": f"仅可用于政策、标准、公开背景或通用原理补充；公司模块web_policy为{policy_note}，本轮不执行联网。",
    }


def _draftability(
    task: dict[str, Any],
    manual: dict[str, Any],
    local: dict[str, Any],
    web: dict[str, Any],
) -> dict[str, Any]:
    title = str((task.get("target_path") or [""])[-1])
    missing: list[str] = []
    if manual["need"] == "required":
        missing.extend(_labels(manual["fact_types"]))
    if local["need"] == "required":
        missing.extend(local["evidence_types"])
    if web["need"] == "required":
        missing.extend(web["purposes"])

    if title == "项目背景":
        status = "can_draft"
        reason = "招标文件已提供项目背景，当前可形成初稿；项目名称等可在定稿时校准。"
    elif missing:
        status = "cannot_finalize"
        reason = "可继续规划结构和占位内容，但缺少关键项目事实或本地证据，不能形成提交版。"
    elif manual["need"] == "optional" or local["need"] == "optional":
        status = "can_draft_with_placeholders"
        reason = "可编写框架和通用说明，项目化事实或材料引用位置使用明确占位符。"
        missing.extend(_labels(manual["fact_types"]) if manual["need"] == "optional" else [])
        missing.extend(local["evidence_types"] if local["need"] == "optional" else [])
    else:
        status = "can_draft"
        reason = "仅依赖招标文件事实即可形成初稿，不需要把最低要求反向当作投标事实。"
    return {"status": status, "reason": reason, "missing_inputs": list(dict.fromkeys(missing))}


def _readiness_matrix(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    matrix = {
        "total_tasks": len(tasks),
        "manual_project_fact": {"none": 0, "optional": 0, "required": 0},
        "local_kb": {"none": 0, "optional": 0, "required": 0},
        "web": {"forbidden": 0, "optional": 0, "required": 0},
        "draftability": {"can_draft": 0, "can_draft_with_placeholders": 0, "cannot_finalize": 0},
    }
    for task in tasks:
        info = task["information_requirements"]
        matrix["manual_project_fact"][info["manual_project_fact"]["need"]] += 1
        matrix["local_kb"][info["local_kb"]["need"]] += 1
        matrix["web"][info["web"]["need"]] += 1
        matrix["draftability"][task["draftability"]["status"]] += 1
    return matrix


def _product_fact_types() -> list[str]:
    return [
        "offered_product_brand",
        "offered_product_model",
        "manufacturer",
        "origin",
        "actual_product_configuration",
        "actual_technical_parameters",
    ]


def _labels(fact_types: list[str]) -> list[str]:
    return [MANUAL_FACT_LABELS.get(item, item) for item in fact_types]


def _safe_key(value: str) -> str:
    import re

    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip()).strip("._")
    if not cleaned:
        raise ValueError("output_key must contain a safe path component")
    return cleaned


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
