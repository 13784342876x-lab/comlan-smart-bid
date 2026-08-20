import json

from app.material_matcher import analyze_project_materials


def base_project(tasks):
    return {
        "lot_name": "test-lot",
        "tender_file_id": "tender_file",
        "score_file_id": "score_file",
        "score_items": [
            {
                "id": task["related_score_item_ids"][0],
                "item_name": task.get("name", task["related_score_item_ids"][0]),
                "score_range": task.get("score_range", {"min": 0, "max": 5}),
                "score_rule": task.get("score_rule", ""),
            }
            for task in tasks
        ],
        "generation_tasks": [
            {
                "task_id": task["task_id"],
                "target_section": task.get("target_section", "商务响应表"),
                "task_type": task.get("task_type", "business"),
                "related_score_item_ids": task["related_score_item_ids"],
                "related_requirements": task.get("related_requirements", [task.get("score_rule", "")]),
                "evidence_needed": task.get("evidence_needed", []),
                "kb_mapping_hint": task.get("kb_mapping_hint", []),
                "generation_instruction": task.get("generation_instruction", ""),
                "status": "pending",
            }
            for task in tasks
        ],
    }


def metadata_fixture():
    return {
        "files": [
            {
                "id": "resume_001",
                "original_name": "张三-项目负责人简历.docx",
                "category": "人员简历库",
                "resume_parse": {
                    "person_name": "张三",
                    "work_years": 8,
                    "skills": [
                        {"name": "Python", "years": "5年"},
                        {"name": "VMware", "years": "4年"},
                    ],
                    "certificates": [
                        {
                            "canonical_id": "alibaba_cloud_acp",
                            "display_name": "Alibaba Cloud ACP",
                            "standard_name": "Alibaba Cloud Certified Professional",
                            "family": "alibaba_cloud",
                            "persons": ["张三"],
                            "confidence": 0.9,
                        },
                        {
                            "canonical_id": "vmware_vcp",
                            "display_name": "VCP-DCV",
                            "standard_name": "VMware Certified Professional",
                            "family": "vmware",
                            "persons": ["张三"],
                            "confidence": 0.9,
                        },
                    ],
                    "projects": [
                        {
                            "name": "数据平台建设项目",
                            "description": "负责数据治理平台实施和验收支持。",
                        }
                    ],
                },
                "assets": [
                    {
                        "id": "asset_acp",
                        "ocr_text": "Alibaba Cloud Certified Professional ACP issued to Zhang San",
                        "ocr_confidence": 0.88,
                    }
                ],
            },
            {
                "id": "qual_001",
                "original_name": "CMMI-DCMM企业资质证书.pdf",
                "category": "企业资质库",
                "company_qualification": {
                    "requirements": [
                        {"name": "CMMI", "level": 3, "or_above": True},
                        {"name": "DCMM", "level": 2},
                    ],
                    "source_file_id": "qual_001",
                    "original_name": "CMMI-DCMM企业资质证书.pdf",
                },
                "parsed_text": "公司具备 CMMI 3级及以上能力成熟度，具备 DCMM 二级能力。",
                "assets": [],
            },
            {
                "id": "patent_001",
                "original_name": "发明专利证书.pdf",
                "category": "企业资质库",
                "parsed_text": "发明专利证书，名称：数据处理方法及系统。",
                "assets": [],
            },
        ],
        "company_qualifications": {
            "qual_001": {
                "requirements": [
                    {"name": "CMMI", "level": 3, "or_above": True, "source_file_id": "qual_001"},
                    {"name": "DCMM", "level": 2, "source_file_id": "qual_001"},
                ],
                "original_name": "CMMI-DCMM企业资质证书.pdf",
            }
        },
        "resumes": {},
    }


def first_task(result):
    return result["task_material_map"][0]


def test_personnel_task_matches_resume_parse():
    task = {
        "task_id": "task_001_personnel",
        "related_score_item_ids": ["score_personnel"],
        "name": "项目负责人情况",
        "score_rule": "对项目负责人工作年限、工作经验和技能进行评审。",
        "evidence_needed": ["项目负责人简历", "资格证书"],
    }
    result = analyze_project_materials(base_project([task]), metadata_fixture())

    item = first_task(result)
    assert item["match_status"] in {"matched", "partial"}
    assert any(material["material_type"] == "person_resume" for material in item["matched_materials"])


def test_personal_capability_task_does_not_match_company_qualification():
    task = {
        "task_id": "task_002_personal_cert",
        "related_score_item_ids": ["score_personal_cert"],
        "name": "个人能力证书",
        "score_rule": "团队人员具备 ACP、Python、VMware 等个人能力。",
        "evidence_needed": ["ACP", "Python", "VMware"],
    }
    result = analyze_project_materials(base_project([task]), metadata_fixture())
    item = first_task(result)
    material_types = {material["material_type"] for material in item["matched_materials"]}

    assert material_types & {"personal_certificate", "person_resume"}
    assert "company_qualification" not in material_types


def test_company_qualification_task_does_not_match_personal_certificate():
    task = {
        "task_id": "task_003_company_qualification",
        "related_score_item_ids": ["score_company_qualification"],
        "name": "企业资质",
        "score_rule": "投标人具备 CMMI 3级及以上或 DCMM 二级企业资质。",
        "evidence_needed": ["CMMI", "DCMM"],
    }
    result = analyze_project_materials(base_project([task]), metadata_fixture())
    item = first_task(result)
    material_types = {material["material_type"] for material in item["matched_materials"]}

    assert "company_qualification" in material_types
    assert "personal_certificate" not in material_types


def test_writing_task_is_not_material_missing():
    task = {
        "task_id": "task_004_writing",
        "related_score_item_ids": ["score_writing"],
        "name": "服务方案",
        "task_type": "technical",
        "score_rule": "从服务方案和工作进度及保证措施进行综合评价。",
        "evidence_needed": ["服务方案", "进度计划"],
    }
    result = analyze_project_materials(base_project([task]), metadata_fixture())
    item = first_task(result)

    assert item["match_status"] == "writing_required"
    assert item["missing_materials"] == []


def test_missing_materials_when_no_evidence_found():
    task = {
        "task_id": "task_005_missing",
        "related_score_item_ids": ["score_missing"],
        "name": "ITSS企业资质",
        "score_rule": "投标人具备 ITSS 企业资质证书。",
        "evidence_needed": ["ITSS"],
    }
    result = analyze_project_materials(base_project([task]), metadata_fixture())
    item = first_task(result)

    assert item["match_status"] == "missing"
    assert item["missing_materials"]


def test_twenty_four_generation_tasks_all_get_material_map_records():
    tasks = [
        {
            "task_id": f"task_{index:03d}",
            "related_score_item_ids": [f"score_{index:03d}"],
            "name": f"评分项{index}",
            "score_rule": "项目负责人简历和技能材料。" if index % 2 else "专利数量和知识产权证明。",
            "evidence_needed": ["项目负责人简历"] if index % 2 else ["专利证书"],
        }
        for index in range(1, 25)
    ]
    result = analyze_project_materials(base_project(tasks), metadata_fixture())

    assert result["summary"]["total_tasks"] == 24
    assert len(result["task_material_map"]) == 24


def test_output_has_no_auto_bid_decision_language():
    task = {
        "task_id": "task_006_safe_language",
        "related_score_item_ids": ["score_safe_language"],
        "name": "项目负责人情况",
        "score_rule": "对项目负责人情况进行评审。",
        "evidence_needed": ["项目负责人简历"],
    }
    result_text = json.dumps(analyze_project_materials(base_project([task]), metadata_fixture()), ensure_ascii=False)

    forbidden = ["能投", "不能投", "建议投标", "不建议投标"]
    assert not any(term in result_text for term in forbidden)
