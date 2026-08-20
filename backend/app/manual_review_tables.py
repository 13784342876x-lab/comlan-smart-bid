from __future__ import annotations

from typing import Any


SERVICE_SOLUTION_POINTS = [
    "招标要求理解",
    "总体技术路线",
    "区块链工业数据存证",
    "链上链下协同存储",
    "主链与企业链协同",
    "工业数据安全检索",
    "密态数据完整性验证",
    "Schnorr 签名验证",
    "业务逻辑合规性验证",
    "智能合约组件",
    "国际标准初稿形成路径",
    "质量控制",
    "风险控制",
    "沟通机制",
    "培训与技术服务",
    "交付成果",
]


IMPLEMENTATION_PHASES = [
    "2025-07-31 至 2025-08：启动调研与需求确认",
    "2025-09：方案设计与关键技术研究",
    "2025-10：原型/组件设计与验证",
    "2025-11：技术报告、专利受理、标准初稿形成",
    "2025-12 至 2025-12-31：验收准备、成果提交、最终交付",
]


TECHNICAL_SPEC_TASKS = [
    "基于区块链的工业数据存证技术",
    "链上链下协同安全存储",
    "主链和企业链协同存储",
    "工业数据信息检索安全",
    "同态哈希完整性验证",
    "Schnorr 签名数字签名验证",
    "业务逻辑合规性验证",
    "智能合约组件",
    "技术报告",
    "受理发明专利1项",
    "国际标准初稿",
]


TEAM_ROLES = [
    "项目负责人",
    "区块链/可信存证研究",
    "数据管理/数据治理",
    "数据安全/隐私保护",
    "标准研究/国际标准文本",
    "测试验证/质量控制",
    "项目管理/沟通协调",
]


def build_manual_review_tables(
    lot_name: str,
    scoring_plan: dict[str, Any],
    outline_debug: dict[str, Any] | None = None,
    technical_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    outline_debug = outline_debug or {}
    technical_context = technical_context or {}
    sections = list(scoring_plan.get("sections") or [])
    debug_sections = list(outline_debug.get("sections") or [])

    tables = [
        build_performance_review_table(find_section(sections, "业绩"), debug_sections),
        build_patent_review_table(find_section(sections, "专利"), debug_sections),
        build_project_leader_review_table(find_section(sections, "项目负责人"), debug_sections),
        build_team_review_table(find_section(sections, "团队"), debug_sections),
        build_work_foundation_review_table(find_section(sections, "工作基础"), debug_sections),
        build_supplier_performance_review_table(find_section(sections, "绩效"), debug_sections),
        build_solution_plan_review_table(sections),
        build_clause_source_review_table(sections, outline_debug),
    ]
    context_items = collect_context_manual_confirm_items(technical_context)
    if context_items:
        tables.append(build_context_manual_confirm_table(context_items))
    return {
        "version": "v1",
        "lot_name": lot_name,
        "review_tables": tables,
        "tables": legacy_tables(tables),
        "summary": {
            "table_count": len(tables),
            "performance_slots": 5,
            "patent_slots": 2,
            "project_leader_rows": len(tables[2]["rows"]),
            "team_rows": len(tables[3]["rows"]),
            "work_foundation_rows": len(tables[4]["rows"]),
            "supplier_performance_rows": len(tables[5]["rows"]),
            "context_manual_confirm_rows": len(context_items),
        },
    }


def collect_context_manual_confirm_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    return list(context.get("global_manual_confirm_items") or []) + [
        item
        for chapter in list(context.get("chapters") or [])
        for item in list(chapter.get("manual_confirm_items") or [])
    ]


def build_context_manual_confirm_table(items: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        review_row(
            review_id=str(item.get("confirm_id") or f"CONTEXT-{index:03d}"),
            section_title=str(item.get("related_field") or item.get("category") or "全局"),
            score_item="结构化上下文人工确认",
            material_slot=str(item.get("related_field") or item.get("category") or "待确认项"),
            review_question=str(item.get("question") or "请人工复核。"),
            expected_evidence="核对 source_refs 对应原始文件，并记录最终确认值。",
            current_finding=str(item.get("current_value") or item.get("reason") or ""),
            risk_level=str(item.get("priority") or "medium"),
            owner_hint="项目经理/投标文件编制人员",
            status="needs_review",
            extra={"candidate_values": list(item.get("candidate_values") or []), "source_refs": list(item.get("source_refs") or [])},
        )
        for index, item in enumerate(items, start=1)
    ]
    return review_table("结构化上下文人工确认表", "全局及技术评分章节", "人工确认", rows)


def build_performance_review_table(
    section: dict[str, Any] | None,
    debug_sections: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = []
    for idx in range(1, 6):
        rows.append(
            review_row(
                review_id=f"PERF-{idx:02d}",
                section_title="业绩与项目经验",
                score_item=(section or {}).get("score_item") or "业绩情况",
                material_slot=f"业绩{idx}",
                review_question="该项是否为近5年技术开发或技术服务类公司业绩，且证明材料完整？",
                expected_evidence="合同关键页、验收证明/验收报告/验收单、中标通知书或客户证明，必要时补充名称变更证明。",
                current_finding=finding_summary(section),
                risk_level="high",
                owner_hint="项目管理/公司业绩管理",
                status="missing",
                extra={
                    "序号": f"业绩{idx}",
                    "项目名称": "",
                    "合同主体是否为投标人": "",
                    "是否近5年": "",
                    "是否技术开发或技术服务": "",
                    "合同关键页是否齐全": "",
                    "是否有验收证明": "",
                    "是否有中标通知书/客户证明": "",
                    "是否体现评分标准要求要素点": "",
                    "是否存在企业名称不一致": "",
                    "是否需要名称变更证明": "",
                    "当前状态：已确认/候选/缺失/不适用": "缺失",
                    "复核结论": "",
                    "备注": "人员项目经验不得替代公司业绩；个人证书、企业资质证书、供应商绩效评价不得替代公司业绩。",
                },
            )
        )
    return review_table("业绩 5 项复核表", "业绩与项目经验", (section or {}).get("score_item") or "业绩情况", rows)


def build_patent_review_table(
    section: dict[str, Any] | None,
    debug_sections: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = []
    for idx in range(1, 3):
        rows.append(
            review_row(
                review_id=f"PAT-{idx:02d}",
                section_title="专利 / 知识产权 / 创新成果",
                score_item=(section or {}).get("score_item") or "专利数量",
                material_slot=f"专利{idx}",
                review_question="该项是否为采购相关发明专利或实用新型专利，且权属和状态可核验？",
                expected_evidence="专利证书、受理通知书或官方查询记录，需体现专利名称、专利号/申请号、权利人/申请人、状态和采购相关性。",
                current_finding=finding_summary(section),
                risk_level="high",
                owner_hint="知识产权/研发管理",
                status="missing",
                extra={
                    "序号": f"专利{idx}",
                    "专利名称": "",
                    "专利类型：发明/实用新型": "",
                    "专利号/申请号": "",
                    "权利人/申请人是否为投标人或可解释主体": "",
                    "状态：授权/受理/公开/其他": "",
                    "是否与采购内容相关": "",
                    "证明材料：专利证书/受理通知书/官方查询记录": "",
                    "当前状态：已确认/候选/缺失": "缺失",
                    "复核结论": "",
                    "备注": "技术规范书中的“受理发明专利1项”是交付成果要求，不等同于既有专利评分材料。",
                },
            )
        )
    return review_table("专利 2 项复核表", "专利 / 知识产权 / 创新成果", (section or {}).get("score_item") or "专利数量", rows)


def build_project_leader_review_table(
    section: dict[str, Any] | None,
    debug_sections: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = collect_person_candidates(debug_sections, leader_only=True)
    rows = []
    if not candidates:
        candidates = [{}]
    for idx, person in enumerate(candidates, start=1):
        rows.append(
            review_row(
                review_id=f"LEADER-{idx:02d}",
                section_title="项目负责人情况",
                score_item=(section or {}).get("score_item") or "项目负责人情况",
                material_slot="项目负责人",
                review_question="拟任项目负责人是否已由主责/强协同部门确认，且简历、经验、证书真实有效？",
                expected_evidence="项目负责人简历表、工作年限和相关经验说明、相关项目经验证明、相关证书及真实性有效性核验记录。",
                current_finding=person_finding(person) or "当前候选人员只能作为候选或调试线索，不能直接确认为主责人员；当前缺数据管理事业部、安全事业部已确认人员简历。",
                risk_level="high",
                owner_hint="数据管理事业部/安全事业部/项目管理",
                status="needs_review" if person else "missing",
                extra={
                    "姓名": person.get("name", ""),
                    "拟任姓名": person.get("name", ""),
                    "所属部门": person.get("department", ""),
                    "是否来自建议主责/强协同部门": department_strength(person),
                    "拟任角色": "项目负责人",
                    "专业背景": join_list(person.get("skills")),
                    "工作年限": person.get("work_years", ""),
                    "相关工作经验": "",
                    "相关项目经验": join_list(person.get("project_experience")),
                    "相关证书": join_list(person.get("certificates")),
                    "证书是否真实有效": "",
                    "是否有简历表": "",
                    "是否可用于项目负责人评分": "",
                    "当前状态：已确认/候选/缺失": "候选" if person else "缺失",
                    "复核结论": "",
                    "备注": "候选人员需人工确认可投入性、档期、角色和项目方向匹配度。",
                },
            )
        )
    return review_table("项目负责人复核表", "项目负责人情况", (section or {}).get("score_item") or "项目负责人情况", rows)


def build_team_review_table(
    section: dict[str, Any] | None,
    debug_sections: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = collect_person_candidates(debug_sections, leader_only=False)
    rows = []
    for idx, role in enumerate(TEAM_ROLES, start=1):
        person = candidates[idx - 1] if idx - 1 < len(candidates) else {}
        rows.append(
            review_row(
                review_id=f"TEAM-{idx:02d}",
                section_title="项目组织与团队配置",
                score_item=(section or {}).get("score_item") or "项目团队人员结构",
                material_slot=role,
                review_question=f"{role}角色是否已有可投入人员，并具备对应专业、年限、经验和简历证明？",
                expected_evidence="拟委任主要人员汇总表、主要人员简历表、相关项目经验、相关证书。",
                current_finding=person_finding(person) or "当前缺数据管理事业部、安全事业部已确认人员简历。",
                risk_level="high" if role in {"项目负责人", "数据管理/数据治理", "数据安全/隐私保护"} else "medium",
                owner_hint="数据管理事业部/安全事业部/项目管理",
                status="needs_review" if person else "missing",
                extra={
                    "姓名": person.get("name", ""),
                    "角色": role,
                    "拟任角色": role,
                    "拟任姓名": person.get("name", ""),
                    "所属部门": person.get("department", ""),
                    "专业方向": join_list(person.get("skills")),
                    "工作年限": person.get("work_years", ""),
                    "相关经验": join_list(person.get("project_experience")),
                    "证书": join_list(person.get("certificates")),
                    "是否覆盖研究/数据管理/安全/项目管理": "",
                    "是否有主要人员简历表": "",
                    "是否有主要人员汇总表": "",
                    "当前状态": "候选" if person else "缺失",
                    "复核结论": "",
                    "备注": "候选人员需人工确认，不能仅凭调试线索直接写入正式响应。",
                },
            )
        )
    return review_table("团队人员复核表", "项目组织与团队配置", (section or {}).get("score_item") or "项目团队人员结构", rows)


def build_work_foundation_review_table(
    section: dict[str, Any] | None,
    debug_sections: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = []
    for idx, kind in enumerate(["产品化成果", "科技成果", "专题研究"], start=1):
        rows.append(
            review_row(
                review_id=f"FOUND-{idx:02d}",
                section_title="工作基础与既有能力",
                score_item=(section or {}).get("score_item") or "工作基础",
                material_slot=kind,
                review_question=f"是否存在与采购项目相关、组织层面的{kind}证明材料？",
                expected_evidence="产品化证明、科技成果证明、专题研究报告、立项/结题材料或其他组织层面可核验证明。",
                current_finding=finding_summary(section),
                risk_level="medium",
                owner_hint="数据管理事业部/安全事业部/相关业务部门",
                status="missing",
                extra={
                    "类别": kind,
                    "成果名称": "",
                    "与采购项目相关性": "",
                    "证明材料类型": "",
                    "级别/状态": "",
                    "权属主体": "",
                    "时间": "",
                    "当前状态：已确认/候选/缺失": "缺失",
                    "是否可用于本评分项": "",
                    "复核结论": "",
                    "备注": "人员简历项目经验不进入工作基础主体，只能作为 debug 线索。",
                },
            )
        )
    return review_table("工作基础复核表", "工作基础与既有能力", (section or {}).get("score_item") or "工作基础", rows)


def build_supplier_performance_review_table(
    section: dict[str, Any] | None,
    debug_sections: list[dict[str, Any]],
) -> dict[str, Any]:
    row = review_row(
        review_id="SUPP-01",
        section_title="绩效评价材料",
        score_item=(section or {}).get("score_item") or "绩效评价",
        material_slot="最近一年度供应商绩效评价",
        review_question="是否有最近一年度招标人对投标人/联合体牵头人的供应商绩效评价材料？",
        expected_evidence="平台截图、招标人评价结果或供应商绩效证明，需体现评价年度、评价主体、评价对象、评价等级或分数、材料日期。",
        current_finding=finding_summary(section),
        risk_level="high",
        owner_hint="供应商管理/商务支持",
        status="missing",
        extra={
            "评价年度": "",
            "评价主体": "",
            "评价对象是否为投标人/联合体牵头人": "",
            "评价等级或分数": "",
            "证明材料类型：平台截图/招标人评价结果/供应商绩效证明": "",
            "材料日期": "",
            "主体名称是否一致": "",
            "当前状态：已确认/缺失/不可替代": "缺失",
            "复核结论": "",
            "备注": "简历、资质证书、业绩合同不能替代供应商绩效评价。",
        },
    )
    return review_table("绩效评价材料复核表", "绩效评价材料", (section or {}).get("score_item") or "绩效评价", [row])


def build_solution_plan_review_table(sections: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    solution_section = find_section(sections, "总体服务方案")
    plan_section = find_section(sections, "实施计划")
    for idx, point in enumerate(SERVICE_SOLUTION_POINTS, start=1):
        rows.append(
            review_row(
                review_id=f"SOL-{idx:02d}",
                section_title="总体服务方案",
                score_item=(solution_section or {}).get("score_item") or "服务方案",
                material_slot=point,
                review_question=f"服务方案是否已形成“{point}”的可写作响应点？",
                expected_evidence="技术规范书任务拆解、技术路线、研究内容、质量控制、风险控制、交付成果。",
                current_finding="需由技术方案编写人员基于技术规范书形成写作输入。",
                risk_level="medium",
                owner_hint="投标文件编制人员/技术方案编写负责人",
                status="needs_review",
                extra={"任务名称": point, "响应点类型": "服务方案", "响应点": point, "人工复核要求": "确认是否写入对应章节并可追溯至技术规范书。"},
            )
        )
    for idx, task in enumerate(TECHNICAL_SPEC_TASKS, start=1):
        rows.append(
            review_row(
                review_id=f"TASK-{idx:02d}",
                section_title="总体服务方案 / 实施计划与进度保障",
                score_item="技术规范书任务拆解",
                material_slot=task,
                review_question=f"是否已将技术规范书任务“{task}”转化为服务方案和实施计划响应点？",
                expected_evidence="技术规范书任务原文、技术路线、阶段节点、验证方式、交付成果或写作输入。",
                current_finding="需由技术方案编写人员确认任务拆解和写作口径。",
                risk_level="medium",
                owner_hint="投标文件编制人员/技术方案编写负责人",
                status="needs_review",
                extra={
                    "任务名称": task,
                    "响应点类型": "技术规范书任务",
                    "响应点": task,
                    "状态": "needs_writing",
                    "人工复核要求": "确认是否对应技术规范书原文、服务方案章节和实施计划节点。",
                },
            )
        )
    for idx, phase in enumerate(IMPLEMENTATION_PHASES, start=1):
        rows.append(
            review_row(
                review_id=f"PLAN-{idx:02d}",
                section_title="实施计划与进度保障",
                score_item=(plan_section or {}).get("score_item") or "工作进度及保证措施",
                material_slot=phase,
                review_question=f"实施计划是否覆盖阶段“{phase}”？",
                expected_evidence="基于服务期限 2025-07-31 至 2025-12-31 的阶段计划、里程碑、交付物和保障措施。",
                current_finding="阶段日期按已解析服务期限生成，需人工复核。",
                risk_level="medium",
                owner_hint="投标文件编制人员/技术方案编写负责人",
                status="needs_review",
                extra={"任务名称": phase, "响应点类型": "实施计划", "阶段": phase, "人工复核要求": "需人工复核日期、里程碑和交付物。"},
            )
        )
    return review_table("服务方案/实施计划响应点复核表", "总体服务方案 / 实施计划与进度保障", "服务方案；工作进度及保证措施", rows)


def build_clause_source_review_table(
    sections: list[dict[str, Any]],
    outline_debug: dict[str, Any],
) -> dict[str, Any]:
    rows = []
    for idx, section in enumerate(sections, start=1):
        rows.append(
            review_row(
                review_id=f"SRC-{idx:02d}",
                section_title=section.get("section_title") or "",
                score_item=section.get("score_item") or "",
                material_slot="条款来源",
                review_question="评分规则、格式要求、技术任务和材料要求来源是否已人工复核？",
                expected_evidence="scoring_action_plan、technical_response_outline、outline_debug 中对应评分规则和原文要求。",
                current_finding=source_finding(section, outline_debug),
                risk_level="low",
                owner_hint="投标文件编制人员",
                status="needs_review",
                extra={"来源复核重点": "确认条款来源可追溯，过滤条款未误入材料表。"},
            )
        )
    return review_table("条款来源复核表", "全部评分项", "条款来源复核", rows)


def review_table(table_name: str, section_title: str, score_item: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "table_name": table_name,
        "section_title": section_title,
        "score_item": score_item,
        "rows": rows,
    }


def review_row(
    *,
    review_id: str,
    section_title: str,
    score_item: str,
    material_slot: str,
    review_question: str,
    expected_evidence: str,
    current_finding: str,
    risk_level: str,
    owner_hint: str,
    status: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "review_id": review_id,
        "section_title": section_title,
        "score_item": score_item,
        "material_slot": material_slot,
        "review_question": review_question,
        "expected_evidence": expected_evidence,
        "current_finding": current_finding,
        "risk_level": risk_level,
        "owner_hint": owner_hint,
        "status": status,
        "状态": status,
        "decision_placeholder": "通过/不通过/需补充/不适用",
        "notes": "",
    }
    if extra:
        row.update(extra)
    return row


def legacy_tables(review_tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    id_map = {
        "业绩 5 项复核表": "performance_review_table",
        "专利 2 项复核表": "patent_review_table",
        "项目负责人复核表": "project_leader_review_table",
        "团队人员复核表": "team_review_table",
        "工作基础复核表": "work_foundation_review_table",
        "绩效评价材料复核表": "supplier_performance_review_table",
        "服务方案/实施计划响应点复核表": "solution_task_breakdown_table",
        "条款来源复核表": "clause_source_review_table",
        "结构化上下文人工确认表": "context_manual_confirm_table",
    }
    legacy = [
        {
            "table_id": id_map.get(table["table_name"], table["table_name"]),
            "title": table["table_name"],
            "rows": table["rows"],
            "guardrails": guardrails_for_table(table["table_name"]),
        }
        for table in review_tables
    ]
    people_rows: list[dict[str, Any]] = []
    for table in review_tables:
        if table["table_name"] in {"项目负责人复核表", "团队人员复核表"}:
            for row in table["rows"]:
                legacy_row = dict(row)
                if table["table_name"] == "团队人员复核表":
                    legacy_row["拟任角色"] = "主要团队成员"
                people_rows.append(legacy_row)
    if people_rows:
        legacy.append(
            {
                "table_id": "personnel_review_table",
                "title": "人员复核表",
                "rows": people_rows,
                "guardrails": ["候选人员只能作为人工复核线索，不能直接确认为主责人员。"],
            }
        )
    return legacy


def guardrails_for_table(table_name: str) -> list[str]:
    if "业绩" in table_name:
        return ["人员项目经验不得进入此表。", "人员项目经验不得替代公司业绩。", "个人证书、企业资质证书、供应商绩效评价不得替代公司业绩。"]
    if "专利" in table_name:
        return ["技术规范书中的“受理发明专利1项”是交付成果要求，不等同于既有专利评分材料。"]
    if "工作基础" in table_name:
        return ["人员简历项目经验不进入工作基础主体，只能作为 debug 线索。"]
    if "绩效" in table_name:
        return ["资质证书、简历、业绩合同不得替代供应商绩效评价。", "简历、资质证书、业绩合同不能替代供应商绩效评价。"]
    return []


def find_section(sections: list[dict[str, Any]], keyword: str) -> dict[str, Any] | None:
    for section in sections:
        text = f"{section.get('section_title') or ''} {section.get('score_item') or ''}"
        if keyword in text:
            return section
    return None


def collect_person_candidates(debug_sections: list[dict[str, Any]], *, leader_only: bool) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for section in debug_sections:
        title = str(section.get("section_title") or "")
        if leader_only and "项目负责人" not in title:
            continue
        if not leader_only and not any(key in title for key in ["项目负责人", "项目组织", "团队"]):
            continue
        for candidate in list(section.get("person_candidates") or []):
            name = str(candidate.get("name") or "").strip()
            if not name or name in seen:
                continue
            result.append(candidate)
            seen.add(name)
    return result


def finding_summary(section: dict[str, Any] | None) -> str:
    if not isinstance(section, dict):
        return "当前未确认可用材料，需人工补充。"
    findings = section.get("kb_material_findings") if isinstance(section.get("kb_material_findings"), dict) else {}
    confirmed = len(list(findings.get("confirmed_usable") or []))
    candidate = len(list(findings.get("needs_manual_review") or []))
    unusable = len(list(findings.get("not_usable") or []))
    return f"已确认 {confirmed} 项；候选/弱证据 {candidate} 项；不能使用/不能替代 {unusable} 项。"


def source_finding(section: dict[str, Any], outline_debug: dict[str, Any]) -> str:
    external_refs = list(section.get("external_references") or [])
    if external_refs:
        return "存在外部引用或缺失来源需复核：" + "；".join(str(item.get("reference_text") or item) for item in external_refs[:3])
    return "评分规则和材料要求来自 scoring_action_plan；需人工抽查对应原文。"


def person_finding(person: dict[str, Any]) -> str:
    if not person:
        return ""
    parts = [str(person.get("name") or ""), str(person.get("department") or ""), join_list(person.get("skills"))]
    return "候选线索：" + " / ".join(part for part in parts if part)


def department_strength(person: dict[str, Any]) -> str:
    status = str(person.get("department_match_status") or "")
    if status == "primary_department":
        return "建议主责部门"
    if status == "secondary_department":
        return "强协同部门"
    if status:
        return "跨部门候选，需复核"
    return ""


def join_list(value: Any, limit: int = 5) -> str:
    if isinstance(value, list):
        return "；".join(str(item) for item in value[:limit] if item)
    return str(value or "")
