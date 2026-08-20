from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import DATA_DIR


DEPARTMENT_PROFILES_PATH = DATA_DIR / "department_profiles.json"
CAPABILITY_DOMAINS_PATH = DATA_DIR / "capability_domains.json"

STANDARD_DEPARTMENTS = [
    "云服务部",
    "安全事业部",
    "数字化办公事业部",
    "数据管理事业部",
    "服务运营管理部",
    "硬件维保事业部",
    "网络事业部",
]

PROFILE_MATCH_FIELDS = [
    ("suitable_project_types", 50),
    ("business_scope", 30),
    ("products_services", 20),
    ("keywords", 5),
    ("description", 1),
]

FALLBACK_PROJECT_KEYWORDS = {
    "数据管理事业部": ["区块链", "工业数据", "数据管理", "数据治理", "数据监管", "数据存证", "隐私计算", "联邦学习", "智能合约"],
    "安全事业部": ["数据安全", "隐私保护", "访问控制", "安全分析", "监管", "安全性证明", "安全评估", "漏洞"],
    "云服务部": ["云平台", "虚拟化", "云服务", "Azure", "阿里云", "VMware", "Citrix", "AWS"],
    "数字化办公事业部": ["M365", "Microsoft 365", "PowerPlatform", "PowerApps", "PowerAutomate", "OA", "流程自动化", "SharePoint", "Teams"],
    "服务运营管理部": ["运维", "SLA", "服务台", "ITIL", "服务流程", "项目运营"],
    "硬件维保事业部": ["硬件", "服务器", "存储", "备件", "巡检", "设备维护"],
    "网络事业部": ["网络", "交换机", "路由器", "防火墙", "网络集成", "无线网络"],
}

WEAK_INFRA_DEPARTMENTS = {"云服务部", "网络事业部", "硬件维保事业部", "服务运营管理部"}
EXPLICIT_SECURITY_TERMS = ["数据安全", "隐私保护", "访问控制", "安全分析"]
WEAK_INFRA_TERMS = ["基础架构", "Infra", "服务器", "存储", "网络", "硬件", "运维", "驻场"]


def load_department_profiles(path: Path = DEPARTMENT_PROFILES_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"department profiles not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8") or '{"departments": []}')
    profiles = data.get("departments") if isinstance(data, dict) else data
    if not isinstance(profiles, list):
        raise ValueError("department_profiles.json must contain a departments list")
    validate_department_profiles(profiles)
    return [normalize_department_profile(item) for item in profiles]


def load_capability_domains(path: Path = CAPABILITY_DOMAINS_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8") or '{"capability_domains": []}')
    domains = data.get("capability_domains") if isinstance(data, dict) else data
    if not isinstance(domains, list):
        raise ValueError("capability_domains.json must contain a capability_domains list")
    result: list[dict[str, Any]] = []
    for item in domains:
        domain = str(item.get("domain") or "").strip()
        preferred_departments = [str(value) for value in list(item.get("preferred_departments") or []) if str(value).strip()]
        invalid = [value for value in preferred_departments if value not in STANDARD_DEPARTMENTS]
        if invalid:
            raise ValueError(f"capability domain {domain} has non-standard departments: {invalid}")
        result.append(
            {
                "domain": domain,
                "keywords": [str(value) for value in list(item.get("keywords") or []) if str(value).strip()],
                "preferred_departments": preferred_departments,
                "routing_note": str(item.get("routing_note") or ""),
            }
        )
    return result


def validate_department_profiles(profiles: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for item in profiles:
        department = str(item.get("department") or "")
        if department not in STANDARD_DEPARTMENTS:
            raise ValueError(f"non-standard department: {department}")
        if department in seen:
            raise ValueError(f"duplicate department profile: {department}")
        seen.add(department)


def normalize_department_profile(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "department": item.get("department"),
        "description": str(item.get("description") or ""),
        "business_scope": list(item.get("business_scope") or []),
        "products_services": list(item.get("products_services") or []),
        "suitable_project_types": list(item.get("suitable_project_types") or []),
        "keywords": list(item.get("keywords") or []),
        "typical_roles": list(item.get("typical_roles") or []),
        "certificates": list(item.get("certificates") or []),
        "not_suitable_for": list(item.get("not_suitable_for") or []),
        "notes": list(item.get("notes") or []),
    }


def inspect_department_profile(department: str, path: Path = DEPARTMENT_PROFILES_PATH) -> dict[str, Any]:
    for item in load_department_profiles(path):
        if item.get("department") == department:
            return item
    raise ValueError(f"department profile not found: {department}")


def build_project_requirement_profile(
    tender_text: str = "",
    score_items: list[dict[str, Any]] | None = None,
    technical_spec_text: str = "",
) -> dict[str, Any]:
    score_text = json.dumps(score_items or [], ensure_ascii=False)
    combined_text = "\n".join([tender_text or "", technical_spec_text or "", score_text])
    keywords = extract_known_project_keywords(combined_text)
    return {
        "text": combined_text,
        "keywords": keywords,
        "source_lengths": {
            "tender_text": len(tender_text or ""),
            "technical_spec_text": len(technical_spec_text or ""),
            "score_items": len(score_items or []),
        },
    }


def match_project_to_departments(
    project_requirement_profile: dict[str, Any],
    department_profiles: list[dict[str, Any]] | None = None,
    capability_domains: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profiles = department_profiles if department_profiles is not None else load_department_profiles()
    domains = capability_domains if capability_domains is not None else load_capability_domains()
    validate_department_profiles(profiles)
    text = normalize_text(
        " ".join(
            [
                str(project_requirement_profile.get("text") or ""),
                " ".join(str(item) for item in list(project_requirement_profile.get("keywords") or [])),
                json.dumps(project_requirement_profile, ensure_ascii=False),
            ]
        )
    )

    selected_by_department: dict[str, dict[str, Any]] = {}
    profile_matches = [item for item in [score_department_profile(profile, text) for profile in profiles] if item["score"] > 0]
    for item in profile_matches:
        selected_by_department[str(item["department"])] = dict(item)

    capability_matches = match_capability_domains(text, domains)
    for match in capability_matches:
        add_capability_match(selected_by_department, match)

    if selected_by_department:
        source = "department_profile" if profile_matches else "capability_domain"
        selected = sorted(selected_by_department.values(), key=lambda item: (-item["score"], STANDARD_DEPARTMENTS.index(item["department"])))
        requires_manual_confirm = source != "department_profile"
    else:
        source = "fallback_keywords"
        selected = sorted(score_fallback_departments(text), key=lambda item: (-item["score"], STANDARD_DEPARTMENTS.index(item["department"])))
        requires_manual_confirm = True

    original_selected = list(selected)
    weak_candidate_departments = weak_infra_candidate_departments(selected, text)
    selected_for_strong = [item for item in selected if item["department"] not in weak_candidate_departments]
    primary: list[str] = []
    secondary: list[str] = []
    if selected_for_strong:
        best = selected_for_strong[0]["score"]
        data_score = max([item["score"] for item in selected_for_strong if item["department"] == "数据管理事业部"] or [0])
        if source == "department_profile" and data_score == best and data_score > 0:
            primary = ["数据管理事业部"]
        else:
            primary = [item["department"] for item in selected_for_strong if item["score"] == best]
        secondary = [
            item["department"]
            for item in selected_for_strong
            if item["department"] not in primary and item["score"] > 0 and keep_secondary_candidate(item, best)
        ]
        if primary == ["数据管理事业部"]:
            secondary = ["安全事业部"] if has_explicit_security_hit(text) and any(item["department"] == "安全事业部" for item in selected_for_strong) else []
        secondary = secondary[:2]
    broad_reason = ""
    if weak_candidate_departments:
        broad_reason = "部门路由过宽，存在泛化能力域命中。"
        requires_manual_confirm = True

    return {
        "primary_departments": primary,
        "secondary_departments": secondary,
        "weak_candidate_departments": sorted(weak_candidate_departments, key=STANDARD_DEPARTMENTS.index),
        "weak_candidate_count": len(weak_candidate_departments),
        "candidate_departments": original_selected,
        "match_keywords": unique_terms([keyword for item in selected for keyword in list(item.get("matched_terms") or [])]),
        "reason": [format_match_reason(item) for item in selected if item.get("score", 0) > 0],
        "match_source": source,
        "used_profile_fields": unique_terms([field for item in selected for field in list(item.get("used_profile_fields") or [])]),
        "capability_domain_matches": capability_matches,
        "routing_notes": unique_terms([note for item in selected for note in list(item.get("routing_notes") or [])]),
        "routing_summary_reason": broad_reason or "按项目关键词和部门画像匹配。",
        "requires_manual_confirm": requires_manual_confirm,
    }


def add_capability_match(selected_by_department: dict[str, dict[str, Any]], match: dict[str, Any]) -> None:
    domain = str(match.get("domain") or "")
    terms = list(match.get("matched_terms") or [])
    evidence = [
        {
            "matched_field": "capability_domain",
            "matched_term": domain,
            "score_weight": 2,
            "match_source": "capability_domain",
        }
    ]
    evidence.extend(
        {
            "matched_field": "capability_domain.keywords",
            "matched_term": term,
            "score_weight": 1,
            "match_source": "capability_domain",
        }
        for term in terms
    )
    for department in list(match.get("preferred_departments") or []):
        if department not in STANDARD_DEPARTMENTS:
            continue
        if department in selected_by_department:
            row = selected_by_department[department]
            row["score"] = row.get("score", 0) + 2
            row["matched_terms"] = unique_terms(list(row.get("matched_terms") or []) + [domain] + terms)
            row["used_profile_fields"] = unique_terms(list(row.get("used_profile_fields") or []) + ["capability_domain"])
            row["matched_evidence"] = list(row.get("matched_evidence") or []) + evidence
            row["routing_notes"] = unique_terms(list(row.get("routing_notes") or []) + [str(match.get("routing_note") or "")])
            if row.get("match_source") == "department_profile":
                row["match_source"] = "department_profile+capability_domain"
        else:
            selected_by_department[department] = {
                "department": department,
                "score": max(1, len(terms)) + 1,
                "matched_terms": unique_terms([domain] + terms),
                "used_profile_fields": ["capability_domain"],
                "matched_evidence": evidence,
                "match_source": "capability_domain",
                "routing_notes": unique_terms([str(match.get("routing_note") or "")]),
            }


def match_capability_domains(text: str, domains: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for item in domains:
        terms = [str(value) for value in list(item.get("keywords") or []) if str(value).strip()]
        hits = [term for term in terms if contains_term(text, term)]
        if hits and has_strong_capability_hit(str(item.get("domain") or ""), hits):
            matches.append(
                {
                    "domain": item.get("domain"),
                    "matched_terms": unique_terms(hits),
                    "preferred_departments": list(item.get("preferred_departments") or []),
                    "routing_note": item.get("routing_note") or "",
                }
            )
    return matches


def keep_secondary_candidate(item: dict[str, Any], best_score: int | float) -> bool:
    score = float(item.get("score") or 0)
    if "capability_domain" in list(item.get("used_profile_fields") or []) and score >= 2:
        return True
    return score >= 10


def weak_infra_candidate_departments(selected: list[dict[str, Any]], text: str = "") -> set[str]:
    weak: set[str] = set()
    for item in selected:
        department = str(item.get("department") or "")
        matched = set(str(value) for value in list(item.get("matched_terms") or []))
        if department == "安全事业部" and not has_explicit_security_hit(text) and "capability_domain" in list(item.get("used_profile_fields") or []) and ("基础架构/Infra" in matched or bool(matched & set(WEAK_INFRA_TERMS))):
            weak.add(department)
            continue
        if department not in WEAK_INFRA_DEPARTMENTS:
            continue
        if "capability_domain" in list(item.get("used_profile_fields") or []) and ("基础架构/Infra" in matched or bool(matched & set(WEAK_INFRA_TERMS))):
            weak.add(department)
            continue
        if matched and matched.issubset(set(WEAK_INFRA_TERMS)):
            weak.add(department)
    return weak


def has_explicit_security_hit(text: str) -> bool:
    return any(contains_term(text, term) for term in EXPLICIT_SECURITY_TERMS)


def has_strong_capability_hit(domain: str, hits: list[str]) -> bool:
    ambiguous_terms = {
        "AI与大模型": {"AI"},
        "基础架构/Infra": {"存储", "网络"},
    }
    ambiguous = ambiguous_terms.get(domain, set())
    return any(term not in ambiguous for term in hits)


def score_department_profile(profile: dict[str, Any], text: str) -> dict[str, Any]:
    score = 0
    matched_terms: list[str] = []
    used_fields: list[str] = []
    matched_evidence: list[dict[str, Any]] = []
    for field, weight in PROFILE_MATCH_FIELDS:
        values = profile_values(profile, field)
        hits = [value for value in values if contains_term(text, value)]
        if hits:
            score += len(hits) * weight
            matched_terms.extend(hits)
            used_fields.append(field)
            matched_evidence.extend(
                {
                    "matched_field": field,
                    "matched_term": value,
                    "score_weight": weight,
                    "match_source": "department_profile",
                }
                for value in hits
            )

    not_suitable_hits = [value for value in list(profile.get("not_suitable_for") or []) if contains_term(text, value)]
    if not_suitable_hits:
        score -= len(not_suitable_hits) * 6
        matched_terms.extend(not_suitable_hits)
        used_fields.append("not_suitable_for")
        matched_evidence.extend(
            {
                "matched_field": "not_suitable_for",
                "matched_term": value,
                "score_weight": -6,
                "match_source": "department_profile",
            }
            for value in not_suitable_hits
        )

    return {
        "department": profile.get("department"),
        "score": max(score, 0),
        "matched_terms": unique_terms(matched_terms),
        "used_profile_fields": unique_terms(used_fields),
        "matched_evidence": matched_evidence,
        "match_source": "department_profile",
        "routing_notes": [],
    }


def score_fallback_departments(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for department in STANDARD_DEPARTMENTS:
        terms = FALLBACK_PROJECT_KEYWORDS.get(department, [])
        hits = [term for term in terms if contains_term(text, term)]
        rows.append(
            {
                "department": department,
                "score": len(hits),
                "matched_terms": hits,
                "used_profile_fields": ["fallback_keywords"] if hits else [],
                "matched_evidence": [
                    {
                        "matched_field": "fallback_keywords",
                        "matched_term": term,
                        "score_weight": 1,
                        "match_source": "fallback_keywords",
                    }
                    for term in hits
                ],
                "match_source": "fallback_keywords",
                "routing_notes": [],
            }
        )
    return [item for item in rows if item["score"] > 0]


def profile_values(profile: dict[str, Any], field: str) -> list[str]:
    value = profile.get(field)
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def format_match_reason(item: dict[str, Any]) -> str:
    fields = "、".join(item.get("used_profile_fields") or [])
    terms = "、".join(item.get("matched_terms") or [])
    return f"{item.get('department')}：{item.get('match_source')} 命中 {terms or '-'}；字段 {fields or '-'}"


def extract_known_project_keywords(text: str) -> list[str]:
    normalized = normalize_text(text)
    terms: list[str] = []
    for values in FALLBACK_PROJECT_KEYWORDS.values():
        for term in values:
            if contains_term(normalized, term):
                terms.append(term)
    return unique_terms(terms)


def normalize_text(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def contains_term(normalized_text: str, term: str) -> bool:
    normalized_term = normalize_text(term)
    if not normalized_term:
        return False
    if normalized_term.isascii() and " " not in normalized_term:
        tokens = (
            normalized_text.replace("/", " ")
            .replace("-", " ")
            .replace("_", " ")
            .replace(".", " ")
            .replace(",", " ")
            .split()
        )
        return normalized_term in tokens
    if normalized_term.endswith("项目"):
        core = normalized_term[:-2].strip()
        if core and core in normalized_text:
            return True
    return normalized_term in normalized_text


def unique_terms(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
