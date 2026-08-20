from typing import Any


FAIL_PARSE_MESSAGE = "该文件尚未完成真实文本解析，无法进行招标文件拆解"


def mock_tender_parse(document_text: str) -> dict[str, Any]:
    text = (document_text or "").strip()
    if not is_usable_text(text):
        return {"status": "failed", "message": FAIL_PARSE_MESSAGE}

    sections: list[dict[str, Any]] = []
    disqualification_rules = extract_disqualification_rules(text)

    qualification_items = []
    if contains_any(text, ["资格", "资质", "营业执照", "许可证", "认证", "审查"]):
        qualification_items.append(
            {
                "requirement": extract_requirement(text, ["资格", "资质", "营业执照", "许可证", "认证"]),
                "score_weight": 0,
                "risk_level": "high" if contains_any(text, ["废标", "无效投标", "否决"]) else "medium",
                "is_disqualifying": contains_any(text, ["废标", "无效投标", "否决", "必须", "不得"]),
                "evidence_needed": ["资格证明文件", "相关证书或许可文件"],
                "kb_mapping_hint": ["企业资质库", "资格文件"],
                "generation_instruction": "逐条响应资格审查要求，引用有效资质文件并标明有效期和页码。",
            }
        )
        sections.append({"section_name": "资格审查", "type": "qualification", "weight": 0, "items": qualification_items})

    business_items = []
    if contains_any(text, ["商务", "业绩", "合同", "信用", "服务承诺", "响应表"]):
        business_items.append(
            {
                "requirement": extract_requirement(text, ["商务", "业绩", "合同", "信用", "服务承诺"]),
                "score_weight": infer_score(text, ["商务", "业绩", "合同", "信用"], default=0),
                "risk_level": "medium",
                "is_disqualifying": False,
                "evidence_needed": ["商务响应材料", "合同或业绩证明", "承诺函"],
                "kb_mapping_hint": ["业绩案例库", "常用素材库"],
                "generation_instruction": "生成商务响应内容，按招标要求列明证明材料、项目经验和承诺事项。",
            }
        )
        sections.append({"section_name": "商务标", "type": "business", "weight": sum_scores(business_items), "items": business_items})

    technical_items = []
    if contains_any(text, ["技术", "方案", "参数", "实施", "售后", "培训", "检测报告"]):
        technical_items.append(
            {
                "requirement": extract_requirement(text, ["技术", "方案", "参数", "实施", "售后", "培训", "检测报告"]),
                "score_weight": infer_score(text, ["技术", "方案", "参数", "售后", "培训"], default=0),
                "risk_level": "high" if contains_any(text, ["实质性响应", "不得偏离", "必须满足"]) else "medium",
                "is_disqualifying": contains_any(text, ["实质性响应", "不得偏离", "必须满足", "废标", "否决"]),
                "evidence_needed": ["技术方案", "产品资料", "检测报告", "售后或培训材料"],
                "kb_mapping_hint": ["产品资料库", "常用素材库", "检测报告"],
                "generation_instruction": "按技术要求逐项生成响应，引用产品资料、检测报告和实施服务材料。",
            }
        )
        sections.append({"section_name": "技术标", "type": "technical", "weight": sum_scores(technical_items), "items": technical_items})

    price_items = []
    if contains_any(text, ["报价", "价格", "投标总价", "开标一览表", "分项报价"]):
        price_items.append(
            {
                "requirement": extract_requirement(text, ["报价", "价格", "投标总价", "开标一览表", "分项报价"]),
                "score_weight": infer_score(text, ["报价", "价格"], default=0),
                "risk_level": "high",
                "is_disqualifying": contains_any(text, ["报价不一致", "无效报价", "废标", "不得"]),
                "evidence_needed": ["报价明细表", "开标一览表", "价格核对表"],
                "kb_mapping_hint": ["报价文件", "价格核对清单"],
                "generation_instruction": "生成报价文件核对任务，确保总价、分项价、税率和大小写金额一致。",
            }
        )
        sections.append({"section_name": "价格标", "type": "price", "weight": sum_scores(price_items), "items": price_items})

    if not sections:
        return {
            "status": "failed",
            "message": "真实解析文本中未识别到招标结构、评分项或响应要求，无法生成招标拆解结果",
        }

    generation_tasks = [
        item["generation_instruction"]
        for section in sections
        for item in section.get("items", [])
    ]
    return {
        "status": "success",
        "project_summary": extract_project_summary(text),
        "sections": sections,
        "disqualification_rules": disqualification_rules,
        "generation_tasks": generation_tasks,
    }


def mock_score_table_parse(document_text: str, lot_name: str) -> dict[str, Any]:
    text = (document_text or "").strip()
    if not is_usable_text(text):
        return {
            "status": "failed",
            "message": "评分表尚未完成真实文本解析，无法拆解评分项",
            "lot_name": lot_name,
            "score_items": [],
            "total_score": 0,
            "disqualification_rules": [],
            "generation_tasks": [],
        }

    score_items: list[dict[str, Any]] = []

    def add_item(
        item_id: str,
        category: str,
        item_name: str,
        score: float,
        score_rule: str,
        max_score_strategy: str,
        evidence_needed: list[str],
        kb_mapping_hint: list[str],
        target_section: str,
        generation_instruction: str,
        risk_level: str = "medium",
        is_mandatory: bool = False,
        is_disqualifying: bool = False,
    ) -> None:
        score_items.append(
            {
                "id": item_id,
                "category": category,
                "item_name": item_name,
                "score": score,
                "score_rule": score_rule,
                "max_score_strategy": max_score_strategy,
                "evidence_needed": evidence_needed,
                "kb_mapping_hint": kb_mapping_hint,
                "target_section": target_section,
                "is_mandatory": is_mandatory,
                "is_disqualifying": is_disqualifying,
                "risk_level": risk_level,
                "generation_instruction": generation_instruction,
            }
        )

    if contains_any(text, ["ISO9001", "质量管理体系认证"]):
        add_item(
            "score_business_iso9001",
            "business",
            "质量管理体系认证",
            infer_score(text, ["ISO9001", "质量管理体系认证"], 3),
            "提供有效 ISO9001 或质量管理体系认证证书得分。",
            "引用有效期内证书，说明认证范围与本项目供货、实施或服务相关。",
            ["ISO9001质量管理体系认证证书", "证书有效期页"],
            ["企业资质库", "ISO9001", "质量管理体系认证"],
            "资格证明文件",
            "写明证书名称、编号、有效期、认证范围，并绑定证书页码。",
        )
    if contains_any(text, ["ISO27001", "信息安全管理体系认证"]):
        add_item(
            "score_business_iso27001",
            "business",
            "信息安全管理体系认证",
            infer_score(text, ["ISO27001", "信息安全管理体系认证"], 2),
            "提供有效 ISO27001 或信息安全管理体系认证证书得分。",
            "引用证书并说明信息安全管理能力与项目交付过程的关系。",
            ["ISO27001信息安全管理体系认证证书"],
            ["企业资质库", "ISO27001", "信息安全"],
            "商务响应表",
            "补充信息安全保障说明，绑定证书附件页码。",
        )
    if contains_any(text, ["AAA", "信用等级"]):
        add_item(
            "score_business_credit",
            "business",
            "企业信用",
            infer_score(text, ["AAA", "信用等级"], 2),
            "提供 AAA 信用等级证书或同等信用证明得分。",
            "引用信用等级证明，强调履约能力和诚信记录。",
            ["AAA信用等级证书"],
            ["企业资质库", "信用等级", "AAA"],
            "商务响应表",
            "说明信用等级、颁发机构和有效期。",
            risk_level="low",
        )
    if contains_any(text, ["合同", "业绩"]):
        add_item(
            "score_business_performance",
            "business",
            "类似项目业绩",
            infer_score(text, ["合同", "业绩"], 8),
            "提供类似项目合同、业绩证明或中标证明按档计分。",
            "优先选择与本标段相同或相近的项目，形成业绩表并附证明材料。",
            ["项目合同", "中标通知书", "验收报告"],
            ["业绩案例库", "合同", "中标通知书", "验收报告"],
            "商务响应表",
            "生成类似项目业绩清单，按项目名称、金额、采购人、时间和证明材料页码组织。",
        )
    if contains_any(text, ["专利", "软著", "软件著作权"]):
        add_item(
            "score_technical_ip",
            "technical",
            "技术能力/知识产权",
            infer_score(text, ["专利", "软著", "软件著作权"], 5),
            "提供相关专利、软件著作权等证明材料按档计分。",
            "筛选与项目技术方案相关的知识产权，并说明与项目需求的关联。",
            ["专利证书", "软件著作权证书"],
            ["企业资质库", "专利", "软件著作权"],
            "技术方案",
            "在技术能力章节引用知识产权，说明技术先进性和项目适配性。",
        )
    if "检测报告" in text:
        add_item(
            "score_technical_test_report",
            "technical",
            "产品检测报告",
            infer_score(text, ["检测报告"], 6),
            "提供符合技术参数的检测报告，检测项完整且结论合格得分。",
            "逐项匹配检测报告参数、页码和招标技术要求。",
            ["产品检测报告", "检测项参数页"],
            ["产品资料库", "检测报告", "技术参数"],
            "技术偏离表",
            "生成技术参数逐项响应，引用检测报告页码作为证明。",
            risk_level="high",
            is_mandatory=True,
            is_disqualifying=contains_any(text, ["必须", "废标", "否决", "无效"]),
        )
    if contains_any(text, ["售后", "培训", "响应时间"]):
        add_item(
            "score_technical_service",
            "technical",
            "售后服务方案",
            infer_score(text, ["售后", "培训", "响应时间"], 10),
            "根据响应时间、服务网点、人员配置、培训计划、质保承诺等内容分档评分。",
            "明确响应时间、人员安排、备件保障、培训计划和回访机制。",
            ["售后服务制度", "培训方案模板", "服务承诺函"],
            ["常用素材库", "售后服务", "培训方案", "服务承诺"],
            "售后服务方案",
            "生成售后服务方案，量化响应时间、人员安排、培训计划和闭环回访机制。",
        )
    if contains_any(text, ["价格", "报价"]):
        add_item(
            "score_price_quote",
            "price",
            "价格评分",
            infer_score(text, ["价格", "报价"], 30),
            "按评标基准价、最低评标价或招标文件规定公式计算价格得分。",
            "生成报价一致性核对任务，确保开标一览表、报价明细和分项报价一致。",
            ["开标一览表", "报价明细表"],
            ["报价文件", "价格核对清单"],
            "报价文件",
            "形成报价文件检查清单，核对总价、税率、交付范围和大小写金额。",
            risk_level="high",
            is_mandatory=True,
            is_disqualifying=contains_any(text, ["无效报价", "废标", "否决"]),
        )

    if not score_items:
        add_item(
            "score_default_compliance",
            "compliance",
            "符合性审查",
            0,
            "投标文件应满足招标文件格式、签字盖章、响应有效期等符合性要求。",
            "逐条核对投标文件格式、签章、授权、响应有效期和承诺函。",
            ["投标文件格式检查表", "授权委托书", "承诺函"],
            ["常用素材库", "投标格式", "承诺函"],
            "投标函",
            "生成符合性审查清单，避免格式性废标。",
            risk_level="high",
            is_mandatory=True,
            is_disqualifying=True,
        )

    total_score = sum(float(item["score"]) for item in score_items)
    disqualification_rules = extract_disqualification_rules(text)
    generation_tasks = [
        f"围绕{item['item_name']}生成{item['target_section']}内容，绑定证明材料：{', '.join(item['evidence_needed'])}"
        for item in score_items
    ]
    return {
        "status": "success",
        "lot_name": lot_name,
        "score_table_summary": f"{lot_name}评分表已拆解为{len(score_items)}个可执行得分任务。",
        "score_items": score_items,
        "total_score": total_score,
        "disqualification_rules": disqualification_rules,
        "generation_tasks": generation_tasks,
    }


def mock_tender_project_merge(
    tender_file_id: str,
    score_file_id: str,
    lot_name: str,
    tender_parse: dict[str, Any],
    score_parse: dict[str, Any],
) -> dict[str, Any]:
    qualification_requirements: list[str] = []
    document_structure: list[dict[str, Any]] = []
    for section in tender_parse.get("sections", []):
        document_structure.append(
            {
                "section_name": section.get("section_name", ""),
                "type": section.get("type", ""),
                "weight": section.get("weight", 0),
            }
        )
        if section.get("type") == "qualification":
            qualification_requirements.extend(item.get("requirement", "") for item in section.get("items", []))

    generation_tasks = []
    for index, item in enumerate(score_parse.get("score_items", []), start=1):
        task_type = item.get("category", "technical")
        if task_type == "compliance":
            task_type = "qualification"
        generation_tasks.append(
            {
                "task_id": f"task_{index:03d}",
                "target_section": item.get("target_section", ""),
                "task_type": task_type,
                "related_score_item_ids": [item.get("id", "")],
                "related_requirements": [item.get("score_rule", "")],
                "evidence_needed": item.get("evidence_needed", []),
                "kb_mapping_hint": item.get("kb_mapping_hint", []),
                "generation_instruction": item.get("generation_instruction", ""),
                "status": "pending",
            }
        )

    return {
        "project_name": tender_parse.get("project_summary", "未命名招标项目")[:80],
        "lot_name": lot_name,
        "tender_file_id": tender_file_id,
        "score_file_id": score_file_id,
        "qualification_requirements": qualification_requirements,
        "disqualification_rules": list(
            dict.fromkeys(
                tender_parse.get("disqualification_rules", [])
                + score_parse.get("disqualification_rules", [])
            )
        ),
        "score_items": score_parse.get("score_items", []),
        "document_structure": document_structure,
        "generation_tasks": generation_tasks,
    }


def mock_section_content(section: str, query: str, kb_results: list[dict[str, Any]]) -> dict[str, Any]:
    used_files = [{"file_id": r.get("file_id"), "name": r.get("original_name"), "category": r.get("category")} for r in kb_results]
    return {
        "section_name": section,
        "used_kb_files": used_files,
        "content": f"{section}\n\n本章围绕“{query}”逐项响应，并结合企业知识库资料补充证明材料索引、服务承诺和执行安排。",
        "trace": {"requirements": [query], "kb_files": used_files, "paragraphs": ["响应说明", "证明材料", "实施安排"]},
    }


def mock_review_section(section: str) -> list[dict[str, Any]]:
    return [
        {
            "risk_level": "中风险",
            "risk_type": "评分项未覆盖",
            "description": f"{section}中部分评分项响应深度不足。",
            "related_requirement": "应按评分项逐条响应并绑定证明材料。",
            "suggestion": "补充评分项对应内容、证明材料页码和可量化承诺。",
            "target_section": section,
        }
    ]


def is_usable_text(text: str) -> bool:
    if not text:
        return False
    failure_markers = [
        "Mock Word解析文本",
        "Mock PDF解析文本",
        "Mock OCR文本",
        "Mock解析文本",
        "Mock Word",
        "Mock PDF",
        "Mock OCR",
        "当前解析 .doc 需要安装 LibreOffice",
        "当前解析 .ppt 需要安装 LibreOffice",
        "图片文件需要 OCR 模块",
        "文件解析失败",
        "缺少依赖",
    ]
    return not any(marker in text for marker in failure_markers)


def contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def infer_score(text: str, keywords: list[str], default: float) -> float:
    import re

    for keyword in keywords:
        index = text.find(keyword)
        if index < 0:
            continue
        window = text[max(0, index - 60) : index + 120]
        match = re.search(r"(\d+(?:\.\d+)?)\s*分", window)
        if match:
            return float(match.group(1))
    return default


def sum_scores(items: list[dict[str, Any]]) -> float:
    return sum(float(item.get("score_weight", 0) or 0) for item in items)


def extract_requirement(text: str, keywords: list[str]) -> str:
    normalized = text.replace("\r", "\n")
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    for keyword in keywords:
        for line in lines:
            if keyword in line:
                return line[:300]
    return lines[0][:300] if lines else ""


def extract_disqualification_rules(text: str) -> list[str]:
    lines = [line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip()]
    keywords = ["废标", "无效投标", "否决", "不予受理", "不得", "必须"]
    rules = [line[:300] for line in lines if contains_any(line, keywords)]
    return list(dict.fromkeys(rules))[:20]


def extract_project_summary(text: str) -> str:
    lines = [line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip()]
    for keyword in ["项目名称", "采购项目", "招标项目", "工程名称"]:
        for line in lines:
            if keyword in line:
                return line[:200]
    return lines[0][:200] if lines else ""
