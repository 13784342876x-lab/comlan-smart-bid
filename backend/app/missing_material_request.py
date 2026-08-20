from __future__ import annotations

from collections import OrderedDict
from typing import Any


REQUEST_GROUPS = [
    "数据管理事业部",
    "安全事业部",
    "项目管理/公司业绩管理",
    "相关业务部门",
    "知识产权/研发管理",
    "供应商管理/商务支持",
    "投标文件编制人员",
]


def build_missing_material_request(
    lot_name: str,
    matrix: dict[str, Any],
    manual_tables: dict[str, Any],
    scoring_plan: dict[str, Any] | None = None,
    technical_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sections = list((scoring_plan or {}).get("sections") or matrix.get("sections") or [])
    context_missing = list((technical_context or {}).get("global_missing_materials") or [])
    requests = requests_from_context_missing(context_missing) if context_missing else build_default_requests(sections)
    requests.extend(requests_from_missing_actions(sections))
    requests = dedupe_requests(requests)
    grouped = group_requests(requests)
    return {
        "version": "v1",
        "lot_name": lot_name,
        "grouped_requests": grouped_with_legacy_aliases(grouped),
        "requests": requests,
        "summary": {
            "request_count": len(requests),
            "request_to_count": len({request_group_count_key(name) for name, rows in grouped.items() if rows}),
            "high_priority_count": len([item for item in requests if item.get("priority") == "high"]),
        },
    }


def requests_from_context_missing(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        owner = normalize_ask_to(str(item.get("suggested_owner") or "相关业务部门"))
        rows.append(
            request(
                str(item.get("missing_id") or f"REQ-CONTEXT-{index:03d}"),
                owner,
                str(item.get("priority") or "medium"),
                "、".join(str(value) for value in list(item.get("related_score_item_ids") or [])) or "技术评分项",
                str(item.get("title") or item.get("requested_material_type") or "待补充材料"),
                str(item.get("description") or "请补充对应材料。"),
                str(item.get("reason") or "当前结构化上下文未定位到可用材料。"),
                "材料需有明确来源文件、可定位摘录，并与对应评分项直接相关。",
                [],
                "technical_response_context.json.global_missing_materials",
            )
        )
    return rows


def build_default_requests(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        request(
            "REQ-PERF-01",
            "项目管理/公司业绩管理",
            "high",
            "业绩情况",
            "近5年技术开发或技术服务类公司业绩合同关键页，优先补足5项",
            "请补充近5年技术开发或技术服务类公司业绩合同关键页，优先补足5项；每项需能体现合同主体、项目名称、服务内容、签署/履行时间、技术开发或技术服务内容。如有验收报告、中标通知书或客户证明，请一并提供。",
            "为达到该评分项上限的材料准备目标：准备 5 项合格公司业绩。",
            "每项至少可核对合同主体、项目名称、服务内容、时间和技术开发/技术服务属性；名称不一致时提供名称变更证明。",
            ["人员项目经验", "个人证书", "企业资质证书", "供应商绩效评价"],
            "评分规则：1项合格业绩得12分；每多1项加2分；最高加8分；因此需1+4=5项。",
        ),
        request(
            "REQ-PERF-02",
            "相关业务部门",
            "high",
            "业绩情况",
            "与公司业绩对应的验收证明、中标通知书或客户证明",
            "请按每项合同逐项补充验收证明、验收报告、验收单、中标通知书或客户证明，证明材料需与合同项目名称和服务内容对应。",
            "用于补强公司业绩真实性、履约完成情况和评分标准要求要素点。",
            "证明材料项目名称、客户/业主、验收或证明日期、服务内容与合同可对应。",
            ["人员项目经验", "个人证书", "企业资质证书"],
            "scoring_action_plan.required_evidence_package：acceptance_report、winning_notice_or_customer_certificate。",
        ),
        request(
            "REQ-LEADER-01",
            "数据管理事业部",
            "high",
            "项目负责人情况",
            "项目负责人及核心成员简历、项目经验和证书材料",
            "请确认可投入本项目的数据管理/数据治理方向项目负责人候选人，并补充简历表、工作年限、相关项目经验、相关证书及证书真实有效性说明。",
            "为尽量达到该评分项高档评价的材料准备目标，需要项目负责人专业、工作年限和工作经验材料完整。",
            "至少包含拟任姓名、所属部门、专业背景、工作年限、相关经验、证书、可投入承诺或角色说明。",
            ["调试候选线索", "未核验简历", "无法确认投入的人员名单"],
            "评分规则：对项目负责人专业、工作年限、工作经验等情况进行评审。",
        ),
        request(
            "REQ-TEAM-01",
            "数据管理事业部",
            "high",
            "项目团队人员结构",
            "数据管理、数据治理、标准研究方向团队人员材料",
            "请补充可投入本项目的数据管理/数据治理、区块链/可信存证、标准研究/国际标准文本方向团队成员简历，并确认是否可进入拟委任主要人员汇总表。",
            "为尽量达到团队配置高档评价，需要覆盖研究、数据管理、标准研究和项目管理角色。",
            "每名人员需有所属部门、专业方向、工作年限、相关经验、证书、简历表和可投入性确认。",
            ["仅有姓名的候选线索", "未确认人员简历", "泛化能力描述"],
            "technical_response_outline：项目团队情况需使用拟委任主要人员汇总表和主要人员简历表。",
        ),
        request(
            "REQ-TEAM-02",
            "安全事业部",
            "medium",
            "项目团队人员结构；服务方案；工作基础",
            "数据安全、隐私保护、密码验证、合规验证方向人员和成果材料",
            "请补充数据安全、隐私保护、密态数据完整性验证、Schnorr签名验证、业务逻辑合规性验证方向可投入人员及相关成果证明。",
            "用于支撑团队配置、服务方案技术任务和工作基础材料准备。",
            "人员材料需可核验；成果材料需为组织层面材料并体现与采购项目相关性。",
            ["通用安全资质证书直接替代项目成果", "无项目关联的证书清单"],
            "技术规范书任务：密态数据完整性验证、Schnorr签名验证、业务逻辑合规性验证。",
        ),
        request(
            "REQ-FOUND-01",
            "相关业务部门",
            "medium",
            "工作基础",
            "产品化成果、科技成果、专题研究三类工作基础证明",
            "请按产品化成果、科技成果、专题研究三类补充与本采购项目相关的组织层面成果材料，包括成果名称、权属主体、时间、状态/级别和证明材料。",
            "为尽量达到工作基础高档评价，需要组织层面的既有能力材料，而不是人员简历项目经验。",
            "材料需体现成果名称、与采购项目相关性、证明材料类型、级别/状态、权属主体和时间。",
            ["人员简历项目经验", "个人证书", "泛化能力描述"],
            "评分规则：产品化成果、科技成果、专题研究分别按相关性和级别/状态评价。",
        ),
        request(
            "REQ-PAT-01",
            "知识产权/研发管理",
            "high",
            "专利数量",
            "2项采购相关发明专利或实用新型专利证明材料",
            "请补充与区块链、工业数据管理、数据安全、智能合约或标准研究相关的既有发明专利或实用新型专利证明材料，优先补足2项；每项需提供专利名称、专利号/申请号、权利人/申请人、状态和官方证明材料。",
            "为达到该项上限的材料准备目标：准备 2 项采购相关发明专利或实用新型专利。",
            "每项需可核验专利类型、权利人/申请人、状态、采购相关性和证明材料。",
            ["技术规范书中的“受理发明专利1项”交付成果", "论文", "软件截图", "人员简历"],
            "评分规则：每项加1分，上限2分，因此需准备2项。",
        ),
        request(
            "REQ-SUPP-01",
            "供应商管理/商务支持",
            "high",
            "绩效评价",
            "最近一年度供应商绩效评价证明",
            "请补充最近一年度供应商绩效评价截图、招标人评价结果或供应商绩效证明，并确认评价对象是否为投标人/联合体牵头人、主体名称是否一致、评价等级或分数是否清晰。",
            "用于支撑供应商绩效评价评分材料准备。",
            "至少体现评价年度、评价主体、评价对象、评价等级或分数、证明材料日期和主体名称一致性。",
            ["简历", "资质证书", "业绩合同", "人员项目经验"],
            "评分规则：根据最近一年度招标人对投标人/联合体牵头人的评价结果分类分级打分。",
        ),
        request(
            "REQ-WRITE-01",
            "投标文件编制人员",
            "high",
            "服务方案；工作进度及保证措施；应答文件技术部分总体评价",
            "技术规范书任务拆解和写作响应点确认",
            "请基于任务拆解表确认每项技术任务的技术路线、阶段节点、验证方式和交付成果责任人，覆盖区块链工业数据存证、链上链下协同、主链与企业链协同、安全检索、密态验证、Schnorr签名、合规验证、智能合约、技术报告、专利受理和国际标准初稿。",
            "用于把技术规范书任务转化为服务方案和实施计划的写作输入。",
            "需覆盖全部技术任务、服务期限 2025-07-31 至 2025-12-31、三项交付成果和人工复核的阶段安排。",
            ["泛化技术路线", "未对应技术规范书的模板段落"],
            "technical_response_outline：服务期限、成果要求和技术规范书任务拆解。",
        ),
    ]


def requests_from_missing_actions(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    counter = 1
    for section in sections:
        for action in list(section.get("missing_actions") or []):
            ask_to = normalize_ask_to(str(action.get("ask_to") or "相关业务部门"))
            rows.append(
                request(
                    f"REQ-ACTION-{counter:02d}",
                    ask_to,
                    priority_for_section(str(section.get("section_title") or "")),
                    str(section.get("score_item") or section.get("section_title") or ""),
                    str(action.get("target_material") or "待补充材料"),
                    str(action.get("question") or "请补充对应评分项所需材料。"),
                    str(action.get("reason") or "用于补齐评分响应和材料准备缺口。"),
                    "材料需可核验、主体一致、与评分项直接相关。",
                    cannot_substitute_for_section(str(section.get("section_title") or "")),
                    f"scoring_action_plan.sections[].missing_actions；章节：{section.get('section_title') or '-'}。",
                )
            )
            counter += 1
    return rows


def request(
    request_id: str,
    ask_to: str,
    priority: str,
    related_score_item: str,
    target_material: str,
    request_text: str,
    why_needed: str,
    minimum_acceptance_check: str,
    cannot_substitute_with: list[str],
    source_basis: str,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "ask_to": ask_to,
        "priority": priority,
        "related_score_item": related_score_item,
        "target_material": target_material,
        "request_text": request_text,
        "why_needed": why_needed,
        "minimum_acceptance_check": minimum_acceptance_check,
        "deadline_hint": "建议在投标文件编制前置材料复核阶段完成；具体截止时间需由项目经理确认。",
        "cannot_substitute_with": cannot_substitute_with,
        "source_basis": source_basis,
    }


def normalize_ask_to(value: str) -> str:
    if "业绩" in value or "项目管理" in value:
        return "项目管理/公司业绩管理"
    if "知识产权" in value or "研发" in value:
        return "知识产权/研发管理"
    if "供应商" in value or "商务" in value:
        return "供应商管理/商务支持"
    if "安全" in value:
        return "安全事业部"
    if "数据" in value or "主责" in value:
        return "数据管理事业部"
    return "相关业务部门"


def priority_for_section(section_title: str) -> str:
    if any(key in section_title for key in ["业绩", "专利", "绩效", "负责人"]):
        return "high"
    return "medium"


def cannot_substitute_for_section(section_title: str) -> list[str]:
    if "业绩" in section_title:
        return ["人员项目经验", "个人证书", "企业资质证书", "供应商绩效评价"]
    if "专利" in section_title:
        return ["技术规范书中的“受理发明专利1项”交付成果", "论文", "软件截图", "人员简历"]
    if "绩效" in section_title:
        return ["简历", "资质证书", "业绩合同"]
    if "工作基础" in section_title:
        return ["人员简历项目经验", "个人证书", "泛化能力描述"]
    return ["未经核验的候选线索", "与评分项无直接关系的材料"]


def dedupe_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in requests:
        key = (str(item.get("ask_to")), str(item.get("related_score_item")), str(item.get("target_material")))
        if key in seen:
            continue
        result.append(item)
        seen.add(key)
    for idx, item in enumerate(result, start=1):
        if str(item.get("request_id", "")).startswith("REQ-ACTION"):
            item["request_id"] = f"REQ-ACTION-{idx:02d}"
    return result


def group_requests(requests: list[dict[str, Any]]) -> OrderedDict[str, list[dict[str, Any]]]:
    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict((name, []) for name in REQUEST_GROUPS)
    for item in requests:
        ask_to = str(item.get("ask_to") or "相关业务部门")
        if ask_to not in grouped:
            grouped[ask_to] = []
        grouped[ask_to].append(item)
    return grouped


def grouped_with_legacy_aliases(grouped: OrderedDict[str, list[dict[str, Any]]]) -> OrderedDict[str, list[dict[str, Any]]]:
    result: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for ask_to, rows in grouped.items():
        result[ask_to] = rows
        for alias in legacy_aliases(ask_to):
            result[alias] = rows
    return result


def request_group_count_key(name: str) -> str:
    if name in {"项目管理/公司业绩管理", "相关业务部门"}:
        return "公司业绩/项目管理/相关业务部门"
    return name


def render_missing_material_request_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 缺失材料请求清单",
        "",
        f"- 标段/包号：{payload.get('lot_name') or '-'}",
        f"- 请求条目：{(payload.get('summary') or {}).get('request_count', 0)}",
        "- 说明：本清单仅用于材料准备、评分响应和写作准备分析，不构成投标决策，也不保证得分。",
        "",
    ]
    grouped = payload.get("grouped_requests") or {}
    groups = grouped.items() if isinstance(grouped, dict) else [
        (group.get("ask_to") or "-", list(group.get("requests") or [])) for group in list(grouped)
    ]
    for ask_to, requests in groups:
        requests = list(requests or [])
        lines.extend([f"## {ask_to}", ""])
        if not requests:
            lines.extend(["当前无单独请求。", ""])
            continue
        for item in requests:
            lines.extend(
                [
                    f"### {item.get('request_id')}（{priority_label(item.get('priority'))}）",
                    "",
                    f"- 关联评分项：{item.get('related_score_item') or '-'}",
                    f"- 目标材料：{item.get('target_material') or '-'}",
                    f"- 可直接发送文本：{item.get('request_text') or '-'}",
                    f"- 用途：{item.get('why_needed') or '-'}",
                    f"- 最低验收检查：{item.get('minimum_acceptance_check') or '-'}",
                    f"- 截止提示：{item.get('deadline_hint') or '-'}",
                    f"- 来源依据：{item.get('source_basis') or '-'}",
                    "- 不得替代为：" + "；".join(str(text) for text in list(item.get("cannot_substitute_with") or [])),
                    "- 不能替代材料：" + "；".join(str(text) for text in list(item.get("cannot_substitute_with") or [])),
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def priority_label(value: Any) -> str:
    return {"high": "高优先级", "medium": "中优先级", "low": "低优先级"}.get(str(value or ""), str(value or "-"))


def legacy_aliases(ask_to: str) -> list[str]:
    aliases = {
        "项目管理/公司业绩管理": ["公司业绩/项目管理/相关业务部门"],
        "知识产权/研发管理": ["知识产权/研发管理/相关业务部门"],
        "供应商管理/商务支持": ["供应商管理/商务支持部门"],
        "投标文件编制人员": ["技术方案编写负责人"],
    }
    return aliases.get(ask_to, [])
