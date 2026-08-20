from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from app.document_clause_indexer import build_clause_index
from app.llm_client import FORBIDDEN_DECISION_TERMS
from app.response_outline_builder import build_response_outline, save_response_outline_index


def sample_metadata() -> dict:
    return {
        "files": [
            {
                "id": "file_tender_001",
                "original_name": "招标文件附件-服务.doc",
                "source_folder": "招标包",
                "parsed_text": "Ⅰ、评标办法\nⅡ、合同条款及格式\nⅢ、委托人要求（技术标准和要求）\n技术标准详见技术规范书；",
                "tables": [],
                "assets": [],
            },
            {
                "id": "file_spec_001",
                "original_name": "技术规范书.docx",
                "source_folder": "招标包",
                "parsed_text": "\n".join(
                    [
                        "附件3：项目招标文件模板 国网河北省电力有限公司",
                        "目 录",
                        "1.2 招标要求",
                        "本项目围绕工业数据安全、隐私保护、访问控制和监管场景开展研究与服务，投标方应形成完整技术响应方案。",
                        "1. 投标须知 1",
                        "1.3对投标人的技术要求 2",
                        "投标人应具备区块链、智能合约、安全分析和性能测试相关研发能力，并说明项目实施与技术服务计划。",
                        "2.1 概述",
                        "服务方案应覆盖总体思路、研究内容、交付物、质量控制和沟通机制。",
                        "2.2 投标方职责",
                        "投标方负责项目计划、进度跟踪、阶段汇报、质量保障和验收配合。",
                        "4.1 研究内容",
                        "研究内容包括工业数据流转、隐私保护、访问控制和监管分析。",
                        "4.2 研究技术指标要求",
                        "技术指标包括智能合约安全分析、性能测试和数据安全评估。",
                        "5 技术培训",
                        "投标方应提供技术培训材料和培训记录。",
                        "6 验收测试",
                        "投标方应配合完成测试验收并提交验收材料。",
                        "7 质量保证要求与售后服务",
                        "投标方应提供质量保证和售后服务安排。",
                        "8.特别声明及免责条款",
                        "本文件解释权归采购人所有，任何目录或模板文字不得作为技术响应实质条款。",
                    ]
                ),
                "tables": [],
                "assets": [],
            },
            {
                "id": "file_person_001",
                "material_profile": {"material_group": "personnel", "material_types": ["person_resume", "personal_certificate"], "classification_source": "manual_override"},
                "personnel_profile": {"person_name": "张三"},
                "original_name": "张三简历.docx",
                "parsed_text": "项目负责人 简历 Python PMP 工业数据安全平台研发",
                "tables": [],
                "assets": [],
            },
            {
                "id": "file_person_002",
                "material_profile": {"material_group": "personnel", "material_types": ["person_resume"], "classification_source": "manual_override"},
                "original_name": "李四简历.docx",
                "parsed_text": "团队成员 简历 运维 培训 企业信息化项目",
                "tables": [],
                "assets": [],
            },
            {
                "id": "file_generic_patent",
                "material_profile": {"material_group": "unknown", "material_types": [], "classification_source": "manual_override"},
                "original_name": "资质+代理证书.docx",
                "parsed_text": "该文件包含营业执照、专利、软件著作权、检测报告、合同等资料。",
                "tables": [],
                "assets": [],
            },
            {
                "id": "file_foundation_person",
                "material_profile": {"material_group": "personnel", "material_types": ["person_resume", "person_project_experience"], "classification_source": "manual_override"},
                "original_name": "人员项目经验.docx",
                "parsed_text": "人员简历项目经验：办公自动化系统开发。",
                "tables": [],
                "assets": [],
            },
            {
                "id": "file_2308aa547dd9",
                "material_profile": {"material_group": "company_qualification", "material_types": ["qualification_certificate"], "classification_source": "manual_override"},
                "original_name": "资质+代理证书.docx",
                "parsed_text": "ISO 9001 质量管理体系认证 ISO 27001 信息安全管理体系认证 ITSS 二级认证 CCRC信息系统安全集成三级服务资质 代理资质证明",
                "tables": [],
                "assets": [],
            },
            {
                "id": "file_a5eee523007a",
                "original_name": "资质+代理证书.docx",
                "parsed_text": "Mock Word解析文本：资质+代理证书.docx。包含营业执照、ISO9001质量管理体系认证、ISO27001信息安全管理体系认证、AAA信用等级、专利、软件著作权、检测报告、合同等资料。",
                "tables": [],
                "assets": [],
            },
        ]
    }


def sample_project() -> dict:
    tasks = [
        ("task_owner", "项目负责人情况"),
        ("task_team", "项目团队人员结构"),
        ("task_service", "总体服务方案"),
        ("task_progress", "实施计划与进度保障"),
        ("task_perf", "业绩情况"),
        ("task_patent", "专利数量"),
        ("task_supplier_perf", "绩效评价"),
        ("task_foundation", "工作基础"),
    ]
    return {
        "lot_name": "lot-v12",
        "project_name": "工业数据安全研究项目",
        "tender_file_id": "file_tender_001",
        "score_file_id": "file_score_001",
        "generation_tasks": [
            {"task_id": "task_business", "task_type": "business", "task_name": "商务响应"},
            {"task_id": "task_price", "task_type": "price", "task_name": "报价质量"},
            *[
                {
                    "task_id": task_id,
                    "task_type": "technical",
                    "task_name": task_name,
                    "related_score_item_ids": [f"score_{task_id}"],
                    "generation_instruction": f"生成{task_name}响应框架。",
                }
                for task_id, task_name in tasks
            ],
        ],
        "score_items": [
            {
                "id": f"score_{task_id}",
                "category": "technical",
                "item_name": task_name,
                "score_range": {"min": 0, "max": 5},
                "score_rule": f"根据{task_name}相关材料和响应内容评分。",
                "source_template_name": "评标办法-服务",
            }
            for task_id, task_name in tasks
        ],
        "material_analysis": {
            "summary": {"total_tasks": 7},
            "task_material_map": [
                {
                    "task_id": "task_owner",
                    "task_name": "项目负责人情况",
                    "task_type": "technical",
                    "score_range": {"min": 0, "max": 5},
                    "matched_materials": [
                        {
                            "material_type": "person_resume",
                            "fact_type": "resume_parse",
                            "file_id": "file_person_001",
                            "source": "resume_parse",
                            "title": "张三简历.docx",
                            "snippet": "姓名：张三；工作年限：8年；技能：Python；证书：PMP, Python；项目经验：办公自动化系统开发。",
                            "confidence": 0.9,
                        },
                        {
                            "material_type": "personal_certificate",
                            "fact_type": "resume_certificate",
                            "file_id": "file_person_001",
                            "source": "resume_parse.certificates",
                            "title": "Python证书",
                            "snippet": "姓名：张三；证书：Python认证。",
                            "confidence": 0.9,
                        },
                    ],
                    "missing_materials": [],
                },
                {
                    "task_id": "task_team",
                    "task_name": "项目团队人员结构",
                    "task_type": "technical",
                    "score_range": {"min": 0, "max": 5},
                    "matched_materials": [
                        {
                            "material_type": "person_resume",
                            "fact_type": "resume_parse",
                            "file_id": "file_person_002",
                            "source": "resume_parse",
                            "title": "李四简历.docx",
                            "snippet": "姓名：李四；工作年限：6年；技能：运维, 培训；项目经验：企业信息化项目。",
                            "confidence": 0.86,
                        }
                    ],
                    "missing_materials": [],
                },
                {
                    "task_id": "task_perf",
                    "task_name": "业绩情况",
                    "task_type": "technical",
                    "score_range": {"min": 0, "max": 5},
                    "matched_materials": [
                        {
                            "material_type": "project_case",
                            "fact_type": "person_project_experience",
                            "file_id": "file_person_001",
                            "source": "resume_parse.project_experience",
                            "title": "工业数据安全平台研发",
                            "snippet": "人员简历项目经验：工业数据安全平台研发。",
                            "confidence": 0.9,
                        }
                    ],
                    "missing_materials": [],
                },
                {
                    "task_id": "task_patent",
                    "task_name": "专利数量",
                    "task_type": "technical",
                    "score_range": {"min": 0, "max": 5},
                    "matched_materials": [
                        {
                            "material_type": "patent",
                            "fact_type": "patent_document",
                            "file_id": "file_generic_patent",
                            "source": "metadata.files[].parsed_text",
                            "title": "资质+代理证书.docx",
                            "snippet": "该文件包含营业执照、专利、软件著作权、检测报告、合同等资料。",
                            "confidence": 0.8,
                        }
                    ],
                    "missing_materials": [],
                },
                {
                    "task_id": "task_supplier_perf",
                    "task_name": "绩效评价",
                    "task_type": "technical",
                    "score_range": {"min": 0, "max": 5},
                    "matched_materials": [
                        {
                            "material_type": "project_case",
                            "fact_type": "project_case_document",
                            "file_id": "file_person_002",
                            "source": "resume_parse",
                            "title": "李四简历.docx",
                            "snippet": "人员简历项目经验：企业信息化项目。",
                            "confidence": 0.8,
                        }
                    ],
                    "missing_materials": [],
                },
                {
                    "task_id": "task_foundation",
                    "task_name": "工作基础",
                    "task_type": "technical",
                    "score_range": {"min": 0, "max": 5},
                    "matched_materials": [
                        {
                            "material_type": "patent",
                            "fact_type": "patent_document",
                            "file_id": "file_2308aa547dd9",
                            "source": "metadata.files[].parsed_text",
                            "title": "资质+代理证书.docx",
                            "snippet": "ISO 9001 质量管理体系认证 ISO 27001 信息安全管理体系认证 ITSS 二级认证 CCRC信息系统安全集成三级服务资质 代理资质证明",
                            "confidence": 0.9,
                        },
                        {
                            "material_type": "patent",
                            "fact_type": "patent_document",
                            "file_id": "file_a5eee523007a",
                            "source": "Mock Word解析文本",
                            "title": "资质+代理证书.docx",
                            "snippet": "Mock Word解析文本：资质+代理证书.docx。包含营业执照、ISO9001质量管理体系认证、ISO27001信息安全管理体系认证、AAA信用等级、专利、软件著作权、检测报告、合同等资料。",
                            "confidence": 0.8,
                        },
                        {
                            "material_type": "project_case",
                            "fact_type": "person_project_experience",
                            "file_id": "file_foundation_person",
                            "source": "resume_parse.project_experience",
                            "title": "人员项目经验",
                            "snippet": "人员简历项目经验：办公自动化系统开发。",
                            "confidence": 0.8,
                        },
                    ],
                    "missing_materials": [],
                },
            ],
        },
        "response_outline": {"version": "v1"},
        "markdown_drafts": {"summary": {"total_files": 6}},
        "writing_plan": {"summary": {"total_tasks": 7}},
        "agent_drafts": {"summary": {"total_sections": 7}},
    }


def read_output(result: dict, kind: str) -> str:
    item = next(file for file in result["files"] if file["kind"] == kind)
    return Path(item["path"]).read_text(encoding="utf-8")


def debug_sections(result: dict) -> list[dict]:
    return json.loads(read_output(result, "outline_debug"))["sections"]


def section_by_title(result: dict, title: str) -> dict:
    return next(item for item in debug_sections(result) if item["section_title"] == title)


def test_cover_and_toc_cannot_be_valid_tender_basis():
    clauses = build_clause_index("file_spec", "技术规范书.docx", sample_metadata()["files"][1]["parsed_text"])
    invalid = [item for item in clauses if "特别声明" in item["clause_text"] or "项目招标文件模板" in item["clause_text"] or "目 录" in item["clause_text"]]

    assert invalid
    assert all(item["evidence_quality"] != "candidate_clause" for item in invalid)


def test_disclaimer_clause_not_confirmed_for_service_plan(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    service = section_by_title(result, "总体服务方案")
    ref = service["tender_requirement_ref"]

    assert "特别声明" not in ref["clause_path"]
    assert ref["evidence_quality"] == "confirmed_clause"
    assert "服务方案应覆盖总体思路" in ref["source_snippet"]


def test_project_owner_uses_aggregated_person_candidate(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    owner = section_by_title(result, "项目负责人情况")
    text = read_output(result, "technical_response_outline")

    assert len(owner["person_candidates"]) == 1
    assert owner["person_candidates"][0]["name"] == "张三"
    assert "候选人员表" in text
    assert owner["evidence_counts"]["confirmed"] == 0


def test_person_candidate_has_match_reason_and_relevance(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    owner = section_by_title(result, "项目负责人情况")
    candidate = owner["person_candidates"][0]

    assert candidate["match_reason"]
    assert candidate["relevance_to_project"] == "candidate_needs_review"
    assert "Python" in candidate["match_reason"]


def test_company_performance_does_not_confirm_resume_project_experience(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    perf = section_by_title(result, "业绩与项目经验")

    assert perf["evidence_counts"]["confirmed"] == 0
    assert perf["evidence_counts"]["weak"] >= 1


def test_company_performance_missing_when_no_contract(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    perf = section_by_title(result, "业绩与项目经验")
    missing_types = {item["material_type"] for item in perf["missing_materials"]}

    assert "company_contract" in missing_types
    assert "acceptance_report" in missing_types
    assert result["summary"]["company_performance_missing"] >= 1


def test_company_performance_markdown_does_not_show_resume_project_experience(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")
    section = text.split("## 5. 业绩与项目经验", 1)[1].split("## 6.", 1)[0]

    assert "人员简历项目经验" not in section
    assert "工业数据安全平台研发" not in section


def test_patent_generic_text_is_not_confirmed(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    patent = section_by_title(result, "专利 / 知识产权 / 创新成果")

    assert patent["evidence_counts"]["confirmed"] == 0
    assert patent["evidence_counts"]["weak"] >= 1


def test_patent_missing_without_identifier(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    patent = section_by_title(result, "专利 / 知识产权 / 创新成果")
    missing_types = {item["material_type"] for item in patent["missing_materials"]}

    assert "patent_certificate" in missing_types
    assert result["summary"]["patent_missing"] == 1


def test_supplier_performance_rejects_resume_and_qualification_materials(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    perf = section_by_title(result, "绩效评价材料")

    assert perf["evidence_counts"]["confirmed"] == 0
    assert perf["evidence_counts"]["unusable"] >= 1


def test_supplier_performance_missing_without_proof(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    perf = section_by_title(result, "绩效评价材料")
    missing_types = {item["material_type"] for item in perf["missing_materials"]}

    assert "supplier_performance_evaluation" in missing_types
    assert result["summary"]["supplier_performance_missing"] == 1


def test_qualification_file_is_reclassified_as_company_qualification(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    foundation = section_by_title(result, "工作基础与既有能力")
    all_items = [item for values in foundation["material_basis"]["groups"].values() for item in values]
    qual = next(item for item in all_items if item.get("file_id") == "file_2308aa547dd9")
    text = read_output(result, "technical_response_outline")

    assert qual["material_type"] == "company_qualification"
    assert qual["fact_type"] == "qualification_certificate"
    assert qual["evidence_strength"] == "unusable"
    assert "ISO 9001质量管理体系认证证书" not in text
    assert "CCRC信息系统安全运维三级服务资质" not in text


def test_stale_mock_summary_is_unusable_and_not_patent_or_project_case(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    foundation = section_by_title(result, "工作基础与既有能力")
    all_items = [item for values in foundation["material_basis"]["groups"].values() for item in values]
    stale = next(item for item in all_items if item.get("file_id") == "file_a5eee523007a")

    assert stale["fact_type"] == "stale_or_mock_summary"
    assert stale["evidence_strength"] == "unusable"
    assert stale["material_type"] == "unusable_summary"


def test_stale_mock_summary_only_in_debug_not_markdown(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")

    assert "file_a5eee523007a" not in text
    assert "stale_or_mock_summary" not in text


def test_service_plan_without_enterprise_material_gets_writing_note(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")

    assert "本章节主要依据评分点和已定位原文要求形成方案框架" in text
    assert "三栏全无" not in text


def test_clause_path_removes_toc_page_numbers(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")

    assert "1. 投标须知 1" not in text
    assert "1.3对投标人的技术要求 2" not in text
    assert "1.3 对投标人的技术要求 2" not in text


def test_team_interpretation_mentions_structure_resume_and_roles(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    team = section_by_title(result, "项目组织与团队配置")

    assert "团队成员" in team["tender_requirement_ref"]["requirement_interpretation"]
    assert "分工" in team["tender_requirement_ref"]["requirement_interpretation"]


def test_owner_interpretation_not_generic_service_plan_text(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    owner = section_by_title(result, "项目负责人情况")

    assert "围绕技术要求、实施方案、质量进度与安全保障组织响应" not in owner["tender_requirement_ref"]["requirement_interpretation"]
    assert "项目负责人" in owner["tender_requirement_ref"]["requirement_interpretation"]


def test_foundation_markdown_does_not_show_person_project_experience(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")
    section = text.split("## 8. 工作基础与既有能力", 1)[1]

    assert "file_foundation_person" not in section
    assert "办公自动化系统开发" not in section


def test_markdown_has_compliance_response_conditions(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")

    assert "### 2. 原文响应要求与证据定位" in text
    assert "## 技术文件格式与表单要求" in text


def test_v15_project_name_does_not_use_technical_spec_filename(tmp_path):
    metadata = sample_metadata()
    metadata["files"][1]["original_name"] = "技术规范书(基于区块链的工业数据管理技术研究).docx"
    project = sample_project()
    project["project_name"] = ""

    result = build_response_outline(project, metadata=metadata, output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")
    debug = json.loads(read_output(result, "outline_debug"))

    assert "项目名称：未定位到项目总名称，需从招标公告或招标文件正文确认" in text
    assert "标段/包号：lot-v12" in text
    assert "包/技术专题名称：基于区块链的工业数据管理技术研究" in text
    assert "名称来源：技术规范书文件名" not in text
    assert debug["project_name"] == "未定位到项目总名称，需从招标公告或招标文件正文确认"


def test_v15_response_outline_reads_tender_project_metadata(tmp_path):
    project = sample_project()
    project["project_name"] = ""
    project["tender_project_metadata"] = {
        "version": "v0",
        "fields": {
            "tender_no": {"value": "SG-2026-001", "source_type": "full_tender_document"},
            "project_name": {"value": "国网工业数据治理服务采购项目", "source_type": "full_tender_document"},
            "lot_no": {"value": "标1", "source_type": "full_tender_document"},
            "lot_name": {"value": "数字化服务", "source_type": "full_tender_document"},
            "package_no": {"value": "包1", "source_type": "procurement_package_table"},
            "package_name": {"value": "工业数据管理", "source_type": "procurement_package_table"},
            "technical_topic_name": {"value": "基于区块链的工业数据管理技术研究", "source_type": "technical_spec"},
            "tenderee": {"value": "某电力公司", "source_type": "full_tender_document"},
            "tender_agency": {"value": "某招标代理公司", "source_type": "full_tender_document"},
        },
        "manual_confirm_items": ["包号：需人工确认"],
    }

    result = build_response_outline(project, metadata=sample_metadata(), output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")

    assert "生成类型：Markdown Outline V1.5" in text
    assert "招标编号：SG-2026-001" in text
    assert "项目名称：国网工业数据治理服务采购项目" in text
    assert "包/技术专题名称：基于区块链的工业数据管理技术研究" in text
    assert "元信息待确认项：包号：需人工确认" in text


def test_v14_summary_and_quality_are_chinese_business_terms(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")

    assert "原文响应要求数量" in text
    assert "可直接引用原文要求" in text
    assert "需复核原文要求" in text
    assert "被过滤系统/商务/流程条款数量" in text
    assert "仅评分点驱动章节数量" in text
    assert "证据质量：可直接引用" in text or "证据质量：标题级依据，需下钻" in text
    for status in ["confirmed_clause", "weak_clause", "heading_only", "not_found", "invalid_clause", "weak/heading_only"]:
        assert status not in text


def test_v14_compliance_does_not_repeat_acceptance_clause_for_all_sections(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")

    assert text.count("6.1 测试验收基本要求") <= 1
    owner_section = text.split("## 1. 项目负责人情况", 1)[1].split("\n## 2. ", 1)[0]
    assert "未定位到本章专属原文响应要求，按评分依据和材料缺口组织本章。" in owner_section
    assert "6.1 测试验收基本要求" not in owner_section


def test_v14_writing_goals_are_not_repeated_template_sentences(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")

    assert "生成“项目负责人情况”评分响应内容，覆盖评分规则、证明材料、页码索引和响应表达。" not in text
    assert "写明项目负责人专业背景、工作年限、工作经验" in text
    assert "说明团队结构、专业分工、人员能力覆盖" in text


def test_v14_excerpt_removes_toc_page_numbers_and_template_cover(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")

    assert "1.3 对投标人的技术要求 2" not in text
    assert "1.3对投标人的技术要求 2" not in text
    assert "附件3：项目招标文件模板" not in text
    assert "目 录" not in text


def test_outputs_have_no_decision_language(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    text = "\n".join(read_output(result, item["kind"]) for item in result["files"])

    for term in FORBIDDEN_DECISION_TERMS:
        assert term.lower() not in text.lower()


def test_response_outline_index_writeback_preserves_existing_structures(tmp_path):
    project = sample_project()
    result = build_response_outline(project, metadata=sample_metadata(), output_dir=tmp_path)
    path = tmp_path / "tender_projects.json"
    original_material_analysis = deepcopy(project["material_analysis"])
    original_markdown_drafts = deepcopy(project["markdown_drafts"])
    original_writing_plan = deepcopy(project["writing_plan"])
    original_agent_drafts = deepcopy(project["agent_drafts"])
    path.write_text(json.dumps({"projects": [project]}, ensure_ascii=False), encoding="utf-8")

    index = save_response_outline_index("lot-v12", result, path=path)
    saved = json.loads(path.read_text(encoding="utf-8"))["projects"][0]

    assert index["version"] == "v1.5"
    assert saved["response_outline"]["files"]
    assert saved["material_analysis"] == original_material_analysis
    assert saved["markdown_drafts"] == original_markdown_drafts
    assert saved["writing_plan"] == original_writing_plan
    assert saved["agent_drafts"] == original_agent_drafts


def regression_project(task_names: list[str]) -> dict:
    return {
        "lot_name": "0425139003001-6582",
        "project_name": "国网河北电力2025年第三次服务类公开招标采购",
        "tender_file_id": "file_tender",
        "generation_tasks": [
            {
                "task_id": f"task_{index}",
                "task_type": "technical",
                "task_name": task_name,
                "related_score_item_ids": [f"score_{index}"],
            }
            for index, task_name in enumerate(task_names, start=1)
        ],
        "score_items": [
            {
                "id": f"score_{index}",
                "category": "technical",
                "item_name": task_name,
                "score_rule": f"{task_name}评分点依据。",
                "source_template_name": "评标办法-服务",
            }
            for index, task_name in enumerate(task_names, start=1)
        ],
        "material_analysis": {"task_material_map": []},
    }


def regression_metadata(parsed_text: str) -> dict:
    return {
        "files": [
            {
                "id": "file_tender",
                "original_name": "招标文件-服务.docx",
                "category": "招标文件",
                "tags": ["full_tender_document"],
                "parsed_text": parsed_text,
                "tables": [],
                "assets": [],
            }
        ]
    }


def test_business_identity_change_not_used_for_technical_sections(tmp_path):
    tasks = [
        "应答文件技术部分总体评价",
        "项目负责人情况",
        "项目组织与团队配置",
        "工作基础",
        "专利数量",
        "绩效评价",
    ]
    text = "\n".join(
        [
            "第六章 投标文件格式",
            "八、特别约定：",
            "10. 企业名称变更",
            "如企业名称变更，应提供企业名称变更证明材料，并在响应文件中说明。",
        ]
    )

    result = build_response_outline(regression_project(tasks), metadata=regression_metadata(text), output_dir=tmp_path)
    markdown = read_output(result, "technical_response_outline")
    appendix_c = markdown.split("## 附录 C：招标正文依据未定位章节", 1)[1]

    assert "企业名称变更" not in markdown.split("## 附录 C：", 1)[0]
    assert "未定位到本章节专属招标/技术条款" in markdown
    assert "所有章节均已定位到候选正文条款" not in appendix_c


def test_business_license_not_personnel_or_team_compliance(tmp_path):
    text = "\n".join(
        [
            "第六章 投标文件格式",
            "1. 企业营业执照",
            "企业营业执照（或事业单位法人证书或其他组织登记证书）（扫描件）。",
        ]
    )

    result = build_response_outline(regression_project(["项目负责人情况", "项目组织与团队配置"]), metadata=regression_metadata(text), output_dir=tmp_path)
    markdown = read_output(result, "technical_response_outline")
    owner_section = markdown.split("## 1. 项目负责人情况", 1)[1].split("\n## 2. ", 1)[0]
    team_section = markdown.split("## 2. 项目组织与团队配置", 1)[1].split("## 附录", 1)[0]

    assert "企业营业执照" not in owner_section
    assert "企业营业执照" not in team_section
    assert "未定位到本章专属原文响应要求，按评分依据和材料缺口组织本章。" in owner_section
    assert "未定位到本章专属原文响应要求，按评分依据和材料缺口组织本章。" in team_section


def test_non_current_construction_demand_table_not_used_for_patent_section(tmp_path):
    text = "=== Sheet: 施工18个 === 2025年第3批输变电工程施工招标计划表 0425139001002-2004 专利证书 发明专利 实用新型"
    project = regression_project(["专利数量"])
    project["score_items"][0]["score_rule"] = "提供采购相关内容的发明专利或实用新型专利，每提供1项加1分，上限不超过2分。"

    result = build_response_outline(project, metadata=regression_metadata(text), output_dir=tmp_path)
    markdown = read_output(result, "technical_response_outline")
    patent_section = markdown.split("## 1. 专利 / 知识产权 / 创新成果", 1)[1].split("## 附录", 1)[0]

    assert "施工18个" not in patent_section
    assert "0425139001002-2004" not in patent_section
    assert "未定位到本章专属原文响应要求，按评分依据和材料缺口组织本章。" in patent_section
    assert "提供采购相关内容的发明专利或实用新型专利" in patent_section


def test_filtered_candidates_are_written_with_reasons(tmp_path):
    text = "\n".join(
        [
            "第六章 投标文件格式",
            "八、特别约定：",
            "10. 企业名称变更",
            "如企业名称变更，应提供企业名称变更证明材料，并在响应文件中说明。",
        ]
    )

    text = text + "\n项目负责人证明材料名称如发生企业名称变更，应提供企业名称变更证明。"

    result = build_response_outline(regression_project(["项目负责人情况"]), metadata=regression_metadata(text), output_dir=tmp_path)
    filtered_entry = next(item for item in result["files"] if item["kind"] == "filtered_clause_candidates")
    rows = json.loads(Path(filtered_entry["path"]).read_text(encoding="utf-8"))
    markdown = read_output(result, "technical_response_outline")

    assert rows
    assert rows[0]["filter_reason"]
    assert rows[0]["clause_type"] == "identity_change"
    assert rows[0]["recoverable"] is True
    assert "未定位到本章节专属人员条款" in markdown


def test_correct_technical_spec_clause_is_not_filtered(tmp_path):
    metadata = regression_metadata(
        "\n".join(
            [
                "第五章 技术标准和要求",
                "1. 服务方案",
                "投标人应提供服务方案、实施方案、质量保证、技术培训和验收测试安排。",
            ]
        )
    )

    result = build_response_outline(regression_project(["服务方案与实施方案"]), metadata=metadata, output_dir=tmp_path)
    markdown = read_output(result, "technical_response_outline")

    assert "投标人应提供服务方案" in markdown
    assert "未定位到本章节专属招标/技术条款" not in markdown.split("## 1. 总体服务方案", 1)[1].split("### 2.", 1)[0]


def test_sections_use_new_information_architecture(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")
    first_section = text.split("## 1. 项目负责人情况", 1)[1].split("\n## 2. ", 1)[0]

    assert "### 1. 评分依据" in first_section
    assert "### 2. 原文响应要求与证据定位" in first_section
    assert "### 3. 评分项行动分析 / 材料准备清单" in first_section
    assert "### 4. 本章响应任务" in first_section
    assert "### 5. 建议写作结构" in first_section
    assert "### 6. 知识库材料 / 候选材料 / 缺失材料" in first_section
    assert "### 7. 待补充 / 待人工确认" in first_section
    assert "### 1. 招标/技术条款依据" not in text
    assert "### 3. 合规与响应条件" not in text


def test_response_outline_writes_scoring_action_plan_files(tmp_path):
    result = build_response_outline(sample_project(), metadata=sample_metadata(), output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")
    file_by_kind = {item["kind"]: item for item in result["files"]}

    assert "### 3. 评分项行动分析 / 材料准备清单" in text
    assert "scoring_action_plan_json" in file_by_kind
    assert "scoring_action_plan_markdown" in file_by_kind
    assert Path(file_by_kind["scoring_action_plan_json"]["path"]).exists()
    assert Path(file_by_kind["scoring_action_plan_markdown"]["path"]).exists()


def test_response_requirement_refs_include_personnel_form_requirement(tmp_path):
    text = "\n".join(
        [
            "第六章 投标文件格式",
            "三、技术文件",
            "2. 项目团队情况",
            "表1 拟委任的主要人员汇总表 表2 主要人员简历表",
        ]
    )

    result = build_response_outline(regression_project(["项目负责人情况", "项目组织与团队配置"]), metadata=regression_metadata(text), output_dir=tmp_path)
    debug = json.loads(read_output(result, "outline_debug"))
    owner = next(item for item in debug["sections"] if item["section_title"] == "项目负责人情况")
    markdown = read_output(result, "technical_response_outline")
    owner_section = markdown.split("## 1. 项目负责人情况", 1)[1].split("\n## 2. ", 1)[0]
    global_format_section = markdown.split("## 技术文件格式与表单要求", 1)[1].split("\n## 1. ", 1)[0]

    assert any(ref["requirement_type"] == "format_form" for ref in owner["response_requirement_refs"])
    assert "#### 2.4 格式 / 表单要求" not in owner_section
    assert "拟委任的主要人员汇总表" in global_format_section
    assert owner_section.count("拟委任的主要人员汇总表") == 0
    assert "未定位到本章专属原文响应要求，按评分依据和材料缺口组织本章。" in owner_section


def test_operation_only_clause_not_in_main_markdown(tmp_path):
    text = "\n".join(
        [
            "第二章 投标人须知",
            "5.1 开标时间和地点",
            "技术部分投标文件通过电子招标投标交易平台递交，使用CA证书和投标工具完成平台解密。",
        ]
    )

    result = build_response_outline(regression_project(["应答文件技术部分总体评价"]), metadata=regression_metadata(text), output_dir=tmp_path)
    markdown = read_output(result, "technical_response_outline")
    filtered_rows = json.loads(read_output(result, "filtered_clause_candidates"))

    assert "开标时间和地点" not in markdown
    assert "电子招标投标交易平台" not in markdown
    assert any(row.get("filter_reason") == "operation_only_not_applicable_to_technical_section" for row in filtered_rows)


def test_package_constraints_render_for_performance_and_schedule(tmp_path):
    project = regression_project(["业绩情况", "实施计划与进度保障"])
    project["tender_project_metadata"] = {
        "fields": {
            "special_qualification_requirement": {"value": "近5年，投标人具有技术开发或技术服务业绩", "source_file_id": "file_demand", "source_file_name": "需求一览表.xlsx", "source_type": "procurement_package_table"},
            "service_period": {"value": "开工日期20250731,竣工日期20251231", "source_file_id": "file_demand", "source_file_name": "需求一览表.xlsx", "source_type": "procurement_package_table"},
        }
    }

    result = build_response_outline(project, metadata=regression_metadata("第五章 技术标准和要求\n1. 服务方案\n投标方负责项目计划、进度跟踪和验收配合。"), output_dir=tmp_path)
    markdown = read_output(result, "technical_response_outline")
    performance_section = markdown.split("## 1. 业绩与项目经验", 1)[1].split("\n## 2. ", 1)[0]
    schedule_section = markdown.split("## 2. 实施计划与进度保障", 1)[1].split("## 附录", 1)[0]

    assert "专用资格要求：近5年，投标人具有技术开发或技术服务业绩" in performance_section
    assert "服务期限：开工日期20250731,竣工日期20251231" in schedule_section


def test_project_key_constraints_render_at_top(tmp_path):
    project = regression_project(["业绩情况"])
    project["tender_project_metadata"] = {
        "fields": {
            "service_period": {"value": "开工日期20250731,竣工日期20251231", "source_type": "procurement_package_table"},
            "max_price": {"value": "50", "source_type": "procurement_package_table"},
            "special_qualification_requirement": {"value": "近5年，投标人具有技术开发或技术服务业绩", "source_type": "procurement_package_table"},
        }
    }

    result = build_response_outline(project, metadata=regression_metadata("无匹配正文。"), output_dir=tmp_path)
    markdown = read_output(result, "technical_response_outline")
    top = markdown.split("## 技术文件格式与表单要求", 1)[0]

    assert "## 项目关键约束" in top
    assert "服务期限：开工日期20250731，竣工日期20251231" in top
    assert "最高限价：50万元" in top
    assert "专用资格要求：近5年，投标人具有技术开发或技术服务业绩" in top


def test_main_markdown_hides_empty_requirement_subsections(tmp_path):
    result = build_response_outline(regression_project(["绩效评价"]), metadata=regression_metadata("无匹配正文。"), output_dir=tmp_path)
    markdown = read_output(result, "technical_response_outline")
    section = markdown.split("## 1. 绩效评价材料", 1)[1].split("## 附录", 1)[0]

    assert "未定位到本章专属原文响应要求，按评分依据和材料缺口组织本章。" in section
    for phrase in [
        "未定位到本章适用参数/指标原文要求",
        "未定位到本章适用人员/证书/资格原文要求",
        "未定位到本章适用证明材料原文要求",
        "未定位到本章适用包级约束",
    ]:
        assert phrase not in markdown


def test_business_and_price_commitments_not_in_technical_main_markdown(tmp_path):
    text = "\n".join(
        [
            "第六章 投标文件格式",
            "一、价格文件",
            "5. 如我方中标",
            "如我方中标，我方承诺按要求提交履约担保并支付中标代理服务费。",
            "二、商务文件",
            "（三）补充文件（上传投标工具路径：商务文件-选择分标-按钮“编辑”-“补充文件批量管理”-按钮“文件上传”）",
            "14. 服务类供应商“重质量、讲诚信”承诺书",
            "服务类供应商应提交“重质量、讲诚信”承诺书。",
            "三、技术文件",
            "1. 业绩文件",
            "近年完成的类似项目情况表。",
            "第二章 投标人须知",
            "3.5.3 近年完成的类似项目情况要求",
            "投标人应提供近年完成的类似项目情况表。",
        ]
    )
    project = regression_project(["项目负责人情况", "项目组织与团队配置", "业绩情况"])
    project["tender_project_metadata"] = {
        "fields": {
            "special_qualification_requirement": {"value": "近5年，投标人具有技术开发或技术服务业绩", "source_type": "procurement_package_table"}
        }
    }

    result = build_response_outline(project, metadata=regression_metadata(text), output_dir=tmp_path)
    markdown = read_output(result, "technical_response_outline")
    filtered_rows = json.loads(read_output(result, "filtered_clause_candidates"))
    owner_section = markdown.split("## 1. 项目负责人情况", 1)[1].split("\n## 2. ", 1)[0]
    team_section = markdown.split("## 2. 项目组织与团队配置", 1)[1].split("\n## 3. ", 1)[0]
    performance_section = markdown.split("## 3. 业绩与项目经验", 1)[1].split("## 附录", 1)[0]
    global_format_section = markdown.split("## 技术文件格式与表单要求", 1)[1].split("\n## 1. ", 1)[0]

    assert "服务类供应商" not in owner_section
    assert "重质量" not in owner_section
    assert "服务类供应商" not in team_section
    assert "如我方中标" not in performance_section
    assert "履约担保" not in performance_section
    assert "近年完成的类似项目情况表" in global_format_section
    assert "3.5.3 近年完成的类似项目情况要求" in performance_section
    assert "专用资格要求：近5年，投标人具有技术开发或技术服务业绩" in performance_section
    assert any(row.get("filter_reason") == "business_quality_commitment_not_applicable_to_technical_section" for row in filtered_rows)
    assert any(row.get("filter_reason") == "price_file_or_bid_letter_commitment_not_applicable_to_technical_section" for row in filtered_rows)


def test_patent_and_supplier_sections_keep_score_and_material_requirements(tmp_path):
    project = regression_project(["专利数量", "绩效评价"])
    project["score_items"][0]["score_rule"] = "提供采购相关内容的发明专利或实用新型专利，每提供1项加1分，上限不超过2分。"
    project["score_items"][1]["score_rule"] = "根据最近一年度供应商绩效评价结果进行评分。"
    text = "\n".join(
        [
            "第五章 技术标准和要求",
            "6.2 测试验收内容",
            "提交技术报告。受理发明专利1项。形成国际标准初稿1份。",
        ]
    )

    result = build_response_outline(project, metadata=regression_metadata(text), output_dir=tmp_path)
    markdown = read_output(result, "technical_response_outline")
    patent_section = markdown.split("## 1. 专利 / 知识产权 / 创新成果", 1)[1].split("\n## 2. ", 1)[0]
    supplier_section = markdown.split("## 2. 绩效评价材料", 1)[1].split("## 附录", 1)[0]

    assert "受理发明专利1项" in patent_section
    assert "提供采购相关内容的发明专利或实用新型专利" in patent_section
    assert "根据最近一年度供应商绩效评价结果进行评分" in supplier_section
    assert "平台绩效评价截图、招标人评价结果或供应商绩效证明" in supplier_section
