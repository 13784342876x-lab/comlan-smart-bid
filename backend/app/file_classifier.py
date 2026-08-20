from app.schemas import FileClassification
from app.tender_source_guard import is_tender_notice_attachment_file, is_tender_notice_file, is_tender_source_file, tender_source_material_types, tender_source_type


CLASSIFY_RULES = [
    ("营业执照", ["营业执照"], "企业资质库", "营业执照", ["营业执照", "资格文件"], "用于资格证明文件。"),
    ("ISO9001质量管理体系认证", ["ISO9001", "质量管理体系认证"], "企业资质库", "体系认证", ["ISO9001", "质量管理"], "用于质量体系和企业资质响应。"),
    ("ISO27001信息安全管理体系认证", ["ISO27001", "信息安全管理体系认证"], "企业资质库", "体系认证", ["ISO27001", "信息安全"], "用于信息安全能力证明。"),
    ("AAA信用等级证书", ["AAA", "信用等级"], "企业资质库", "信用证书", ["AAA", "信用等级"], "用于企业信誉和商务评分响应。"),
    ("专利证书", ["专利"], "企业资质库", "专利证书", ["专利", "知识产权"], "用于技术能力和创新能力证明。"),
    ("软件著作权", ["软件著作权", "软著"], "企业资质库", "软件著作权", ["软著", "软件著作权"], "用于软件能力和知识产权证明。"),
    ("合同", ["合同"], "业绩案例库", "合同", ["合同", "业绩案例"], "用于同类业绩证明。"),
    ("中标通知书", ["中标通知书"], "业绩案例库", "中标通知书", ["中标通知书", "业绩案例"], "用于中标业绩证明。"),
    ("验收报告", ["验收报告"], "业绩案例库", "验收报告", ["验收报告", "项目验收"], "用于项目履约和验收能力证明。"),
    ("产品手册", ["产品手册", "技术参数"], "产品资料库", "产品手册", ["产品手册", "技术参数"], "用于技术方案和参数响应。"),
    ("检测报告", ["检测报告"], "产品资料库", "检测报告", ["检测报告", "技术参数"], "用于技术参数证明。"),
]


def classify_file(record: dict) -> FileClassification:
    if is_tender_source_file(record):
        material_types = tender_source_material_types(record)
        source_type = tender_source_type(record)
        if source_type == "tender_notice":
            category = "招标公告"
            document_type = "招标公告"
        elif source_type == "tender_notice_attachment":
            category = "招标公告附件"
            document_type = "招标公告附件"
        elif source_type == "procurement_package_table":
            category = "招标需求一览表"
            document_type = "招标需求一览表"
        else:
            category = "招标文件"
            document_type = "完整招标文件正文" if "full_tender_document" in material_types else "招标来源文件"
        return FileClassification(
            source_file_id=record["id"],
            category=category,
            document_type=document_type,
            material_group="tender_source",
            source_type=source_type,
            material_types=material_types,
            tags=material_types,
            valid_from=None,
            valid_to=None,
            recommended_usage="用于招标项目元信息、响应文件组成、签字盖章、否决条款、资格条件、服务期限和采购需求依据抽取。",
            confidence=0.95,
            is_bundle=False,
        )

    text = build_classification_text(record)
    matches = []
    for name, keywords, category, document_type, tags, usage in CLASSIFY_RULES:
        if any(keyword in text for keyword in keywords):
            matches.append(
                {
                    "name": name,
                    "keywords": keywords,
                    "category": category,
                    "document_type": document_type,
                    "tags": tags,
                    "usage": usage,
                }
            )

    assets_count = len(record.get("assets", []))
    is_word_pdf = record.get("file_ext") in {"doc", "docx", "pdf"}
    is_bundle = len(matches) > 1 or (is_word_pdf and assets_count > 2)

    if matches:
        primary = matches[0]
        tags = sorted({tag for match in matches for tag in match["tags"]})
        confidence = 0.82 if len(matches) == 1 else 0.9
        return FileClassification(
            source_file_id=record["id"],
            category=primary["category"],
            document_type=primary["document_type"] if not is_bundle else "汇总资料",
            tags=tags,
            valid_from=None,
            valid_to=None,
            recommended_usage="；".join(match["usage"] for match in matches[:3]),
            confidence=confidence,
            is_bundle=is_bundle,
        )

    return FileClassification(
        source_file_id=record["id"],
        category=record.get("category", "常用素材库"),
        document_type="未分类资料",
        tags=["待确认"],
        valid_from=None,
        valid_to=None,
        recommended_usage="待人工确认资料类型和用途。",
        confidence=0.35,
        is_bundle=False,
    )


def build_classification_text(record: dict) -> str:
    asset_text = " ".join(f"{asset.get('name', '')} {asset.get('ocr_text', '')}" for asset in record.get("assets", []))
    return f"{record.get('original_name', '')} {record.get('parsed_text', '')} {asset_text}"
