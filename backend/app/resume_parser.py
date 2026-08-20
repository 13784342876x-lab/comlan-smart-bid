import re
from pathlib import PurePosixPath
from typing import Any

from app.certificate_family import match_certificate_family
from app.certificate_canonical import merge_canonical_certificates
from app.certificate_ownership import batch_resolve_certificates
from app.certificate_standardizer import build_capability_profile, standardize_certificate as standardize_certificate_family


SECTION_TITLES = [
    "工程师简介",
    "基本信息",
    "个人信息",
    "工作经历",
    "专业技能",
    "项目经验",
    "项目经历",
    "相关证书",
    "证书",
    "教育经历",
]

TIME_PATTERN = re.compile(
    r"(?P<time>(?:20\d{2}|19\d{2})(?:[./年-]\s*\d{1,2}\s*月?)?"
    r"\s*(?:[-—~至到]\s*(?:20\d{2}|19\d{2}|至今|今|现在)"
    r"(?:[./年-]\s*\d{1,2}\s*月?)?)?)"
)

KNOWN_TECHS = [
    "Python",
    "PowerPlatform",
    "Power Platform",
    "Power Apps",
    "Power Automate",
    "M365",
    "Microsoft 365",
    "Office 365",
    "VMware",
    "vSphere",
    "vCenter",
    "Citrix",
    "Nutanix",
    "NUTANIX",
    "阿里云",
    "Azure",
    "Windows Server",
    "Linux",
    "SQL Server",
    "MySQL",
    "Docker",
    "Kubernetes",
]

CERT_KEYWORDS = [
    "VCP",
    "NCP",
    "ACP",
    "HCIA",
    "HCIP",
    "HCIE",
    "PMP",
    "ITIL",
    "CCNA",
    "CCNP",
    "CISP",
    "CISSP",
    "RHCE",
    "RHCSA",
    "AZ-104",
    "AZ-140",
    "NCSC",
    "SCTP",
    "Certified",
    "Certificate",
    "Azure",
    "Data Center Virtualization",
    "Alibaba Cloud Certified Professional",
    "Nutanix Certified Professional",
    "VMware Certified Professional",
    "认证",
    "证书",
]

VENDOR_PATTERNS = [
    ("VMware", re.compile(r"vmware|vcp", re.I)),
    ("Nutanix", re.compile(r"nutanix|ncp", re.I)),
    ("阿里云", re.compile(r"阿里云|alibaba|aliyun|acp", re.I)),
    ("Citrix", re.compile(r"citrix", re.I)),
    ("华为", re.compile(r"华为|huawei|hcia|hcip|hcie", re.I)),
    ("Microsoft", re.compile(r"microsoft|微软|azure|m365|office\s*365", re.I)),
    ("Cisco", re.compile(r"cisco|ccna|ccnp", re.I)),
]


def parse_resume(
    parsed_text: str,
    original_name: str = "",
    source_path: str | None = None,
    source_folder: str | None = None,
    department: str | None = None,
    assets: list[dict[str, Any]] | None = None,
    file_id: str | None = None,
) -> dict[str, Any]:
    """Parse one resume into structured fields without guessing department from body text."""
    text = normalize_text(parsed_text or "")
    source_info = parse_source_department(source_path=source_path, source_folder=source_folder)
    resolved_department = clean_value(department) or source_info.get("department")
    raw_sections = split_sections(text)
    table_blocks = parse_table_blocks(text)
    table_profile_text = build_profile_text_from_tables(table_blocks)
    table_work_history = extract_work_history_from_tables(table_blocks)
    table_skills_text = build_skills_text_from_tables(table_blocks)
    table_projects = extract_projects_from_tables(table_blocks)
    table_certificates = extract_certificates_from_tables(table_blocks)
    overview_text = "\n".join(
        section
        for key, section in raw_sections.items()
        if key in {"工程师简介", "基本信息", "个人信息", "_preamble"}
    )
    overview_text = "\n".join(part for part in [overview_text, table_profile_text] if part.strip())
    if not overview_text.strip():
        overview_text = text[:1200]

    certificate_section_text = raw_sections.get("相关证书") or raw_sections.get("证书") or ""
    if "=== Table" in certificate_section_text:
        certificate_section_text = certificate_section_text.split("=== Table", 1)[0]
    certificates, certificate_warnings = extract_certificates_from_text(certificate_section_text)
    certificates.extend(table_certificates)
    embedded_certificates, embedded_candidates = extract_embedded_certificate_candidates(assets or [])
    certificates.extend(embedded_certificates)

    raw_project_text = raw_sections.get("项目经验") or raw_sections.get("项目经历") or ""
    raw_projects = extract_projects(raw_project_text)
    if table_projects and raw_project_text and not re.search(r"项目名称|项目日期|项目时间|项目描述|工作内容描述", raw_project_text):
        raw_projects = []

    person_name = extract_labeled_value(overview_text, ["姓名"])
    structured_certificates = bind_certificate_owners(
        dedupe_certificates(certificates),
        person_name=person_name,
        file_id=file_id,
        original_name=original_name,
        source_path=source_path,
    )
    canonical_certificates = merge_canonical_certificates(structured_certificates)

    structured = {
        "person_name": person_name,
        "gender": normalize_gender(extract_labeled_value(overview_text, ["性别"])),
        "work_years": extract_work_years(overview_text),
        "department": resolved_department,
        "source_group": source_info.get("source_group"),
        "source_folder": source_info.get("source_folder"),
        "work_history": merge_rows(
            extract_work_history(raw_sections.get("工作经历", "")),
            table_work_history,
            ["time", "company", "position"],
        ),
        "skills": extract_skills("\n".join([raw_sections.get("专业技能", ""), table_skills_text])),
        "projects": merge_rows(
            raw_projects,
            table_projects,
            ["name", "date", "description"],
        ),
        "certificates": canonical_certificates,
        "capability_profile": build_capability_profile(structured_certificates),
        "embedded_certificate_candidates": embedded_candidates,
        "raw_sections": raw_sections,
        "parse_warnings": [],
    }

    warnings = structured["parse_warnings"]
    if not text.strip():
        warnings.append("parsed_text is empty")
    if not structured["person_name"]:
        warnings.append("person_name not found")
    if not structured["gender"]:
        warnings.append("gender not found")
    if structured["work_years"] is None:
        warnings.append("work_years not found")
    if not structured["department"]:
        warnings.append("department not provided and not resolved from source path")
    if not structured["projects"]:
        warnings.append("project experience not found")
    if embedded_candidates and any(candidate.get("needs_ocr") for candidate in embedded_candidates):
        warnings.append("embedded images need OCR before certificate names can be confirmed")
    warnings.extend(certificate_warnings)

    return structured


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \u3000]+\n", "\n", text)
    return text.strip()


def parse_table_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    parts = re.split(r"^=== Table (?P<index>\d+) ===\s*$", text, flags=re.M)
    for offset in range(1, len(parts), 2):
        index = int(parts[offset])
        body = parts[offset + 1] if offset + 1 < len(parts) else ""
        rows = []
        for line in body.splitlines():
            if not line.strip():
                continue
            if line.startswith("=== Table "):
                break
            if "\t" not in line and rows:
                rows[-1][-1] = "\n".join(part for part in [rows[-1][-1], line.strip()] if part)
                continue
            rows.append([cell.strip() for cell in line.split("\t")])
        blocks.append({"table_index": index, "rows": rows})
    return blocks


def build_profile_text_from_tables(table_blocks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    wanted = {"姓名", "性别", "工作年限", "邮箱", "联系电话"}
    for block in table_blocks:
        for row in block.get("rows", []):
            row_values = [cell for cell in row if cell]
            if not any(cell in wanted for cell in row_values):
                continue
            for label in wanted:
                value = value_after_label(row_values, label)
                if value:
                    lines.append(f"{label}：{value}")
    return "\n".join(lines)


def value_after_label(row_values: list[str], label: str) -> str | None:
    for index, cell in enumerate(row_values):
        if cell != label:
            continue
        for value in row_values[index + 1 :]:
            if value and value != label:
                return value
    return None


def extract_work_history_from_tables(table_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for block in table_blocks:
        rows = block.get("rows", [])
        if not rows:
            continue
        header = rows[0]
        if not {"时间", "公司", "职位"}.issubset(set(header)):
            continue
        time_index = header.index("时间")
        company_index = header.index("公司")
        position_index = header.index("职位")
        for row in rows[1:]:
            entries.append(
                {
                    "time": cell_at(row, time_index),
                    "company": cell_at(row, company_index),
                    "position": cell_at(row, position_index),
                    "raw_text": "\t".join(row),
                }
            )
    return [entry for entry in entries if any(entry.get(key) for key in ["time", "company", "position"])]


def build_skills_text_from_tables(table_blocks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for block in table_blocks:
        rows = block.get("rows", [])
        if not rows:
            continue
        if any(is_project_table(rows) or is_profile_table(rows) or is_work_history_table(rows) for _ in [None]):
            continue
        for row in rows:
            row_values = [cell for cell in row if cell]
            if len(row_values) >= 2 and re.search(r"\d{1,2}\s*年", row_values[1]) and detect_known_techs(row_values[0]):
                lines.append("：".join([row_values[0], row_values[1]]))
    return "\n".join(lines)


def extract_projects_from_tables(table_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    for block in table_blocks:
        rows = block.get("rows", [])
        if not is_project_table(rows):
            continue
        kv = table_key_values(rows)
        raw_text = "\n".join("\t".join(row) for row in rows)
        products = clean_value(kv.get("涉及产品") or kv.get("产品或技术") or kv.get("技术环境"))
        if not products:
            detected_techs = detect_known_techs(raw_text)
            products = "、".join(detected_techs) if detected_techs else None
        projects.append(
            {
                "name": clean_value(kv.get("项目名称") or kv.get("项目名称：") or kv.get("项目")),
                "date": clean_value(kv.get("项目日期") or kv.get("项目时间") or kv.get("项目周期")),
                "description": clean_value(kv.get("项目描述") or kv.get("工作内容描述") or kv.get("项目简介")),
                "products_or_technologies": products,
                "role_or_work_content": clean_value(kv.get("角色或工作内容") or kv.get("工作内容描述") or kv.get("项目职责")),
                "raw_text": raw_text,
            }
        )
    return [project for project in projects if project.get("name") or project.get("description")]


def extract_certificates_from_tables(table_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    certificates: list[dict[str, Any]] = []
    for block in table_blocks:
        rows = block.get("rows", [])
        if not rows or is_project_table(rows) or is_profile_table(rows) or is_work_history_table(rows):
            continue
        for row in rows:
            row_values = [cell for cell in row if cell]
            if len(row_values) < 2:
                continue
            evidence = " ".join(row_values)
            if looks_like_certificate(evidence):
                certificates.append(build_certificate(evidence, source_type="text", evidence_level="explicit_text"))
    return certificates


def is_profile_table(rows: list[list[str]]) -> bool:
    flat = {cell for row in rows for cell in row if cell}
    return bool({"姓名", "性别", "工作年限"} & flat)


def is_work_history_table(rows: list[list[str]]) -> bool:
    return bool(rows and {"时间", "公司", "职位"}.issubset(set(rows[0])))


def is_project_table(rows: list[list[str]]) -> bool:
    flat = {cell.rstrip("：:") for row in rows for cell in row if cell}
    return "项目名称" in flat and ({"项目描述", "工作内容描述", "项目日期", "项目时间"} & flat)


def table_key_values(rows: list[list[str]]) -> dict[str, str]:
    values: dict[str, str] = {}
    for row in rows:
        if len(row) < 2:
            continue
        key = row[0].strip()
        value = "\n".join(cell for cell in row[1:] if cell.strip()).strip()
        values[key] = value
        values[key.rstrip("：:")] = value
    return values


def cell_at(row: list[str], index: int) -> str | None:
    return row[index].strip() if index < len(row) and row[index].strip() else None


def merge_rows(primary: list[dict[str, Any]], secondary: list[dict[str, Any]], key_fields: list[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for item in [*primary, *secondary]:
        key = tuple(str(item.get(field) or "") for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def parse_source_department(source_path: str | None = None, source_folder: str | None = None) -> dict[str, str | None]:
    raw = clean_value(source_folder) or clean_value(source_path)
    if not raw:
        return {"department": None, "source_group": None, "source_folder": None}

    normalized = raw.replace("\\", "/").strip("/")
    parts = [part for part in normalized.split("/") if part and part != "."]
    folder_parts = list(parts)
    if folder_parts and re.search(r"\.[A-Za-z0-9]{1,8}$", folder_parts[-1]):
        folder_parts = folder_parts[:-1]
    source_folder_value = "/".join(folder_parts) if folder_parts else normalized

    department = None
    source_group = None
    for index, part in enumerate(folder_parts):
        if "工程师简历" in part and index + 1 < len(folder_parts):
            department = folder_parts[index + 1]
            if index + 2 < len(folder_parts):
                source_group = folder_parts[index + 2]
            break

    internal_roots = {"original", "storage", "assets", "parsed"}
    if department is None and len(folder_parts) >= 2 and folder_parts[0].lower() not in internal_roots:
        department = folder_parts[-2]
        source_group = folder_parts[-1]

    return {
        "department": department,
        "source_group": source_group,
        "source_folder": source_folder_value or None,
    }


def split_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {"_preamble": []}
    current = "_preamble"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        heading = normalize_heading(line)
        if heading:
            current = heading
            sections.setdefault(current, [])
            remainder = strip_heading_prefix(line, heading)
            if remainder:
                sections[current].append(remainder)
            continue
        sections.setdefault(current, []).append(raw_line)
    return {key: "\n".join(value).strip() for key, value in sections.items() if "\n".join(value).strip()}


def normalize_heading(line: str) -> str | None:
    candidate = re.sub(r"^[一二三四五六七八九十0-9]+[、.)．]\s*", "", line).strip()
    candidate = candidate.rstrip(":：")
    for title in SECTION_TITLES:
        if candidate == title:
            return "相关证书" if title == "证书" else ("项目经验" if title == "项目经历" else title)
        if candidate.startswith(title) and len(candidate) <= len(title) + 3:
            return "相关证书" if title == "证书" else ("项目经验" if title == "项目经历" else title)
    return None


def strip_heading_prefix(line: str, heading: str) -> str:
    return re.sub(rf"^[一二三四五六七八九十0-9]*[、.)．]?\s*{re.escape(heading)}\s*[:：]?\s*", "", line).strip()


def extract_labeled_value(text: str, labels: list[str]) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        for label in labels:
            match = re.match(rf"^{re.escape(label)}\s*[:：\t ]+\s*(?P<value>.+)$", line)
            if match:
                return clean_profile_value(match.group("value"))
            if line == label and index + 1 < len(lines):
                return clean_profile_value(lines[index + 1])
    return None


def clean_profile_value(value: str) -> str | None:
    value = clean_value(value)
    if not value:
        return None
    value = re.split(r"\s{2,}|\t", value)[0].strip()
    return value or None


def normalize_gender(value: str | None) -> str | None:
    if not value:
        return None
    if "男" in value:
        return "男"
    if "女" in value:
        return "女"
    return value


def extract_work_years(text: str) -> int | None:
    patterns = [
        r"工作年限\s*[:：\t ]*\s*(?P<years>\d{1,2})\s*年",
        r"(?P<years>\d{1,2})\s*年\s*(?:工作|从业|相关工作)?经验",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group("years"))
    return None


def extract_work_history(section_text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for block in split_blocks(section_text):
        row = parse_work_history_block(block)
        if row:
            entries.append(row)
    return entries


def parse_work_history_block(block: str) -> dict[str, Any] | None:
    lines = [line.strip(" \t-•●◆") for line in block.splitlines() if line.strip()]
    if not lines:
        return None
    joined = "\n".join(lines)
    tab_cells = [cell.strip() for cell in re.split(r"\t+", lines[0]) if cell.strip()]
    time_value = None
    company = None
    position = None
    if len(tab_cells) >= 3 and TIME_PATTERN.search(tab_cells[0]):
        time_value, company, position = tab_cells[0], tab_cells[1], tab_cells[2]
    else:
        time_match = TIME_PATTERN.search(joined)
        time_value = clean_value(time_match.group("time")) if time_match else None
        company = extract_labeled_value(joined, ["公司", "任职公司", "单位"])
        position = extract_labeled_value(joined, ["职位", "岗位", "职务"])
        if not company or not position:
            first_line_without_time = TIME_PATTERN.sub("", lines[0]).strip(" -—|，,")
            chunks = [item.strip() for item in re.split(r"\s{2,}|[|｜]", first_line_without_time) if item.strip()]
            if len(chunks) >= 2:
                company = company or chunks[0]
                position = position or chunks[1]
    if not any([time_value, company, position]):
        return None
    return {"time": time_value, "company": company, "position": position, "raw_text": block.strip()}


def extract_skills(section_text: str) -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_item in split_items(section_text):
        item = raw_item.strip(" \t-•●◆；;")
        if not item:
            continue
        detected_names = detect_known_techs(item)
        if not detected_names:
            label_match = re.match(r"^(?P<name>[\w\u4e00-\u9fff .+#-]{1,40})\s*[:：]\s*(?P<rest>.+)$", item)
            detected_names = [label_match.group("name").strip()] if label_match else []
        for name in detected_names:
            key = name.lower().replace(" ", "")
            if key in seen:
                continue
            seen.add(key)
            skills.append({"name": name, "years": extract_years_text(item), "raw_text": item})
    return skills


def detect_known_techs(text: str) -> list[str]:
    found: list[str] = []
    for tech in KNOWN_TECHS:
        if re.search(rf"(?<![A-Za-z]){re.escape(tech)}(?![A-Za-z])", text, re.I):
            canonical = {
                "Power Platform": "PowerPlatform",
                "Microsoft 365": "M365",
                "Office 365": "M365",
                "NUTANIX": "Nutanix",
            }.get(tech, tech)
            if canonical not in found:
                found.append(canonical)
    return found


def extract_years_text(text: str) -> str | None:
    match = re.search(r"(?P<years>\d{1,2})\s*年", text)
    return f"{match.group('years')}年" if match else None


def extract_projects(section_text: str) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    for block in split_project_blocks(section_text):
        project = parse_project_block(block)
        if project and (project.get("name") or project.get("description") or project.get("date")):
            projects.append(project)
    return projects


def split_project_blocks(section_text: str) -> list[str]:
    text = section_text.strip()
    if not text:
        return []
    if re.search(r"项目名称\s*[:：]", text):
        chunks = re.split(r"(?=项目名称\s*[:：])", text)
        return [chunk.strip() for chunk in chunks if chunk.strip()]
    blocks = split_blocks(text)
    if len(blocks) > 1:
        return blocks
    lines = [line for line in text.splitlines() if line.strip()]
    starts = [idx for idx, line in enumerate(lines) if re.match(r"^\s*(?:项目\d+|[一二三四五六七八九十]+[、.．])", line)]
    if starts:
        result = []
        for offset, start in enumerate(starts):
            end = starts[offset + 1] if offset + 1 < len(starts) else len(lines)
            result.append("\n".join(lines[start:end]).strip())
        return result
    return [text]


def parse_project_block(block: str) -> dict[str, Any]:
    name = extract_labeled_value(block, ["项目名称", "项目"])
    date = extract_labeled_value(block, ["项目日期", "项目时间", "项目周期", "时间"])
    description = extract_labeled_paragraph(block, ["项目描述", "项目简介", "描述"])
    products = extract_labeled_paragraph(block, ["产品", "技术", "技术环境", "产品或技术", "使用技术"])
    role = extract_labeled_paragraph(block, ["角色", "职责", "工作内容", "项目职责", "承担工作"])

    lines = [line.strip(" \t-•●◆") for line in block.splitlines() if line.strip()]
    if not name and lines:
        first = re.sub(r"^[一二三四五六七八九十0-9]+[、.)．]\s*", "", lines[0]).strip()
        if not re.match(r"^(项目日期|项目时间|项目描述|描述|职责|工作内容)\s*[:：]", first):
            name = re.sub(r"^项目名称\s*[:：]\s*", "", first).strip()
    if not date:
        match = TIME_PATTERN.search(block)
        date = clean_value(match.group("time")) if match else None
    if not products:
        detected = detect_known_techs(block)
        products = "、".join(detected) if detected else None
    if not description:
        description = build_unlabeled_description(lines)

    return {
        "name": clean_value(name),
        "date": clean_value(date),
        "description": clean_value(description),
        "products_or_technologies": clean_value(products),
        "role_or_work_content": clean_value(role),
        "raw_text": block.strip(),
    }


def extract_labeled_paragraph(text: str, labels: list[str]) -> str | None:
    lines = [line.rstrip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        stripped = line.strip()
        for label in labels:
            match = re.match(rf"^{re.escape(label)}\s*[:：\t ]+\s*(?P<value>.*)$", stripped)
            if not match:
                continue
            values = [match.group("value").strip()] if match.group("value").strip() else []
            for next_line in lines[index + 1 :]:
                next_stripped = next_line.strip()
                if not next_stripped:
                    break
                if re.match(r"^[\u4e00-\u9fffA-Za-z0-9 /（）()_-]{1,12}\s*[:：]", next_stripped):
                    break
                values.append(next_stripped)
            return "\n".join(values).strip() or None
    return None


def build_unlabeled_description(lines: list[str]) -> str | None:
    kept = []
    for line in lines[1:]:
        if re.match(r"^(项目名称|项目日期|项目时间|时间|技术|产品|角色|职责|工作内容)\s*[:：]", line):
            continue
        kept.append(line)
    return "\n".join(kept[:6]).strip() or None


def extract_certificates_from_text(section_text: str) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    if not section_text.strip():
        return [], warnings

    certificates: list[dict[str, Any]] = []
    for item in split_items(section_text):
        item = item.strip(" \t-•●◆；;")
        if not item or not looks_like_certificate(item):
            continue
        certificates.append(build_certificate(item, source_type="text", evidence_level="explicit_text"))
    return certificates, warnings


def extract_embedded_certificate_candidates(
    assets: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    certificates: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for asset in assets:
        if asset.get("asset_type") and asset.get("asset_type") != "image":
            continue
        ocr_text = clean_value(asset.get("ocr_text") or asset.get("text") or asset.get("parsed_text"))
        base = {
            "asset_id": asset.get("id"),
            "source_location": asset.get("source_location") or asset.get("asset_key"),
            "asset_key": asset.get("asset_key"),
            "source_type": "embedded_image",
        }
        if ocr_text:
            cert_base = {**base, "needs_ocr": False, "ocr_text": ocr_text}
            extracted = extract_certificates_from_ocr_text(ocr_text, cert_base)
            certificates.extend(extracted)
            candidates.append({**base, "ocr_text": ocr_text, "needs_ocr": False, "extracted_count": len(extracted)})
            if not extracted:
                unmatched = build_ocr_unmatched_certificate(ocr_text, base)
                certificates.append(unmatched)
        else:
            image_only = build_image_pending_certificate(base)
            certificates.append(image_only)
            candidates.append(image_only)
    return certificates, candidates


def looks_like_certificate(text: str) -> bool:
    return any(re.search(re.escape(keyword), text, re.I) for keyword in CERT_KEYWORDS)


def build_certificate(
    evidence_text: str,
    source_type: str,
    evidence_level: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    name = extract_certificate_name(evidence_text) or clean_certificate_name(evidence_text)
    normalized = standardize_certificate(evidence_text, name)
    certificate = {
        "name": name,
        "certificate_full_name": normalized.get("certificate_full_name") or name,
        "certificate_short_name": normalized.get("certificate_short_name"),
        "normalized_name": normalized.get("normalized_name") or name,
        "vendor": normalized.get("vendor") or detect_vendor(evidence_text),
        "level": normalized.get("level") or detect_certificate_level(evidence_text),
        "holder_name": extract_holder_name(evidence_text),
        "certificate_no": extract_certificate_no(evidence_text),
        "issue_date": extract_issue_date(evidence_text),
        "expiry_date": extract_expiry_date(evidence_text),
        "source_type": source_type,
        "evidence_text": evidence_text.strip(),
        "evidence_level": evidence_level,
        "needs_ocr": False,
        "usable": bool(name),
        "review_required": not bool(name),
        "review_reason": None if name else "certificate name not extracted",
        "standardization_status": normalized.get("standardization_status") or ("original_kept" if name else "name_not_extracted"),
        "standardization_confidence": normalized.get("standardization_confidence") or ("medium" if name else "low"),
    }
    if extra:
        certificate.update(extra)
    return apply_certificate_family(certificate)


def extract_certificates_from_ocr_text(ocr_text: str, base: dict[str, Any]) -> list[dict[str, Any]]:
    text = normalize_text(ocr_text)
    if not text:
        return []
    if not looks_like_certificate(text):
        return []

    certificate = build_certificate(text, "embedded_image", "ocr_text", base)
    if not certificate.get("name"):
        return []
    certificate["needs_ocr"] = False
    certificate["usable"] = True
    certificate["review_required"] = False
    certificate["review_reason"] = None
    return [certificate]


def build_image_pending_certificate(base: dict[str, Any], error: str | None = None) -> dict[str, Any]:
    certificate = {
        **base,
        "name": None,
        "certificate_full_name": None,
        "certificate_short_name": None,
        "normalized_name": None,
        "vendor": None,
        "level": None,
        "holder_name": None,
        "certificate_no": None,
        "issue_date": None,
        "expiry_date": None,
        "evidence_text": "",
        "evidence_level": "image_only",
        "needs_ocr": True,
        "usable": False,
        "review_required": True,
        "review_reason": error or "embedded image has no OCR text",
        "standardization_status": "image_pending_ocr",
        "standardization_confidence": "low",
    }
    return apply_certificate_family(certificate)


def build_ocr_unmatched_certificate(ocr_text: str, base: dict[str, Any]) -> dict[str, Any]:
    certificate = {
        **base,
        "name": None,
        "certificate_full_name": None,
        "certificate_short_name": None,
        "normalized_name": None,
        "vendor": detect_vendor(ocr_text),
        "level": detect_certificate_level(ocr_text),
        "holder_name": extract_holder_name(ocr_text),
        "certificate_no": extract_certificate_no(ocr_text),
        "issue_date": extract_issue_date(ocr_text),
        "expiry_date": extract_expiry_date(ocr_text),
        "ocr_text": ocr_text,
        "evidence_text": ocr_text,
        "evidence_level": "ocr_text",
        "needs_ocr": False,
        "usable": False,
        "review_required": True,
        "review_reason": "ocr text found but certificate name not extracted",
        "standardization_status": "name_not_extracted",
        "standardization_confidence": "low",
    }
    return apply_certificate_family(certificate)


def apply_certificate_family(certificate: dict[str, Any]) -> dict[str, Any]:
    family_text = "\n".join(
        str(value)
        for value in [
            certificate.get("name"),
            certificate.get("normalized_name"),
            certificate.get("certificate_full_name"),
            certificate.get("certificate_short_name"),
            certificate.get("evidence_text"),
            certificate.get("ocr_text"),
        ]
        if value
    )
    family_result = match_certificate_family(family_text)
    certificate["family"] = family_result.get("family")
    certificate["family_canonical"] = family_result.get("canonical")
    certificate["family_confidence"] = family_result.get("confidence", 0.0)
    certificate["family_matched_alias"] = family_result.get("matched_alias")
    standard = standardize_certificate_family(certificate.get("family"), certificate.get("family_matched_alias"))
    certificate["standard_name"] = standard.get("standard_name")
    certificate["short_name"] = standard.get("short_name")
    certificate["display_name"] = standard.get("display_name")
    certificate["standard_source_alias"] = standard.get("source_alias")

    confidence = float(certificate.get("family_confidence") or 0.0)
    if 0.0 < confidence < 0.6:
        certificate["review_required"] = True
        certificate["review_reason"] = certificate.get("review_reason") or "certificate family confidence below 0.6"
    return certificate


def bind_certificate_owners(
    certificates: list[dict[str, Any]],
    person_name: str | None = None,
    file_id: str | None = None,
    original_name: str | None = None,
    source_path: str | None = None,
) -> list[dict[str, Any]]:
    ownership_inputs = []
    for certificate in certificates:
        item = dict(certificate)
        if person_name:
            item["resume_person_name"] = person_name
        if file_id:
            item["file_id"] = file_id
        if original_name:
            item["original_name"] = original_name
        if source_path:
            item["source_path"] = source_path
        ownership_inputs.append(item)
    resolved = batch_resolve_certificates(ownership_inputs)
    for item in resolved:
        item.pop("resume_person_name", None)
        item.pop("original_name", None)
        item.pop("source_path", None)
        item.pop("file_id", None)
    return resolved


def clean_certificate_name(text: str) -> str:
    text = re.sub(r"^[\d一二三四五六七八九十]+[、.)．]\s*", "", text.strip())
    text = re.sub(r"^(持有|获得|具备)\s*", "", text)
    text = re.split(r"[，,；;。]", text)[0].strip()
    return text


def extract_certificate_name(text: str) -> str | None:
    compact = " ".join(text.split())
    patterns = [
        r"VMware Certified Professional(?:\s*[-–—]?\s*Data Center Virtualization)?(?:\s*VCP-?DCV)?",
        r"VCP-?DCV",
        r"VMware\s+VCP(?:-?DCV)?",
        r"Nutanix Certified Professional",
        r"NUTANIX\s+NCP",
        r"Nutanix\s+NCP",
        r"\bNCP\b",
        r"Alibaba Cloud Certified Professional",
        r"Alibaba Cloud\s+ACP",
        r"阿里云\s*ACP",
        r"\bACP\b",
        r"\bAZ-104\b",
        r"\bAZ-140\b",
        r"Azure\s+140",
        r"深信服[^\n，,；;。]{0,40}",
        r"Sangfor[^\n，,；;。]{0,40}",
        r"\bNCSC\b",
        r"\bSCTP\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, compact, re.I)
        if match:
            return match.group(0).strip()
    if looks_like_certificate(compact):
        return clean_certificate_name(compact)
    return None


def standardize_certificate(evidence_text: str, name: str | None) -> dict[str, Any]:
    text = " ".join(part for part in [evidence_text, name or ""] if part)
    compact = " ".join(text.split())
    lower = compact.lower()

    if re.search(r"vmware|vcp|data center virtualization", compact, re.I):
        short_name = "VCP-DCV" if re.search(r"vcp-?dcv|data center virtualization", compact, re.I) else "VCP"
        return {
            "certificate_full_name": "VMware Certified Professional - Data Center Virtualization"
            if short_name == "VCP-DCV"
            else "VMware Certified Professional",
            "certificate_short_name": short_name,
            "normalized_name": "VMware VCP",
            "vendor": "VMware",
            "level": "VCP",
            "standardization_status": "normalized",
            "standardization_confidence": "high",
        }
    if re.search(r"\b(NCSC|SCTP)\b", compact, re.I):
        level = re.search(r"\b(NCSC|SCTP)\b", compact, re.I).group(1).upper()
        return {
            "certificate_full_name": name or level,
            "certificate_short_name": level,
            "normalized_name": name or level,
            "vendor": detect_vendor(compact),
            "level": level,
            "standardization_status": "original_kept",
            "standardization_confidence": "low",
        }
    if re.search(r"nutanix|ncp", compact, re.I):
        return {
            "certificate_full_name": "Nutanix Certified Professional",
            "certificate_short_name": "NCP",
            "normalized_name": "Nutanix NCP",
            "vendor": "Nutanix",
            "level": "NCP",
            "standardization_status": "normalized",
            "standardization_confidence": "high",
        }
    if re.search(r"阿里云|alibaba cloud|aliyun|acp", compact, re.I):
        return {
            "certificate_full_name": "Alibaba Cloud Certified Professional",
            "certificate_short_name": "ACP",
            "normalized_name": "Alibaba Cloud ACP",
            "vendor": "Alibaba Cloud",
            "level": "ACP",
            "standardization_status": "normalized",
            "standardization_confidence": "high",
        }
    if re.search(r"\bAZ-104\b", compact, re.I):
        return {
            "certificate_full_name": "Microsoft Azure AZ-104",
            "certificate_short_name": "AZ-104",
            "normalized_name": "Microsoft Azure AZ-104",
            "vendor": "Microsoft",
            "level": "AZ-104",
            "standardization_status": "normalized",
            "standardization_confidence": "high",
        }
    if re.search(r"\bAZ-140\b", compact, re.I):
        return {
            "certificate_full_name": "Microsoft Azure AZ-140",
            "certificate_short_name": "AZ-140",
            "normalized_name": "Microsoft Azure AZ-140",
            "vendor": "Microsoft",
            "level": "AZ-140",
            "standardization_status": "normalized",
            "standardization_confidence": "high",
        }
    if re.search(r"azure\s+140", lower):
        return {
            "certificate_full_name": name or "Azure 140",
            "certificate_short_name": "Azure 140",
            "normalized_name": "Azure 140",
            "vendor": "Microsoft",
            "level": "140",
            "standardization_status": "original_kept",
            "standardization_confidence": "medium",
        }
    if re.search(r"深信服|sangfor|hci|超融合", compact, re.I):
        vendor = "深信服" if "深信服" in compact else "Sangfor"
        return {
            "certificate_full_name": name,
            "certificate_short_name": detect_certificate_level(compact),
            "normalized_name": name,
            "vendor": vendor,
            "level": detect_certificate_level(compact),
            "standardization_status": "original_kept",
            "standardization_confidence": "medium",
        }
    return {
        "certificate_full_name": name,
        "certificate_short_name": detect_certificate_level(compact),
        "normalized_name": name,
        "vendor": detect_vendor(compact),
        "level": detect_certificate_level(compact),
        "standardization_status": "original_kept" if name else "name_not_extracted",
        "standardization_confidence": "medium" if name else "low",
    }


def extract_holder_name(text: str) -> str | None:
    patterns = [
        r"Issued\s+to\s+(?P<value>[\u4e00-\u9fffA-Za-z .·-]{2,40})",
        r"授予\s*(?P<value>[\u4e00-\u9fffA-Za-z .·-]{2,20})",
        r"持证人\s*[:：]?\s*(?P<value>[\u4e00-\u9fffA-Za-z .·-]{2,20})",
        r"姓名\s*[:：]?\s*(?P<value>[\u4e00-\u9fffA-Za-z .·-]{2,20})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return clean_certificate_capture(match.group("value"))
    return None


def extract_certificate_no(text: str) -> str | None:
    patterns = [
        r"Certificate\s+(?:ID|No\.?|Number)\s*[:：#]?\s*(?P<value>[A-Za-z0-9._/-]{3,60})",
        r"证书(?:编号|号)\s*[:：]?\s*(?P<value>[A-Za-z0-9._/-]{3,60})",
        r"\bID\s*[:：#]?\s*(?P<value>[A-Za-z0-9._/-]{3,60})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return clean_certificate_capture(match.group("value"))
    return None


def extract_issue_date(text: str) -> str | None:
    patterns = [
        r"(?:Issue Date|Issued on|Issued)\s*[:：]?\s*(?P<value>\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?)",
        r"(?:发证日期|签发日期|颁发日期)\s*[:：]?\s*(?P<value>\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?)",
    ]
    return first_date_match(text, patterns)


def extract_expiry_date(text: str) -> str | None:
    patterns = [
        r"(?:Expiry Date|Expires|Valid Until|Expiration Date)\s*[:：]?\s*(?P<value>\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?)",
        r"(?:有效期至|到期日期|失效日期)\s*[:：]?\s*(?P<value>\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?)",
    ]
    return first_date_match(text, patterns)


def first_date_match(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return clean_certificate_capture(match.group("value"))
    return None


def clean_certificate_capture(text: str) -> str:
    return re.split(r"[\n\r，,；;。|]", str(text).strip())[0].strip()


def detect_vendor(text: str) -> str | None:
    for vendor, pattern in VENDOR_PATTERNS:
        if pattern.search(text):
            return vendor
    return None


def detect_certificate_level(text: str) -> str | None:
    levels = ["VCP", "NCP", "ACP", "HCIA", "HCIP", "HCIE", "PMP", "ITIL", "CCNA", "CCNP", "CISP", "CISSP", "RHCE", "RHCSA"]
    for level in levels:
        if re.search(rf"\b{re.escape(level)}\b", text, re.I):
            return level.upper()
    match = re.search(r"([一二三四五六七八九十0-9]+级|高级|中级|初级)", text)
    return match.group(1) if match else None


def dedupe_certificates(certificates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any, Any]] = set()
    for cert in certificates:
        key = (
            cert.get("name"),
            cert.get("asset_id"),
            cert.get("source_type"),
            cert.get("evidence_text"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(cert)
    return result


def split_blocks(text: str) -> list[str]:
    normalized = normalize_text(text)
    if not normalized:
        return []
    blocks = [block.strip() for block in re.split(r"\n\s*\n+", normalized) if block.strip()]
    if len(blocks) > 1:
        return blocks
    lines = [line for line in normalized.splitlines() if line.strip()]
    starts = [idx for idx, line in enumerate(lines) if TIME_PATTERN.search(line) and idx != 0]
    if starts:
        result = []
        start_indexes = [0] + starts
        for offset, start in enumerate(start_indexes):
            end = start_indexes[offset + 1] if offset + 1 < len(start_indexes) else len(lines)
            result.append("\n".join(lines[start:end]).strip())
        return [item for item in result if item]
    return [normalized]


def split_items(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = re.split(r"[；;]\s*|(?<=[。])\s*", stripped)
        for part in parts:
            part = part.strip()
            if part:
                lines.append(part)
    return lines


def clean_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def source_folder_as_path(source_folder: str | None) -> PurePosixPath | None:
    if not source_folder:
        return None
    return PurePosixPath(source_folder.replace("\\", "/"))
