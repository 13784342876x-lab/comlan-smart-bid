from __future__ import annotations

import re
from typing import Any

from app.vector_store import VectorStore


DEFAULT_SEARCH_LIMIT = 5
LOW_SCORE_THRESHOLD = 30
MEDIUM_SCORE_THRESHOLD = 45
HIGH_SCORE_THRESHOLD = 70

STOPWORDS = {
    "材料",
    "证明",
    "文件",
    "相关",
    "情况",
    "进行",
    "评价",
    "响应",
    "投标",
    "提供",
    "本项",
    "项目",
    "要求",
    "综合",
    "评分",
    "内容",
}

EVIDENCE_ALIASES = {
    "公共信用信息报告": ["公共信用信息报告", "信用中国报告", "信用信息报告"],
    "企业信用信息公示报告": ["企业信用信息公示报告", "国家企业信用信息公示系统", "企业信用报告"],
    "相关承诺函": ["承诺函", "声明函", "无不良行为承诺"],
    "绿色发展规划": ["绿色发展规划", "低碳发展规划", "绿色低碳规划"],
    "绿色管理体系认证": [
        "绿色管理体系认证",
        "能源管理体系",
        "质量管理体系",
        "环境管理体系",
        "职业健康安全管理体系",
        "ISO9001",
        "ISO14001",
        "ISO45001",
        "ISO50001",
    ],
    "环境影响评价材料": ["环境影响评价", "环评报告", "能评报告", "废水", "废气", "废固", "ESG报告"],
    "绿电绿证": ["绿电", "绿证", "绿色电力证书"],
    "创新成果证明": ["创新成果", "科技成果", "专利", "软件著作权", "软著", "知识产权"],
    "研发制度": ["研发制度", "研发管理制度", "创新激励机制", "研发激励"],
    "研发团队名单": ["研发团队名单", "研发人员名单", "研发团队", "高级职称", "高级技师"],
    "科研经费证明": ["科研经费", "研发投入", "研发费用", "审计报告"],
    "企业资质证明": ["营业执照", "企业资质", "资质证书", "高新技术企业", "质量奖", "AAA信用"],
    "人员与资源证明": ["人员名单", "项目负责人", "团队人员", "社保证明", "资格证书", "职称证书"],
    "服务能力证明": ["服务能力", "检测报告", "服务团队", "服务设施", "服务案例"],
    "投标响应文件": ["投标响应文件", "应答文件", "商务投标文件", "技术投标文件"],
    "偏差表": ["偏差表", "商务偏差", "技术偏差"],
    "响应承诺": ["响应承诺", "承诺函", "响应声明"],
    "项目负责人简历": ["项目负责人简历", "负责人简历", "项目经理简历"],
    "团队人员名单": ["团队人员名单", "项目团队", "人员名单"],
    "资格证书": ["资格证书", "职称证书", "职业资格", "高级职称", "高级技师"],
    "社保证明": ["社保证明", "社保缴纳", "社会保险"],
    "项目合同": ["项目合同", "合同", "业绩合同"],
    "中标通知书": ["中标通知书", "成交通知书"],
    "验收证明": ["验收证明", "验收报告", "用户证明"],
    "服务方案": ["服务方案", "技术方案", "实施方案"],
    "进度计划": ["进度计划", "工作进度", "计划安排"],
    "保障措施": ["保障措施", "保证措施", "质量保障", "风险控制"],
    "项目基础材料": ["项目基础材料", "产品资料", "工作基础", "专题研究", "产品化"],
    "专利证书": ["专利证书", "发明专利", "实用新型专利", "专利"],
    "知识产权证明": ["知识产权", "专利", "软件著作权", "软著"],
    "绩效评价证明": ["绩效评价证明", "绩效评价", "供应商绩效"],
    "历史评价结果": ["历史评价结果", "评价结果", "年度评价"],
    "报价明细表": ["报价明细表", "分项报价", "报价清单", "价格明细"],
    "开标一览表": ["开标一览表", "投标一览表", "报价一览表"],
    "报价一致性核对表": ["报价一致性核对表", "价格核对表", "报价核对", "大小写金额"],
}


def match_materials_for_project(project: dict[str, Any], limit: int | None = None) -> dict[str, Any]:
    """Match each generation task to candidate knowledge-base materials.

    This layer is intentionally retrieval-only: it does not call an LLM or any
    agent. A failed search for one task is captured on that task and does not
    stop the rest of the project from being processed.
    """

    tasks = list(project.get("generation_tasks") or [])
    top_k = limit if isinstance(limit, int) and limit > 0 else DEFAULT_SEARCH_LIMIT
    results: list[dict[str, Any]] = []

    try:
        vector_store: VectorStore | None = VectorStore()
    except Exception as exc:
        vector_store = None
        init_error = str(exc)
    else:
        init_error = ""

    for task in tasks:
        result = _empty_task_result(task)
        result["query"] = build_task_query(task)

        if not result["query"]:
            result["status"] = "skipped"
            result["trace"].append("query_empty")
            results.append(result)
            continue

        if vector_store is None:
            result["status"] = "error"
            result["trace"].append(f"vector_store_unavailable: {init_error}")
            results.append(result)
            continue

        try:
            rows = vector_store.search(result["query"], top_k=top_k)
            result["matched_materials"] = [
                enrich_material_match(task, normalize_material(row, rank=index + 1))
                for index, row in enumerate(rows)
            ]
            usable_matches = [
                material
                for material in result["matched_materials"]
                if material.get("usability") == "usable"
            ]
            supporting_matches = [
                material
                for material in result["matched_materials"]
                if material.get("usability") == "supporting"
            ]
            result["status"] = "matched" if usable_matches else "no_match"
            result["trace"].append(f"vector_search_top_k={top_k}")
            result["trace"].append(f"matches={len(result['matched_materials'])}")
            result["trace"].append(f"usable_matches={len(usable_matches)}")
            result["trace"].append(f"supporting_matches={len(supporting_matches)}")
        except Exception as exc:
            result["status"] = "error"
            result["trace"].append(f"search_error: {exc}")

        results.append(result)

    matched_count = sum(1 for item in results if item.get("status") == "matched")
    status_counts = count_by_status(results)
    return {
        "status": "success",
        "total_tasks": len(tasks),
        "matched_count": matched_count,
        "results": results,
        "summary": {
            "search_limit_per_task": top_k,
            "status_counts": status_counts,
        },
    }


def build_task_query(task: dict[str, Any]) -> str:
    parts: list[str] = []
    append_text(parts, task.get("target_section"))
    append_text(parts, task.get("task_type"))
    append_list(parts, task.get("related_requirements"))
    append_list(parts, task.get("evidence_needed"))
    append_list(parts, task.get("kb_mapping_hint"))
    append_text(parts, task.get("generation_instruction"))
    return dedupe_join(parts)


def normalize_material(row: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "file_id": row.get("file_id"),
        "split_item_id": row.get("split_item_id"),
        "original_name": row.get("original_name"),
        "category": row.get("category"),
        "tags": row.get("tags", ""),
        "matched_text": row.get("matched_text", ""),
        "distance": row.get("distance"),
    }


def enrich_material_match(task: dict[str, Any], material: dict[str, Any]) -> dict[str, Any]:
    evaluation = evaluate_material_relevance(task, material)
    usability = evaluate_material_usability(
        task,
        material,
        evaluation["matched_evidence"],
        evaluation["relevance"],
    )
    return {
        **material,
        "matched_evidence": evaluation["matched_evidence"],
        "relevance": evaluation["relevance"],
        "match_reason": evaluation["match_reason"],
        "score": evaluation["score"],
        "usability": usability["usability"],
        "usability_reason": usability["usability_reason"],
    }


def evaluate_material_relevance(task: dict[str, Any], material: dict[str, Any]) -> dict[str, Any]:
    haystack = build_material_haystack(material)
    evidence_needed = [str(item).strip() for item in list(task.get("evidence_needed") or []) if str(item).strip()]
    matched_evidence = [
        evidence for evidence in evidence_needed if evidence_matches_material(evidence, haystack)
    ]
    context_hits = task_context_hits(task, haystack, matched_evidence)
    score = calculate_relevance_score(matched_evidence, context_hits, material)
    relevance = classify_relevance(score, matched_evidence, context_hits)
    match_reason = build_match_reason(matched_evidence, context_hits, relevance)
    return {
        "matched_evidence": matched_evidence,
        "relevance": relevance,
        "match_reason": match_reason,
        "score": score,
    }


def evaluate_material_usability(
    task: dict[str, Any],
    material: dict[str, Any],
    matched_evidence: list[str],
    relevance: str,
) -> dict[str, str]:
    profile = classify_task_profile(task)
    haystack = build_material_haystack(material)

    if profile == "patent_count":
        if material_is_patent_certificate(haystack):
            return usable_result("Patent-count scoring requires patent certificates.")
        if material_is_software_copyright(haystack) or material_is_test_report(haystack):
            return supporting_result("Can support innovation context, but patent-count scoring needs patent certificates.")
        return supporting_result("Not a patent certificate, so it cannot directly support patent-count scoring.")

    if profile == "performance":
        if material_is_contract(haystack) or material_is_bid_notice(haystack) or material_is_acceptance(haystack):
            return usable_result("Performance scoring accepts contracts, award notices, and acceptance proofs.")
        return supporting_result("Not a direct performance proof such as contract, award notice, or acceptance proof.")

    if profile in {"project_leader", "project_team"}:
        if (
            material_is_resume(haystack)
            or material_is_personnel_list(haystack)
            or material_is_personnel_certificate(haystack)
            or material_is_social_security(haystack)
        ):
            return usable_result("Project-team scoring requires resumes, staff lists, certificates, or social-security proofs.")
        return supporting_result("General qualifications, patents, or contracts do not directly prove project-team staffing.")

    if profile == "quote":
        if material_is_quote_document(haystack):
            return usable_result("Quote scoring requires quote schedule, detailed quote, or consistency check materials.")
        return supporting_result("Not a quote schedule, detailed quote, or quote consistency check material.")

    if profile == "green_management_system":
        if material_is_green_management_system(haystack):
            return usable_result("Green-management scoring accepts energy, quality, occupational health, or environmental management certification.")
        if material_is_information_security_system(haystack):
            return supporting_result("Information-security certification can be background support, but does not cover green-management certification.")
        return supporting_result("Not one of the required green-management system certifications.")

    if profile == "comprehensive_strength":
        if (
            material_is_business_license(haystack)
            or material_is_management_system(haystack)
            or material_is_high_tech_certificate(haystack)
            or material_is_quality_award(haystack)
            or material_is_personnel_certificate(haystack)
            or material_is_personnel_list(haystack)
        ):
            return usable_result("Comprehensive-strength scoring can use enterprise qualification, certification, award, or personnel proof.")
        return supporting_result("Material may be useful background, but is not direct comprehensive-strength evidence.")

    if matched_evidence and relevance in {"high", "medium"}:
        return usable_result("Material explicitly matches required evidence and no stricter task-specific rule applies.")
    return supporting_result("Material is retained as context only; it does not directly cover required evidence.")


def usable_result(reason: str) -> dict[str, str]:
    return {"usability": "usable", "usability_reason": reason}


def supporting_result(reason: str) -> dict[str, str]:
    return {"usability": "supporting", "usability_reason": reason}


def classify_task_profile(task: dict[str, Any]) -> str:
    task_type = str(task.get("task_type") or "").lower()
    identity = build_task_identity_text(task)
    if task_type == "price" or contains_keyword(identity, ["报价质量", "价格评分", "报价文件", "开标一览表", "报价明细表", "报价一致性核对表"]):
        return "quote"
    if contains_keyword(identity, ["专利数量"]):
        return "patent_count"
    if contains_keyword(identity, ["业绩情况"]):
        return "performance"
    if contains_keyword(identity, ["项目负责人情况", "项目负责人"]):
        return "project_leader"
    if contains_keyword(identity, ["项目团队人员结构", "项目团队", "人员结构"]):
        return "project_team"
    if contains_keyword(identity, ["绿色管理体系认证"]):
        return "green_management_system"
    if contains_keyword(identity, ["综合实力"]):
        return "comprehensive_strength"
    return "default"


def build_task_identity_text(task: dict[str, Any]) -> str:
    parts: list[str] = []
    append_text(parts, task.get("task_id"))
    append_text(parts, task.get("task_type"))
    append_text(parts, task.get("target_section"))
    append_text(parts, task.get("generation_instruction"))
    append_list(parts, task.get("related_requirements"))
    append_list(parts, task.get("evidence_needed"))
    return "\n".join(parts)


def material_is_patent_certificate(text: str) -> bool:
    if material_is_software_copyright(text):
        return False
    return contains_keyword(text, ["专利证书", "发明专利", "实用新型专利"]) or (
        contains_keyword(text, ["专利"]) and not contains_keyword(text, ["软件著作权", "软著"])
    )


def material_is_software_copyright(text: str) -> bool:
    return contains_keyword(text, ["软件著作权", "软著"])


def material_is_test_report(text: str) -> bool:
    return contains_keyword(text, ["检测报告", "检验报告", "测试报告"])


def material_is_business_license(text: str) -> bool:
    return contains_keyword(text, ["营业执照"])


def material_is_aaa_credit(text: str) -> bool:
    return contains_keyword(text, ["AAA信用", "信用等级证书", "信用等级"])


def material_is_contract(text: str) -> bool:
    return contains_keyword(text, ["项目合同", "业绩合同", "合同"])


def material_is_bid_notice(text: str) -> bool:
    return contains_keyword(text, ["中标通知书", "成交通知书"])


def material_is_acceptance(text: str) -> bool:
    return contains_keyword(text, ["验收证明", "验收报告", "用户证明"])


def material_is_resume(text: str) -> bool:
    return contains_keyword(text, ["项目负责人简历", "负责人简历", "项目经理简历", "简历"])


def material_is_personnel_list(text: str) -> bool:
    return contains_keyword(text, ["团队人员名单", "项目团队", "人员名单", "研发团队名单"])


def material_is_personnel_certificate(text: str) -> bool:
    if material_is_management_system(text):
        return False
    return contains_keyword(text, ["职称证书", "资格证书", "职业资格", "高级职称", "高级技师"])


def material_is_social_security(text: str) -> bool:
    return contains_keyword(text, ["社保证明", "社保缴纳", "社会保险"])


def material_is_quote_document(text: str) -> bool:
    return contains_keyword(text, ["开标一览表", "报价明细表", "报价一致性核对表", "分项报价", "价格核对表", "报价核对"])


def material_is_information_security_system(text: str) -> bool:
    return contains_keyword(text, ["ISO27001", "信息安全管理体系"])


def material_is_green_management_system(text: str) -> bool:
    if material_is_information_security_system(text):
        return False
    return contains_keyword(
        text,
        [
            "能源管理体系",
            "质量管理体系",
            "职业健康安全管理体系",
            "环境管理体系",
            "ISO9001",
            "ISO14001",
            "ISO45001",
            "ISO50001",
        ],
    )


def material_is_management_system(text: str) -> bool:
    return contains_keyword(
        text,
        [
            "管理体系认证",
            "质量管理体系",
            "环境管理体系",
            "职业健康安全管理体系",
            "能源管理体系",
            "信息安全管理体系",
            "ISO9001",
            "ISO14001",
            "ISO45001",
            "ISO50001",
            "ISO27001",
        ],
    )


def material_is_high_tech_certificate(text: str) -> bool:
    return contains_keyword(text, ["高新技术企业", "高新技术企业证书"])


def material_is_quality_award(text: str) -> bool:
    return contains_keyword(text, ["中国质量奖", "全国质量奖", "中国工业大奖", "质量奖"])


def contains_keyword(text: str, keywords: list[str]) -> bool:
    normalized_text = normalize_for_match(text)
    return any(normalize_for_match(keyword) in normalized_text for keyword in keywords)


def build_material_haystack(material: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("original_name", "category", "tags", "matched_text"):
        value = material.get(key)
        if value:
            parts.append(str(value))
    return "\n".join(parts)


def evidence_matches_material(evidence: str, haystack: str) -> bool:
    normalized_haystack = normalize_for_match(haystack)
    if not normalized_haystack:
        return False

    terms = evidence_terms(evidence)
    if not terms:
        return False

    for term in terms:
        normalized_term = normalize_for_match(term)
        if normalized_term and normalized_term in normalized_haystack:
            return True

    evidence_tokens = significant_tokens(evidence)
    if not evidence_tokens:
        return False

    matched_tokens = [
        token for token in evidence_tokens if normalize_for_match(token) in normalized_haystack
    ]
    required = 1 if len(evidence_tokens) <= 2 else max(2, len(evidence_tokens) // 2)
    return len(matched_tokens) >= required


def evidence_terms(evidence: str) -> list[str]:
    terms = [evidence]
    terms.extend(EVIDENCE_ALIASES.get(evidence, []))
    for key, aliases in EVIDENCE_ALIASES.items():
        if key in evidence or evidence in key:
            terms.extend(aliases)
    return dedupe_terms(terms)


def task_context_hits(
    task: dict[str, Any],
    haystack: str,
    matched_evidence: list[str],
) -> list[str]:
    normalized_haystack = normalize_for_match(haystack)
    if not normalized_haystack:
        return []

    context_terms: list[str] = []
    append_text(context_terms, task.get("target_section"))
    append_text(context_terms, task.get("task_type"))
    append_list(context_terms, task.get("kb_mapping_hint"))
    for evidence in matched_evidence:
        context_terms.extend(evidence_terms(evidence))
    for value in list(task.get("related_requirements") or []):
        context_terms.extend(domain_terms(value))
    context_terms.extend(domain_terms(task.get("generation_instruction")))

    hits: list[str] = []
    for term in dedupe_terms(context_terms):
        normalized_term = normalize_for_match(term)
        if len(normalized_term) >= 2 and normalized_term in normalized_haystack:
            hits.append(term)
    return hits


def domain_terms(value: Any) -> list[str]:
    text = str(value or "")
    terms: list[str] = []
    for key, aliases in EVIDENCE_ALIASES.items():
        if key in text or any(alias in text for alias in aliases):
            terms.append(key)
            terms.extend(aliases)
    for token in significant_tokens(text):
        if token not in STOPWORDS and len(normalize_for_match(token)) >= 3:
            terms.append(token)
    return terms


def calculate_relevance_score(
    matched_evidence: list[str],
    context_hits: list[str],
    material: dict[str, Any],
) -> int:
    score = min(70, len(matched_evidence) * 35)
    score += min(20, len(context_hits) * 5)

    distance = material.get("distance")
    if isinstance(distance, (int, float)):
        if distance <= 0.35:
            score += 10
        elif distance <= 0.75:
            score += 5

    if not matched_evidence and context_hits:
        score = min(score, LOW_SCORE_THRESHOLD)
    return max(0, min(100, score))


def classify_relevance(score: int, matched_evidence: list[str], context_hits: list[str]) -> str:
    if matched_evidence and score >= HIGH_SCORE_THRESHOLD:
        return "high"
    if matched_evidence and score >= MEDIUM_SCORE_THRESHOLD:
        return "medium"
    if matched_evidence or context_hits or score >= LOW_SCORE_THRESHOLD:
        return "low"
    return "low"


def build_match_reason(
    matched_evidence: list[str],
    context_hits: list[str],
    relevance: str,
) -> str:
    if matched_evidence:
        reason = "Matched required evidence: " + ", ".join(matched_evidence)
        if context_hits:
            reason += "; context keywords: " + ", ".join(context_hits[:5])
        return reason
    if context_hits:
        return "Only broad context keywords matched: " + ", ".join(context_hits[:5])
    return f"No explicit evidence keyword matched; relevance={relevance}."


def significant_tokens(text: Any) -> list[str]:
    raw = str(text or "")
    tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", raw)
    cleaned: list[str] = []
    for token in tokens:
        normalized = normalize_for_match(token)
        if len(normalized) >= 2 and token not in STOPWORDS:
            cleaned.append(token)
    return cleaned


def normalize_for_match(text: Any) -> str:
    return re.sub(r"\s+|[^\w\u4e00-\u9fff]+", "", str(text or "").lower())


def dedupe_terms(parts: list[Any]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        normalized = normalize_for_match(text)
        if normalized and normalized not in seen:
            cleaned.append(text)
            seen.add(normalized)
    return cleaned


def _empty_task_result(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": str(task.get("task_id") or ""),
        "task_type": str(task.get("task_type") or ""),
        "target_section": str(task.get("target_section") or ""),
        "query": "",
        "evidence_needed": list(task.get("evidence_needed") or []),
        "kb_mapping_hint": list(task.get("kb_mapping_hint") or []),
        "matched_materials": [],
        "status": "skipped",
        "trace": [],
    }


def append_text(parts: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if text:
        parts.append(text)


def append_list(parts: list[str], value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            append_text(parts, item)
    else:
        append_text(parts, value)


def dedupe_join(parts: list[str]) -> str:
    seen: set[str] = set()
    cleaned: list[str] = []
    for part in parts:
        normalized = " ".join(str(part).split())
        if normalized and normalized not in seen:
            cleaned.append(normalized)
            seen.add(normalized)
    return "\n".join(cleaned)


def count_by_status(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts
