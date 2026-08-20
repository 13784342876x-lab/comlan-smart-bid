from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.manual_review_tables import (
    IMPLEMENTATION_PHASES,
    SERVICE_SOLUTION_POINTS,
    build_manual_review_tables,
)
from app.missing_material_request import build_missing_material_request, render_missing_material_request_markdown


MATRIX_ORDER = [
    "应答文件技术部分总体评价",
    "项目负责人情况",
    "项目组织与团队配置",
    "业绩与项目经验",
    "总体服务方案",
    "实施计划与进度保障",
    "工作基础与既有能力",
    "专利 / 知识产权 / 创新成果",
    "绩效评价材料",
]


def build_score_material_outputs(
    lot_name: str,
    output_dir: str | Path = "outputs",
    project: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = Path(output_dir) / safe_path_name(lot_name) / "outline"
    context = read_json(base / "technical_response_context.json", default={})
    scoring_plan = scoring_plan_from_context(context) if context else read_json(base / "scoring_action_plan.json")
    outline_debug = outline_debug_from_context(context) if context else read_json(base / "outline_debug.json", default={})

    sections = [
        build_matrix_section(section, outline_debug, context)
        for section in order_sections(list(scoring_plan.get("sections") or []))
    ]
    manual_tables = build_manual_review_tables(lot_name, scoring_plan, outline_debug, technical_context=context)
    matrix = {
        "version": "v1",
        "lot_name": lot_name,
        "project_name": scoring_plan.get("project_name") or project_name_from_project(project),
        "generated_from": {
            "technical_response_context": str(base / "technical_response_context.json") if context else "legacy_json_fixture",
        },
        "sections": sections,
        "items": legacy_items(sections),
        "summary": {
            "score_item_count": len(sections),
            "manual_review_table_count": len(manual_tables.get("review_tables") or []),
            "required_performance_slots": 5,
            "required_patent_slots": 2,
        },
    }
    missing_request = build_missing_material_request(
        lot_name,
        matrix,
        manual_tables,
        scoring_plan=scoring_plan,
        technical_context=context,
    )
    files = write_score_material_outputs(base, matrix, manual_tables, missing_request)
    return {
        "lot_name": lot_name,
        "version": "v1",
        "output_dir": str(base),
        "files": files,
        "summary": {
            **matrix["summary"],
            "missing_material_request_count": (missing_request.get("summary") or {}).get("request_count", 0),
            "request_to_count": (missing_request.get("summary") or {}).get("request_to_count", 0),
        },
    }


def build_score_maximization_matrix(
    lot_name: str,
    output_dir: str | Path = "outputs",
    project: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_score_material_outputs(lot_name, output_dir=output_dir, project=project)


def build_matrix_section(
    section: dict[str, Any],
    outline_debug: dict[str, Any],
    technical_context: dict[str, Any],
) -> dict[str, Any]:
    section_title = str(section.get("section_title") or "")
    score_item = str(section.get("score_item") or section_title)
    findings = section.get("kb_material_findings") if isinstance(section.get("kb_material_findings"), dict) else {}
    debug_section = find_debug_section(outline_debug, section_title)
    confirmed = material_texts(findings.get("confirmed_usable"))
    candidate = material_texts(findings.get("needs_manual_review")) + debug_material_texts(debug_section, ["candidate", "weak"])
    not_usable = material_texts(findings.get("not_usable")) + debug_material_texts(debug_section, ["unusable"])

    profile = section_profile(section, technical_context)
    missing = missing_materials(section, profile, confirmed)
    warnings = profile.get("warnings", [])

    return {
        "section_title": section_title,
        "score_item": score_item,
        "score_range": normalize_range(section.get("score_range")),
        "scoring_rule_summary": str(section.get("scoring_rule_summary") or ""),
        "max_score_preparation_target": profile["target"],
        "calculation_or_basis": profile["basis"],
        "required_materials": profile["required_materials"],
        "recommended_materials": profile["recommended_materials"],
        "confirmed_materials": confirmed,
        "candidate_or_weak_materials": unique(candidate),
        "not_usable_materials": unique(not_usable + profile.get("not_usable_materials", [])),
        "missing_materials": missing,
        "owner_hints": profile["owner_hints"],
        "manual_review_focus": profile["manual_review_focus"],
        "writing_response_points": profile["writing_response_points"],
        "source_basis": source_basis(section, technical_context),
        "warnings": unique(warnings),
    }


def section_profile(section: dict[str, Any], technical_context: dict[str, Any]) -> dict[str, Any]:
    title = str(section.get("section_title") or "")
    score_item = str(section.get("score_item") or "")
    required_from_plan = evidence_package_texts(section.get("required_evidence_package"))
    manual_questions = [str(item) for item in list(section.get("manual_review_questions") or [])]
    rule = str(section.get("scoring_rule_summary") or "")

    if "业绩" in title:
        return profile(
            "为达到该评分项上限的材料准备目标：准备5项合格公司业绩。",
            "计算依据：1 项合格业绩得 12 分；每多 1 项加 2 分；最高加 8 分；因此需 1 + 4 = 5 项。",
            required_from_plan or ["5项近5年技术开发或技术服务类公司业绩合同关键页", "验收证明/验收报告/验收单", "中标通知书或客户证明", "名称不一致时的名称变更证明"],
            ["业绩真实性说明", "合同与验收材料对应索引", "评分要素点标注页"],
            ["人员项目经验", "个人证书", "企业资质证书", "供应商绩效评价"],
            ["项目管理/公司业绩管理", "相关业务部门"],
            manual_questions + ["合同主体是否为投标人", "是否近5年", "是否技术开发或技术服务", "人员项目经验不得替代公司业绩"],
            ["在业绩章节按5项合格公司业绩逐项列示合同主体、项目名称、服务内容、时间、验收或客户证明。"],
        )
    if "专利" in title or "知识产权" in title:
        return profile(
            "为达到该项上限的材料准备目标：准备 2 项采购相关发明专利或实用新型专利。",
            "计算依据：每项加 1 分，上限 2 分，因此需准备 2 项。",
            required_from_plan or ["2项采购相关发明专利或实用新型专利证明", "专利证书/受理通知书/官方查询记录"],
            ["专利与采购内容相关性说明", "权利人/申请人与投标主体关系说明"],
            ["技术规范书中的“受理发明专利1项”交付成果", "论文", "软件截图", "人员简历"],
            ["知识产权/研发管理", "相关业务部门"],
            manual_questions + ["技术规范书中的“受理发明专利1项”是交付成果要求，不等同于既有专利评分材料"],
            ["在创新成果章节列示2项既有采购相关专利，说明类型、状态、权属和相关性。"],
        )
    if "项目负责人" in title:
        return profile(
            "为尽量达到该评分项高档评价的材料准备目标：确认1名可投入、专业和经验匹配的项目负责人，并准备完整简历、经验和证书证明。",
            "判断依据：评分规则关注项目负责人专业、工作年限、工作经验等情况；当前候选人员只能作为候选或调试线索，不能直接确认为主责人员。",
            required_from_plan or ["项目负责人简历表", "相关项目经验证明", "相关证书", "可投入性/角色说明"],
            ["证书真实性有效性核验", "主责/强协同部门确认记录"],
            ["未经确认的候选人员线索", "未核验简历", "无法确认投入的人员名单"],
            ["数据管理事业部", "安全事业部", "项目管理"],
            manual_questions + ["当前缺数据管理事业部、安全事业部已确认人员简历"],
            ["项目负责人响应中突出专业背景、工作年限、相关经验、职责边界和可投入性。"],
        )
    if "组织" in title or "团队" in title:
        roles = ["项目负责人", "区块链/可信存证研究", "数据管理/数据治理", "数据安全/隐私保护", "标准研究/国际标准文本", "测试验证/质量控制", "项目管理/沟通协调"]
        return profile(
            "为尽量达到该评分项高档评价的材料准备目标：确认覆盖研究、数据管理、安全、标准研究、测试验证和项目管理的团队配置。",
            "判断依据：评分规则关注项目团队人员结构、专业、工作年限和工作经验；需由主责和强协同部门确认可投入人员。",
            required_from_plan or ["拟委任主要人员汇总表", "主要人员简历表", "相关项目经验", "相关证书"],
            ["角色分工说明", "投入时间安排", "跨部门协同机制"],
            ["未经确认的候选人员线索", "泛化人员名单"],
            ["数据管理事业部", "安全事业部", "项目管理"],
            manual_questions + ["至少预留角色：" + "、".join(roles)],
            ["团队配置章节按角色列出专业方向、经验、证书、职责和协同关系。"],
        )
    if "工作基础" in title:
        return profile(
            "为尽量达到该评分项高档评价的材料准备目标：准备产品化成果、科技成果、专题研究三类与采购项目相关的组织级证明材料。",
            "判断依据：评分规则分别评价与采购项目有关的产品开发、科技成果和专题研究；人员简历项目经验不进入工作基础主体，只能作为 debug 线索。",
            required_from_plan or ["产品化成果证明", "科技成果证明", "专题研究报告/立项/结题材料"],
            ["成果权属说明", "成果与采购项目相关性说明", "成果状态/级别证明"],
            ["人员简历项目经验", "个人证书", "泛化能力描述"],
            ["数据管理事业部", "安全事业部", "相关业务部门"],
            manual_questions + ["产品化成果、科技成果、专题研究三类分别复核"],
            ["工作基础章节按三类成果说明已有能力、权属主体、时间、状态和项目相关性。"],
        )
    if "绩效" in title:
        return profile(
            "为尽量达到该评分项高档评价的材料准备目标：准备最近一年度供应商绩效评价证明材料，并核验主体名称一致性。",
            "判断依据：评分规则依据最近一年度招标人对投标人/联合体牵头人的供应商绩效评价结果分类分级；不能保证评价档位。",
            required_from_plan or ["平台截图", "招标人评价结果", "供应商绩效证明"],
            ["主体名称一致性说明", "评价年度和评价主体说明"],
            ["简历", "资质证书", "业绩合同", "人员项目经验"],
            ["供应商管理/商务支持"],
            manual_questions + ["简历、资质证书、业绩合同不能替代供应商绩效评价"],
            ["绩效评价材料仅列示最近一年度评价结果证明，不用业绩或资质替代。"],
        )
    if "总体服务方案" in title:
        return profile(
            "为尽量达到高档评价的材料准备目标：将技术规范书任务拆解为可执行服务方案、技术路线、质量控制、风险控制和交付成果。",
            "判断依据：评分规则从先进性、创新性、可操作性、技术性、经济性、风险等方面综合评价；需逐项响应技术规范书任务。",
            required_from_plan or ["技术规范书任务拆解表", "技术路线", "研究内容", "验证方案", "交付成果说明"],
            ["质量控制机制", "风险控制机制", "沟通培训安排"],
            ["泛化模板段落", "未对应技术规范书的技术路线"],
            ["投标文件编制人员"],
            manual_questions + ["服务方案需覆盖：" + "、".join(SERVICE_SOLUTION_POINTS)],
            SERVICE_SOLUTION_POINTS,
        )
    if "实施计划" in title or "进度" in title:
        return profile(
            "为尽量达到高档评价的材料准备目标：基于服务期限形成阶段计划、里程碑、质量保障和交付验收安排。",
            "判断依据：服务期限为 2025-07-31 至 2025-12-31；阶段安排由系统基于服务期限生成，需人工复核。",
            required_from_plan or ["阶段进度计划", "里程碑", "质量保障措施", "风险保障措施", "交付验收节点"],
            ["责任人安排", "周/月度沟通机制", "验收准备清单"],
            ["未对应服务期限的泛化进度表"],
            ["投标文件编制人员"],
            manual_questions + ["阶段安排需人工复核"],
            IMPLEMENTATION_PHASES,
            warnings=["阶段日期由服务期限生成，需人工复核。"],
        )
    return profile(
        "为尽量达到高档评价的材料准备目标：准备规范、完整、逻辑一致的技术应答总控材料，并建立评分项、条款来源和材料索引闭环。",
        "判断依据：评分规则关注技术内容的规范性、完整性、逻辑性；需结合技术文件格式与表单要求复核。",
        required_from_plan or ["技术响应目录", "评分项覆盖检查", "材料索引", "技术偏差表/专项应答文件检查"],
        ["章节交叉引用检查", "格式和表单完整性检查"],
        ["投标决策性表述", "无法追溯来源的泛化正文"],
        ["投标文件编制人员"],
        manual_questions + format_requirements_from_context(technical_context),
        ["总控章节检查技术偏差表、项目团队表、业绩表、材料索引和评分点覆盖关系。"],
    )


def profile(
    target: str,
    basis: str,
    required_materials: list[str],
    recommended_materials: list[str],
    not_usable_materials: list[str],
    owner_hints: list[str],
    manual_review_focus: list[str],
    writing_response_points: list[str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "target": target,
        "basis": basis,
        "required_materials": unique(required_materials),
        "recommended_materials": unique(recommended_materials),
        "not_usable_materials": unique(not_usable_materials),
        "owner_hints": unique(owner_hints),
        "manual_review_focus": unique(manual_review_focus),
        "writing_response_points": unique(writing_response_points),
        "warnings": warnings or [],
    }


def write_score_material_outputs(
    base: Path,
    matrix: dict[str, Any],
    manual_tables: dict[str, Any],
    missing_request: dict[str, Any],
) -> dict[str, str]:
    base.mkdir(parents=True, exist_ok=True)
    files = {
        "score_maximization_matrix_json": base / "score_maximization_matrix.json",
        "score_maximization_matrix_markdown": base / "score_maximization_matrix.md",
        "manual_review_tables_json": base / "manual_review_tables.json",
        "manual_review_tables_markdown": base / "manual_review_tables.md",
        "missing_material_request_json": base / "missing_material_request.json",
        "missing_material_request_markdown": base / "missing_material_request.md",
    }
    files["score_maximization_matrix_json"].write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    files["score_maximization_matrix_markdown"].write_text(render_score_maximization_matrix_markdown(matrix, manual_tables, missing_request), encoding="utf-8")
    files["manual_review_tables_json"].write_text(json.dumps(manual_tables, ensure_ascii=False, indent=2), encoding="utf-8")
    files["manual_review_tables_markdown"].write_text(render_manual_review_tables_markdown(manual_tables), encoding="utf-8")
    files["missing_material_request_json"].write_text(json.dumps(missing_request, ensure_ascii=False, indent=2), encoding="utf-8")
    files["missing_material_request_markdown"].write_text(render_missing_material_request_markdown(missing_request), encoding="utf-8")
    return {key: str(value) for key, value in files.items()}


def render_score_maximization_matrix_markdown(
    matrix: dict[str, Any],
    manual_tables: dict[str, Any],
    missing_request: dict[str, Any],
) -> str:
    lines = [
        "# 评分项材料准备矩阵",
        "",
        "# 评分项上限准备矩阵",
        "",
        "别名：评分项上限准备矩阵。",
        "",
        "本文件仅用于材料准备、评分响应和写作准备分析，不输出投标决策，不保证得分。",
        "",
        "## 总览表",
        "",
    ]
    columns = [
        "章节",
        "评分项",
        "分值范围",
        "评分规则摘要",
        "为达到该评分项上限/高档评价的材料准备目标",
        "计算依据或判断依据",
        "必须准备材料",
        "推荐补强材料",
        "当前知识库已确认材料",
        "当前候选/弱证据",
        "不能使用/不能替代材料",
        "缺失材料",
        "责任对象/建议补充部门",
        "人工复核重点",
        "写作响应点",
        "来源依据",
    ]
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("| " + " | ".join(["---"] * len(columns)) + " |")
    for section in list(matrix.get("sections") or []):
        lines.append(
            "| "
            + " | ".join(
                escape_md(value)
                for value in [
                    section.get("section_title"),
                    section.get("score_item"),
                    format_score_range(section.get("score_range")),
                    section.get("scoring_rule_summary"),
                    section.get("max_score_preparation_target"),
                    section.get("calculation_or_basis"),
                    join_md(section.get("required_materials")),
                    join_md(section.get("recommended_materials")),
                    join_md(section.get("confirmed_materials")),
                    join_md(section.get("candidate_or_weak_materials")),
                    join_md(section.get("not_usable_materials")),
                    join_md(section.get("missing_materials")),
                    join_md(section.get("owner_hints")),
                    join_md(section.get("manual_review_focus")),
                    join_md(section.get("writing_response_points")),
                    join_md(section.get("source_basis")),
                ]
            )
            + " |"
        )

    for idx, section in enumerate(list(matrix.get("sections") or []), start=1):
        lines.extend(
            [
                "",
                f"## {idx}. {section.get('section_title') or '-'}",
                "",
                f"- 评分项：{section.get('score_item') or '-'}",
                f"- 分值范围：{format_score_range(section.get('score_range'))}",
                f"- 评分规则摘要：{section.get('scoring_rule_summary') or '-'}",
                f"- {section.get('max_score_preparation_target') or '-'}",
                f"- {section.get('calculation_or_basis') or '-'}",
                "",
                "### 材料准备",
            ]
        )
        lines.extend(bullet_block("必须准备材料", section.get("required_materials")))
        lines.extend(bullet_block("推荐补强材料", section.get("recommended_materials")))
        lines.extend(bullet_block("当前知识库已确认材料", section.get("confirmed_materials")))
        lines.extend(bullet_block("当前候选/弱证据", section.get("candidate_or_weak_materials")))
        lines.extend(bullet_block("不能使用/不能替代材料", section.get("not_usable_materials")))
        lines.extend(bullet_block("缺失材料", section.get("missing_materials")))
        lines.extend(bullet_block("人工复核重点", section.get("manual_review_focus")))
        lines.extend(bullet_block("写作响应点", section.get("writing_response_points")))
        lines.extend(bullet_block("来源依据", section.get("source_basis")))

    lines.extend(render_key_review_tables_for_matrix(manual_tables))
    return "\n".join(lines).rstrip() + "\n"


def render_manual_review_tables_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 人工逐项复核表",
        "",
        "本文件只用于人工逐项复核。`decision_placeholder` 为人工复核结果占位，不是系统投标决策。",
        "",
    ]
    for table in list(payload.get("review_tables") or []):
        rows = list(table.get("rows") or [])
        lines.extend([f"## {table.get('table_name') or '-'}", ""])
        if not rows:
            lines.extend(["暂无复核行。", ""])
            continue
        columns = table_columns(rows)
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("| " + " | ".join(["---"] * len(columns)) + " |")
        for row in rows:
            lines.append("| " + " | ".join(escape_md(row.get(column)) for column in columns) + " |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_key_review_tables_for_matrix(manual_tables: dict[str, Any]) -> list[str]:
    wanted = {"业绩 5 项复核表", "专利 2 项复核表", "项目负责人复核表", "团队人员复核表", "工作基础复核表", "绩效评价材料复核表"}
    lines = ["", "## 关键占位复核表", ""]
    for table in list(manual_tables.get("review_tables") or []):
        if table.get("table_name") not in wanted:
            continue
        rows = list(table.get("rows") or [])
        lines.extend([f"### {table.get('table_name')}", ""])
        columns = table_columns(rows)
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("| " + " | ".join(["---"] * len(columns)) + " |")
        for row in rows:
            lines.append("| " + " | ".join(escape_md(row.get(column)) for column in columns) + " |")
        lines.append("")
    return lines


def inspect_score_maximization_matrix(lot_name: str, output_dir: str | Path = "outputs") -> dict[str, Any]:
    base = Path(output_dir) / safe_path_name(lot_name) / "outline"
    matrix_path = base / "score_maximization_matrix.json"
    tables_path = base / "manual_review_tables.json"
    if not matrix_path.exists():
        raise FileNotFoundError(f"score_maximization_matrix.json not found: {matrix_path}")
    matrix = read_json(matrix_path)
    tables = read_json(tables_path, default={"review_tables": []})
    table_map = {item.get("table_name"): item for item in list(tables.get("review_tables") or [])}
    return {
        "lot_name": lot_name,
        "files": {
            "matrix_json": str(matrix_path),
            "matrix_markdown": str(base / "score_maximization_matrix.md"),
            "manual_review_tables_json": str(tables_path),
            "manual_review_tables_markdown": str(base / "manual_review_tables.md"),
            "matrix_json_exists": matrix_path.exists(),
            "matrix_markdown_exists": (base / "score_maximization_matrix.md").exists(),
            "manual_review_tables_json_exists": tables_path.exists(),
            "manual_review_tables_markdown_exists": (base / "manual_review_tables.md").exists(),
        },
        "summary": matrix.get("summary") or {},
        "items": matrix.get("items") or legacy_items(list(matrix.get("sections") or [])),
        "sections": matrix.get("sections") or [],
        "manual_review_summary": {
            "performance_rows": len(list((table_map.get("业绩 5 项复核表") or {}).get("rows") or [])),
            "patent_rows": len(list((table_map.get("专利 2 项复核表") or {}).get("rows") or [])),
            "project_leader_rows": len(list((table_map.get("项目负责人复核表") or {}).get("rows") or [])),
            "team_rows": len(list((table_map.get("团队人员复核表") or {}).get("rows") or [])),
            "work_foundation_rows": len(list((table_map.get("工作基础复核表") or {}).get("rows") or [])),
            "supplier_performance_rows": len(list((table_map.get("绩效评价材料复核表") or {}).get("rows") or [])),
        },
    }


def inspect_missing_material_requests(lot_name: str, output_dir: str | Path = "outputs") -> dict[str, Any]:
    base = Path(output_dir) / safe_path_name(lot_name) / "outline"
    request_path = base / "missing_material_request.json"
    if not request_path.exists():
        raise FileNotFoundError(f"missing_material_request.json not found: {request_path}")
    payload = read_json(request_path)
    grouped_value = payload.get("grouped_requests") or {}
    grouped = (
        [{"ask_to": name, "requests": rows} for name, rows in grouped_value.items()]
        if isinstance(grouped_value, dict)
        else list(grouped_value)
    )
    return {
        "lot_name": lot_name,
        "files": {
            "json": str(request_path),
            "markdown": str(base / "missing_material_request.md"),
            "json_exists": request_path.exists(),
            "markdown_exists": (base / "missing_material_request.md").exists(),
        },
        "summary": payload.get("summary") or {},
        "request_to": [{"name": item.get("ask_to"), "count": len(list(item.get("requests") or []))} for item in grouped],
        "requests": payload.get("requests") or [],
    }


def missing_materials(section: dict[str, Any], profile_data: dict[str, Any], confirmed: list[str]) -> list[str]:
    title = str(section.get("section_title") or "")
    result = [str(action.get("target_material") or action.get("question") or "") for action in list(section.get("missing_actions") or []) if action]
    if "业绩" in title:
        result.append("需补足5项合格公司业绩材料包")
    elif "专利" in title:
        result.append("需补足2项采购相关既有专利证明材料")
    elif "项目负责人" in title:
        result.append("缺数据管理事业部、安全事业部已确认人员简历")
    elif "团队" in title or "组织" in title:
        result.append("缺覆盖研究/数据管理/安全/标准/测试/项目管理角色的人员确认材料")
    elif "工作基础" in title:
        result.append("缺产品化成果、科技成果、专题研究三类组织级证明材料")
    elif "绩效" in title:
        result.append("缺最近一年度供应商绩效评价证明")
    return unique([item for item in result if item])


def source_basis(section: dict[str, Any], technical_context: dict[str, Any]) -> list[str]:
    basis = []
    if section.get("scoring_rule_summary"):
        basis.append("scoring_action_plan.scoring_rule_summary")
    if section.get("required_evidence_package"):
        basis.append("scoring_action_plan.required_evidence_package")
    if section.get("qualified_performance_criteria"):
        basis.append("scoring_action_plan.qualified_performance_criteria")
    if any(key in str(section.get("section_title") or "") for key in ["总体服务方案", "实施计划", "应答文件技术部分总体评价", "项目负责人", "项目组织", "业绩"]):
        basis.append("technical_response_context.json：评分引用、原文要求、证据、缺失材料和写作任务")
    return basis or ["scoring_action_plan.json"]


def evidence_package_texts(value: Any) -> list[str]:
    result = []
    for item in list(value or []):
        if isinstance(item, dict):
            material_type = item.get("material_type") or "材料"
            checks = "、".join(str(text) for text in list(item.get("check_points") or []))
            result.append(f"{material_type}（{checks}）" if checks else str(material_type))
        else:
            result.append(str(item))
    return result


def material_texts(value: Any) -> list[str]:
    result = []
    for item in list(value or []):
        if isinstance(item, dict):
            text = item.get("material_name") or item.get("file_name") or item.get("name") or item.get("title") or item.get("reason") or item.get("source_file_name")
            if text:
                result.append(str(text))
            else:
                result.append(short_json(item))
        elif item:
            result.append(str(item))
    return result


def debug_material_texts(debug_section: dict[str, Any] | None, groups: list[str]) -> list[str]:
    if not isinstance(debug_section, dict):
        return []
    material_basis = debug_section.get("material_basis") if isinstance(debug_section.get("material_basis"), dict) else {}
    group_data = material_basis.get("groups") if isinstance(material_basis.get("groups"), dict) else {}
    result: list[str] = []
    for group in groups:
        result.extend(material_texts(group_data.get(group)))
    return result


def find_debug_section(outline_debug: dict[str, Any], section_title: str) -> dict[str, Any] | None:
    for section in list(outline_debug.get("sections") or []):
        if str(section.get("section_title") or "") == section_title:
            return section
    return None


def format_requirements_from_context(technical_context: dict[str, Any]) -> list[str]:
    points = []
    refs = [
        ref
        for chapter in list(technical_context.get("chapters") or [])
        for ref in list(chapter.get("response_format_refs") or [])
    ] + list(technical_context.get("global_response_format_refs") or [])
    text = json.dumps(refs, ensure_ascii=False)
    project = technical_context.get("project") if isinstance(technical_context.get("project"), dict) else {}
    if "技术偏差表" in text:
        points.append("复核技术偏差表")
    if "拟委任的主要人员汇总表" in text or "主要人员简历表" in text:
        points.append("复核项目团队情况：拟委任主要人员汇总表、主要人员简历表")
    if "近年完成的类似项目情况表" in text:
        points.append("复核业绩文件：近年完成的类似项目情况表")
    if project.get("service_period"):
        points.append(f"复核服务期限：{project.get('service_period')}")
    return points


def scoring_plan_from_context(context: dict[str, Any]) -> dict[str, Any]:
    project = context.get("project") if isinstance(context.get("project"), dict) else {}
    sections = [dict(chapter.get("action_plan") or {}) for chapter in list(context.get("chapters") or [])]
    return {
        "version": "v0",
        "lot_name": context.get("lot_name"),
        "project_name": project.get("project_name"),
        "sections": sections,
    }


def outline_debug_from_context(context: dict[str, Any]) -> dict[str, Any]:
    sections = []
    for chapter in list(context.get("chapters") or []):
        groups = {"confirmed": [], "candidate": [], "weak": [], "unusable": []}
        status_group = {"confirmed": "confirmed", "candidate": "candidate", "needs_review": "candidate", "weak": "weak", "unusable": "unusable", "missing": "unusable"}
        for item in list(chapter.get("evidence_refs") or []):
            groups[status_group.get(str(item.get("evidence_status") or ""), "candidate")].append(item)
        sections.append(
            {
                "section_title": chapter.get("chapter_title"),
                "person_candidates": list(chapter.get("person_candidates") or []),
                "response_requirement_refs": [
                    item
                    for key in ["tender_requirement_refs", "technical_spec_refs", "response_format_refs"]
                    for item in list(chapter.get(key) or [])
                ],
                "material_basis": {"groups": groups},
                "manual_confirm_items": list(chapter.get("manual_confirm_items") or []),
            }
        )
    return {"version": context.get("schema_version"), "lot_name": context.get("lot_name"), "sections": sections}


def legacy_items(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "section_title": section.get("section_title"),
            "score_item": section.get("score_item"),
            "score_range": section.get("score_range"),
            "max_preparation_goal": section.get("max_score_preparation_target"),
            "calculation_basis": section.get("calculation_or_basis"),
            "required_slots": 5 if "业绩" in str(section.get("section_title")) else 2 if "专利" in str(section.get("section_title")) else None,
            "slot_type": slot_type_for_section(str(section.get("section_title") or "")),
            "required_material_package": section.get("required_materials"),
            "qualification_filter": section.get("manual_review_focus"),
            "current_kb_status": {
                "confirmed_usable_count": len(list(section.get("confirmed_materials") or [])),
                "candidate_count": len(list(section.get("candidate_or_weak_materials") or [])),
                "not_usable_count": len(list(section.get("not_usable_materials") or [])),
            },
            "gap_summary": "；".join(str(item) for item in list(section.get("missing_materials") or [])) or "待人工复核",
            "manual_review_table_id": slot_type_for_section(str(section.get("section_title") or "")),
            "request_to": "；".join(str(item) for item in list(section.get("owner_hints") or [])),
        }
        for section in sections
    ]


def slot_type_for_section(title: str) -> str:
    if "业绩" in title:
        return "performance_review_table"
    if "专利" in title:
        return "patent_review_table"
    if "负责人" in title:
        return "project_leader_review_table"
    if "团队" in title or "组织" in title:
        return "team_review_table"
    if "工作基础" in title:
        return "work_foundation_review_table"
    if "绩效" in title:
        return "supplier_performance_review_table"
    return "solution_task_breakdown_table"


def order_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(section: dict[str, Any]) -> int:
        title = str(section.get("section_title") or "")
        try:
            return MATRIX_ORDER.index(title)
        except ValueError:
            return len(MATRIX_ORDER)

    return sorted(sections, key=key)


def table_columns(rows: list[dict[str, Any]]) -> list[str]:
    preferred = [
        "review_id",
        "section_title",
        "score_item",
        "material_slot",
        "review_question",
        "expected_evidence",
        "current_finding",
        "risk_level",
        "owner_hint",
        "status",
        "decision_placeholder",
        "notes",
    ]
    keys: list[str] = []
    for key in preferred:
        if any(key in row for row in rows):
            keys.append(key)
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    return keys


def bullet_block(title: str, values: Any) -> list[str]:
    lines = [f"#### {title}"]
    items = list(values or [])
    if not items:
        return lines + ["- 暂无已确认信息"]
    return lines + [f"- {item}" for item in items]


def normalize_range(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [value.get("min"), value.get("max")]
    if isinstance(value, (list, tuple)):
        return list(value[:2])
    return [None, None]


def format_score_range(value: Any) -> str:
    score_range = normalize_range(value)
    if len(score_range) >= 2 and score_range[0] is not None and score_range[1] is not None:
        return f"{format_number(score_range[0])}-{format_number(score_range[1])}"
    return "-"


def format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(f"required json not found: {path}")
    return json.loads(path.read_text(encoding="utf-8") or "{}")


def project_name_from_project(project: dict[str, Any] | None) -> str:
    if not isinstance(project, dict):
        return ""
    metadata = project.get("tender_project_metadata") if isinstance(project.get("tender_project_metadata"), dict) else {}
    fields = metadata.get("fields") if isinstance(metadata.get("fields"), dict) else {}
    value = fields.get("project_name")
    if isinstance(value, dict):
        return str(value.get("value") or "")
    return str(value or "")


def safe_path_name(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]+', "_", str(value or "unknown")).strip("_") or "unknown"


def escape_md(value: Any) -> str:
    return str(value if value not in (None, "") else "-").replace("|", "\\|").replace("\n", " ")


def join_md(value: Any) -> str:
    if isinstance(value, list):
        return "；".join(str(item) for item in value if item) or "-"
    return str(value or "-")


def unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def short_json(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text[:180] + ("..." if len(text) > 180 else "")
