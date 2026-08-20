import json

from app.writing_orchestrator import build_writing_plan


def make_task(task_id, name, task_type="business"):
    return {
        "task_id": task_id,
        "target_section": "商务响应表" if task_type == "business" else "技术响应",
        "task_type": task_type,
        "related_score_item_ids": [name],
        "related_requirements": [f"{name}评分要求"],
        "evidence_needed": ["证明材料"],
        "kb_mapping_hint": [],
        "generation_instruction": f"生成{name}响应骨架。",
    }


def material_item(task_id, name, task_type, status, materials=None, missing=None):
    return {
        "task_id": task_id,
        "task_name": name,
        "task_type": task_type,
        "score_range": {"min": 0, "max": 5},
        "required_material_types": [],
        "matched_materials": materials or [],
        "missing_materials": missing or [],
        "match_status": status,
        "confidence": 0.8 if materials else 0.0,
        "notes": [],
    }


def evidence(material_type="person_resume", title="张三简历.docx"):
    return {
        "material_type": material_type,
        "fact_type": "resume_parse" if material_type == "person_resume" else "company_qualification",
        "file_id": "file_resume_001" if material_type == "person_resume" else "file_qual_001",
        "source": "metadata.files[].resume_parse" if material_type == "person_resume" else "metadata.company_qualifications",
        "title": title,
        "person": "张三" if material_type == "person_resume" else None,
        "snippet": "姓名：张三；工作年限：8年；证书：ACP。",
        "confidence": 0.9,
    }


def project_fixture(tasks, material_items):
    return {
        "lot_name": "test-lot",
        "generation_tasks": tasks,
        "material_analysis": {
            "summary": {},
            "task_material_map": material_items,
            "matched_materials": [],
            "missing_materials": [],
        },
    }


def test_matched_personnel_task_generates_evidence_backed_section():
    task = make_task("task_001", "项目负责人情况", "technical")
    project = project_fixture(
        [task],
        [material_item("task_001", "项目负责人情况", "technical", "matched", [evidence("person_resume")])],
    )

    result = build_writing_plan(project)
    section = result["draft_sections"][0]

    assert section["section_type"] == "evidence_backed"
    assert section["writer_agent"] == "technical_writer"
    assert section["evidence_refs"]
    assert "人员基本情况" in result["draft_plan"][0]["outline"]


def test_writing_required_task_generates_outline_without_missing():
    task = make_task("task_002", "服务方案", "technical")
    project = project_fixture(
        [task],
        [material_item("task_002", "服务方案", "technical", "writing_required")],
    )

    result = build_writing_plan(project)
    section = result["draft_sections"][0]

    assert section["section_type"] == "writing_required"
    assert section["status"] == "draft_skeleton"
    assert section["missing_materials"] == []
    assert "服务目标理解" in result["draft_plan"][0]["outline"]


def test_missing_task_generates_blocked_section_without_fabrication():
    task = make_task("task_003", "ITSS企业资质", "business")
    missing = [{"material_type": "company_qualification", "reason": "未找到 ITSS 企业资质。"}]
    project = project_fixture(
        [task],
        [material_item("task_003", "ITSS企业资质", "business", "missing", [], missing)],
    )

    result = build_writing_plan(project)
    section = result["draft_sections"][0]

    assert section["section_type"] == "missing_blocked"
    assert section["writer_agent"] == "none"
    assert section["missing_materials"] == missing
    assert "不生成事实性正文" in section["draft_text"]
    assert "ITSS证书编号" not in section["draft_text"]


def test_price_task_does_not_calculate_price():
    task = make_task("task_004", "价格评分", "price")
    project = project_fixture(
        [task],
        [material_item("task_004", "价格评分", "price", "not_applicable")],
    )

    result = build_writing_plan(project)
    section = result["draft_sections"][0]

    assert section["section_type"] == "not_applicable"
    assert section["status"] == "not_applicable"
    assert "不进行报价测算" in section["draft_text"]
    assert "价格建议" not in section["draft_text"]


def test_evidence_refs_only_come_from_material_analysis():
    task = make_task("task_005", "项目团队人员结构", "technical")
    allowed = evidence("personal_certificate", "ACP个人证书.pdf")
    project = project_fixture(
        [task],
        [material_item("task_005", "项目团队人员结构", "technical", "matched", [allowed])],
    )
    project["files"] = [{"id": "file_not_allowed", "parsed_text": "不应被读取"}]

    result = build_writing_plan(project)
    refs = result["draft_sections"][0]["evidence_refs"]

    assert refs == [
        {
            "file_id": allowed["file_id"],
            "source": allowed["source"],
            "title": allowed["title"],
            "snippet": allowed["snippet"],
            "material_type": allowed["material_type"],
            "confidence": allowed["confidence"],
        }
    ]


def test_twenty_four_generation_tasks_all_get_draft_plan_records():
    tasks = []
    materials = []
    for index in range(1, 25):
        task_id = f"task_{index:03d}"
        name = "项目负责人情况" if index % 2 else "服务方案"
        status = "matched" if index % 2 else "writing_required"
        task_type = "technical"
        tasks.append(make_task(task_id, name, task_type))
        materials.append(
            material_item(
                task_id,
                name,
                task_type,
                status,
                [evidence("person_resume")] if status == "matched" else [],
            )
        )

    result = build_writing_plan(project_fixture(tasks, materials))

    assert result["summary"]["total_tasks"] == 24
    assert len(result["draft_plan"]) == 24
    assert len(result["draft_sections"]) == 24


def test_output_has_no_bid_decision_language():
    task = make_task("task_006", "项目负责人情况", "technical")
    project = project_fixture(
        [task],
        [material_item("task_006", "项目负责人情况", "technical", "matched", [evidence("person_resume")])],
    )

    result_text = json.dumps(build_writing_plan(project), ensure_ascii=False)

    forbidden = ["能投", "不能投", "建议投标", "不建议投标", "是否投标", "go/no-go"]
    assert not any(term in result_text for term in forbidden)


def test_personal_certificate_is_not_described_as_company_qualification():
    task = make_task("task_007", "项目负责人情况", "technical")
    project = project_fixture(
        [task],
        [
            material_item(
                "task_007",
                "项目负责人情况",
                "technical",
                "matched",
                [evidence("personal_certificate", "ACP个人证书.pdf")],
            )
        ],
    )

    result = build_writing_plan(project)
    section = result["draft_sections"][0]

    assert section["evidence_refs"][0]["material_type"] == "personal_certificate"
    assert "不将个人证书写作企业资质" in section["draft_text"]
