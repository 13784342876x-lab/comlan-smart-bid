from __future__ import annotations

import json
import hashlib
from pathlib import Path

from jsonschema import Draft202012Validator

from app.chapter_draft_schema import load_chapter_draft_schema
from app.technical_doc_source import (
    MISMATCH_MESSAGE,
    record_docx_source,
    verify_docx_source,
)
from app.openai_compatible_llm_client import parse_json_object
from app.technical_writing_agent import (
    _fresh_quality_error_details,
    _merge_table_fill_data,
    chapter_run_status,
    validate_technical_chapter,
)
from app.technical_writing_quality import (
    audit_generation_permissions,
    calculate_confirmed_work_years,
    classify_deviation_status,
    count_confirmed_similar_projects,
    inspect_deviation_issues,
    inspect_unconfirmed_commitments,
    inspect_unverified_standard_references,
    normalize_tender_table,
    review_chapter_quality,
    tender_response_policy,
)
from app.technical_writing_rules import load_writing_rules


def prose(label: str = "实施") -> str:
    return (
        f"本节以完成{label}工作并形成可核验成果为目标。在招标要求、现场条件和责任接口已经"
        "确认的前提下，项目团队按照既定边界组织操作，说明实施方法、关键控制点和过程记录，"
        "保证每项工作都能够追踪到输入、动作与输出。\n\n"
        "执行中如发现输入缺失、接口异常或检查结果不满足要求，先登记问题并分析影响，完成处置"
        "和复核后再继续推进。最终以过程记录完整、异常关闭且成果满足检查要求作为完成判据。"
    )


def draft(content: str, *, block_type: str = "technical_solution", metadata=None):
    value = {
        "chapter_title": "技术方案",
        "sections": [
            {
                "heading": "实施方案",
                "block_type": block_type,
                "content": content,
                "covered_score_item_ids": [],
                "covered_requirement_ids": [],
                "placeholder_ids": [],
            }
        ],
        "claims": [],
        "placeholders_used": [],
        "score_items_covered": [],
        "requirements_covered": [],
        "warnings": [],
    }
    if metadata is not None:
        value["generated_supporting_tables"] = metadata
    return value


def source_table(title: str, headers: list[str], **point_values):
    point = {
        "point_id": "RP-1",
        "content_type": "table",
        "original_heading": title,
        "source_refs": [{"file_id": "F-1"}],
        **point_values,
    }
    return normalize_tender_table(
        {"title": title, "headers": headers, "rows": [["x"] * len(headers)]},
        point=point,
    )


def test_formal_rules_are_single_v110_prose_first_ruleset():
    rules = load_writing_rules()
    assert rules.version == "1.1.0"
    assert "正式技术标以完整正文论述为主体" in rules.content
    assert "PPT 提纲" in rules.content
    assert "legacy" not in rules.path.name.lower()


def test_source_tables_are_classified_by_purpose():
    scoring = source_table("评审因素及评标标准", ["评分项", "分值", "评分标准"])
    required = source_table(
        "技术偏差表",
        ["条款", "投标响应", "偏差说明"],
        requires_bidder_fill=True,
    )
    informational = source_table("设备参数来源表", ["参数", "要求"])
    assert scoring["table_type"] == "scoring_reference_table"
    assert scoring["reuse_required"] is False
    assert required["table_type"] == "required_response_table"
    assert required["reuse_required"] is True
    assert informational["table_type"] == "informational_source_table"


def test_scoring_semantics_override_required_marker_and_notes_do_not_imply_fill():
    scoring = normalize_tender_table(
        {
            "table_type": "required_response_table",
            "title": "评审因素及评标标准",
            "headers": ["评分项", "分值", "评分标准"],
            "rows": [],
        },
        point={"point_id": "RP-S", "source_refs": [{"file_id": "F-S"}]},
    )
    noted = normalize_tender_table(
        {
            "title": "设备参数来源表",
            "headers": ["参数", "要求"],
            "notes": ["本表内容供阅读，数据以原文为准"],
            "rows": [],
        },
        point={"point_id": "RP-N", "source_refs": [{"file_id": "F-N"}]},
    )
    two_column = source_table("项目基本信息", ["事项", "内容"])
    declaration = source_table("投标人声明函", ["声明事项", "投标人声明"])
    assert scoring["table_type"] == "scoring_reference_table"
    assert noted["table_type"] == "informational_source_table"
    assert two_column["table_type"] == "informational_source_table"
    assert declaration["table_type"] == "required_response_table"


def test_table_fill_merge_keeps_only_allowed_required_table_ids():
    merged = _merge_table_fill_data(
        [
            {"table_id": "REQ-1", "cell_values": {"响应": None}},
            {"table_id": "SCORE-1", "cell_values": {"评分": "错误记录"}},
        ],
        [
            {"table_id": "INFO-1", "cell_values": {"内容": "错误记录"}},
            {"table_id": "REQ-1", "cell_values": {"响应": "已知响应"}},
        ],
        allowed_required_table_ids={"REQ-1"},
    )
    assert merged == [
        {"table_id": "REQ-1", "cell_values": {"响应": "已知响应"}}
    ]


def test_scoring_reference_table_never_enters_body_or_fill_data():
    scoring = normalize_tender_table(
        {
            "title": "评审因素及评标标准",
            "headers": ["评分项", "分值", "评分标准"],
            "rows": [],
            "has_merged_cells": True,
        },
        point={
            "point_id": "SCORE-1",
            "content_type": "table",
            "original_heading": "评审因素及评标标准",
            "source_refs": [{"file_id": "F-SCORE"}],
        },
    )
    clean = review_chapter_quality(draft(prose()), tender_tables=[scoring])
    assert clean["table_fill_data"] == []
    inserted = review_chapter_quality(
        draft(
            prose()
            + "\n\n以下为评分表。\n\n"
            "| 评分项 | 分值 | 评分标准 |\n| --- | --- | --- |\n| 方案 | 10 | 完整 |\n\n"
            "该表用于说明评分内容。"
        ),
        tender_tables=[scoring],
    )
    assert any(
        "scoring_reference_table_inserted_in_response" in item
        for item in inserted["quality_errors"]
    )


def test_narrative_section_with_only_table_and_list_fails():
    content = (
        "| 阶段 | 工作 |\n| --- | --- |\n| 准备 | 核对 |\n\n"
        "- 检查输入\n- 形成记录"
    )
    review = review_chapter_quality(draft(content), tender_tables=[])
    assert any("only_tables_or_lists" in item for item in review["quality_errors"])
    assert review["sections_with_insufficient_prose"] == ["实施方案"]


def test_narrative_section_requires_two_complete_prose_paragraphs():
    review = review_chapter_quality(
        draft("本节说明实施目标、方法、控制点和成果，但只有一个短段落。"),
        tender_tables=[],
    )
    assert any(
        "insufficient_prose" in item or "no_complete_prose" in item
        for item in review["quality_errors"]
    )


def test_generated_table_requires_intro_followup_and_rationale():
    table = "| 阶段 | 成果 |\n| --- | --- |\n| 实施 | 记录 |"
    missing = review_chapter_quality(draft(prose() + "\n\n" + table), tender_tables=[])
    assert any("table_without_followup" in item for item in missing["quality_errors"])
    assert any("missing_purpose_metadata" in item for item in missing["quality_errors"])


def test_balanced_prose_and_one_supporting_table_passes():
    content = (
        prose()
        + "\n\n以下阶段成果表用于横向核对阶段与成果的对应关系，不代替前述实施说明。"
        + "\n\n表1 阶段成果表"
        + "\n\n| 阶段 | 成果 |\n| --- | --- |\n| 实施 | 过程记录 |"
        + "\n\n表格执行时仍以前述控制点和完成判据为准，异常事项进入问题闭环。"
    )
    review = review_chapter_quality(
        draft(
            content,
            metadata=[
                {
                    "section_id": "实施方案",
                    "title": "阶段成果表",
                    "purpose": "核对阶段与成果",
                    "why_table_is_better_than_prose": "横向对应字段便于逐项检查",
                }
            ],
        ),
        tender_tables=[],
    )
    assert review["quality_errors"] == []
    assert review["chapter_metrics"]["prose_ratio"] > review["chapter_metrics"]["table_ratio"]


def test_required_response_table_is_not_rejected_for_table_ratio():
    required = source_table(
        "技术响应表",
        ["条款", "投标响应"],
        requires_bidder_fill=True,
    )
    content = (
        prose()
        + "\n\n以下原表按招标字段填写，用于逐项响应招标条款。"
        + "\n\n| 条款 | 投标响应 |\n| --- | --- |\n"
        + "\n".join(f"| 要求{i} | 响应说明{i} |" for i in range(20))
        + "\n\n原表填写内容应与正文方案和最终确认的技术应答保持一致。"
    )
    review = review_chapter_quality(draft(content), tender_tables=[required])
    assert not any("structured_content_exceeds_prose" in item for item in review["quality_errors"])
    assert review["table_fill_data"] == []


def test_required_table_exempts_only_its_own_characters_not_other_lists():
    required = source_table(
        "技术响应表",
        ["条款", "投标响应"],
        requires_bidder_fill=True,
    )
    content = (
        prose()
        + "\n\n以下原表用于逐项填写投标响应，字段边界保持不变。"
        + "\n\n| 条款 | 投标响应 |\n| --- | --- |\n| 要求 | 响应 |"
        + "\n\n原表填写结果将与后续技术说明核对，并保留问题关闭记录。"
        + "\n\n"
        + "\n".join(
            f"- 空泛条目{i}仅重复一般原则且没有说明具体输入、执行动作、控制点、成果或完成判据"
            for i in range(40)
        )
    )
    review = review_chapter_quality(draft(content), tender_tables=[required])
    assert any(
        "bullet_dominant" in item or "structured_content_exceeds_prose" in item
        for item in review["quality_errors"]
    )
    assert review["chapter_metrics"]["required_table_exempt_character_count"] > 0


def test_unconfirmed_numeric_commitment_and_product_capability_are_errors():
    value = draft(
        prose()
        + "\n\n我方将在2个工作日内完成部署，并保证系统具备高可用和故障自动恢复能力。"
    )
    errors = inspect_unconfirmed_commitments(value, confirmed_facts={})
    assert any("numeric_commitment" in item for item in errors)
    assert any("unconfirmed_product_capability" in item for item in errors)


def test_numeric_commitment_subtypes_have_specific_instructions():
    value = draft(
        prose()
        + "\n\n项目计划在3个工作日内完成。"
        + "\n\n拟投入3名工程师。"
        + "\n\n提供7×24小时技术响应。"
        + "\n\n整改资料应在2日内提交。"
    )
    raw = [
        "fact_boundary:实施方案:numeric_commitment:项目计划在3个工作日内完成。",
        "fact_boundary:实施方案:numeric_commitment:拟投入3名工程师。",
        "fact_boundary:实施方案:numeric_commitment:提供7×24小时技术响应。",
        "fact_boundary:实施方案:numeric_commitment:整改资料应在2日内提交。",
    ]
    details = _fresh_quality_error_details(raw, draft=value)
    by_type = {
        item["offending_fact_type"]: item["boundary_instruction"]
        for item in details
    }
    assert "按合同及确认计划执行" in by_type["schedule_duration"]
    assert "不得自行填写" in by_type["staffing_count"]
    assert "数值与单位完全等同" in by_type["service_level"]
    assert "整改、交接" in by_type["handover_or_rectification_deadline"]


def test_unconfirmed_staffing_count_remains_blocked():
    value = draft(prose() + "\n\n项目团队配置1名项目经理和3名实施工程师。")
    errors = inspect_unconfirmed_commitments(value, confirmed_facts={})
    assert any("numeric_commitment" in item for item in errors)
    details = _fresh_quality_error_details(errors, draft=value)
    assert any(
        item["offending_fact_type"] == "staffing_count"
        for item in details
    )


def test_placeholder_or_cautious_condition_does_not_become_commitment():
    value = draft(
        prose()
        + "\n\n具体响应时限为{{PH-123456789ABC}}；若具备已确认的集群条件，可在技术应答确认后启用多副本。"
    )
    assert inspect_unconfirmed_commitments(value, confirmed_facts={}) == []


def test_all_supported_placeholder_forms_cover_unconfirmed_values():
    values = [
        "{{PH-SERVICE-COMMITMENT}}",
        "【待人工确认：最终响应时限】",
        "【待补充企业材料：服务能力证明】",
        "【待核验外部来源：标准适用性】",
    ]
    for placeholder in values:
        value = draft(prose() + f"\n\n我方承诺在{placeholder}所示条件确认后执行。")
        assert inspect_unconfirmed_commitments(value, confirmed_facts={}) == []


def test_exact_tender_deadlines_can_be_answered_as_no_deviation():
    tender_text = "采购要求明确：交付应在 **30 日内** 完成，并提供 **7×24 小时** 服务。"
    tender = draft(tender_text)
    tender["claims"] = [
        {
            "text": "交付应在30日内完成，并提供7×24小时服务",
            "claim_type": "tender_fact",
            "source_ids": ["RP-1"],
            "evidence_ids": [],
            "human_review_required": False,
        }
    ]
    assert inspect_unconfirmed_commitments(tender, confirmed_facts={}) == []
    bidder = draft(
        tender_text + "\n\n我方承诺在30日内完成，并保证提供7×24小时服务。"
    )
    bidder["claims"] = tender["claims"]
    errors = inspect_unconfirmed_commitments(bidder, confirmed_facts={})
    assert errors == []


def test_standalone_if_does_not_exempt_definite_commitment():
    value = draft(prose() + "\n\n若现场正常，我方承诺2个工作日内完成部署。")
    errors = inspect_unconfirmed_commitments(value, confirmed_facts={})
    assert any("numeric_commitment" in item for item in errors)


def test_candidate_material_cannot_be_promoted_to_confirmed_fact():
    value = draft(prose() + "\n\n我公司已通过相关认证，我方项目经理为张三。")
    errors = inspect_unconfirmed_commitments(value, confirmed_facts={})
    assert any("unconfirmed_" in item for item in errors)
    assert not any("candidate_promoted_to_fact" in item for item in errors)


def test_candidate_binding_phrasings_are_all_rejected():
    for statement in (
        "项目经理由张三担任。",
        "张三负责本项目。",
        "拟派张三担任项目负责人。",
        "张三持有一级项目管理资质。",
        "我公司已完成三个同类项目并具有相关业绩。",
    ):
        errors = inspect_unconfirmed_commitments(
            draft(prose() + "\n\n" + statement),
            confirmed_facts={},
        )
        assert any("unconfirmed_" in item for item in errors), statement
        assert not any("candidate_promoted_to_fact" in item for item in errors), statement


def test_candidate_enterprise_qualification_cannot_become_definite_prose():
    value = draft(
        prose()
        + "\n\n公司运行符合 ISO 9001 要求的质量管理体系，"
        "我公司已通过相关质量管理体系认证。"
    )
    errors = inspect_unconfirmed_commitments(
        value,
        confirmed_facts={},
        candidate_evidence_ids={"KB-QUALIFICATION"},
    )
    assert any("candidate_promoted_to_fact" in item for item in errors)


def test_human_review_required_does_not_confirm_candidate_fact():
    value = draft(prose() + "\n\n我公司已通过质量管理体系认证。")
    value["claims"] = [
        {
            "text": "我公司已通过质量管理体系认证",
            "claim_type": "confirmed_enterprise_fact",
            "source_ids": [],
            "evidence_ids": ["KB-CANDIDATE-QUALIFICATION"],
            "human_review_required": True,
        }
    ]
    validation = validate_technical_chapter(
        value,
        schema=load_chapter_draft_schema(),
        target={"title": "技术方案", "children": []},
        confirmed_facts={},
        pending_confirmations=[],
        tender_tables=[],
        candidate_evidence_ids={"KB-CANDIDATE-QUALIFICATION"},
        confirmed_evidence_ids=set(),
    )
    assert validation["valid"] is False
    assert any(
        "confirmed enterprise claims require confirmed evidence" in item
        for item in validation["errors"]
    )


def test_person_placeholder_does_not_cover_unconfirmed_qualification_or_years():
    value = draft(
        prose()
        + "\n\n拟派{{PH-PROJECT-MANAGER}}具备5年以上项目经验，"
        "持有高级资格证书并提供劳动合同和社保证明。"
    )
    errors = inspect_unconfirmed_commitments(value, confirmed_facts={})
    assert any("unconfirmed_personnel_qualification" in item for item in errors)
    assert not any("candidate_promoted_to_fact" in item for item in errors)
    assert any("numeric_commitment" in item for item in errors)


def test_candidate_id_never_supports_confirmed_enterprise_fact():
    value = draft(prose() + "\n\n我公司已取得相关企业资质。")
    value["claims"] = [
        {
            "text": "我公司已取得相关企业资质",
            "claim_type": "confirmed_enterprise_fact",
            "source_ids": [],
            "evidence_ids": ["KB-CANDIDATE-1"],
            "human_review_required": False,
        }
    ]
    validation = validate_technical_chapter(
        value,
        schema=load_chapter_draft_schema(),
        target={"title": "技术方案", "children": []},
        confirmed_facts={},
        pending_confirmations=[],
        tender_tables=[],
        candidate_evidence_ids={"KB-CANDIDATE-1"},
    )
    assert any(
        "confirmed enterprise claims require confirmed evidence" in item
        for item in validation["errors"]
    )


def test_confirmed_project_fact_supports_traced_enterprise_claim():
    value = draft(prose() + "\n\n投标人名称为华东智联科技有限公司。")
    value["claims"] = [
        {
            "text": "投标人名称为华东智联科技有限公司。",
            "claim_type": "confirmed_enterprise_fact",
            "source_ids": ["FACT-001"],
            "evidence_ids": [],
            "human_review_required": False,
        }
    ]
    validation = validate_technical_chapter(
        value,
        schema=load_chapter_draft_schema(),
        target={"title": "技术方案", "children": []},
        confirmed_facts={"bidder_name": "华东智联科技有限公司"},
        pending_confirmations=[],
        tender_tables=[],
    )

    assert not any(
        "confirmed enterprise claims require confirmed evidence" in item
        for item in validation["errors"]
    )


def test_confirmed_project_fact_value_remains_valid_when_model_omits_source_id():
    value = draft(prose() + "\n\n投标人名称为华东智联科技有限公司。")
    value["claims"] = [
        {
            "text": "投标人名称为华东智联科技有限公司。",
            "claim_type": "confirmed_enterprise_fact",
            "source_ids": [],
            "evidence_ids": [],
            "human_review_required": False,
        }
    ]
    validation = validate_technical_chapter(
        value,
        schema=load_chapter_draft_schema(),
        target={"title": "技术方案", "children": []},
        confirmed_facts={"bidder_name": "华东智联科技有限公司"},
        pending_confirmations=[],
        tender_tables=[],
    )

    assert not any(
        "confirmed enterprise claims require confirmed evidence" in item
        for item in validation["errors"]
    )


def test_tender_capability_is_requirement_not_confirmed_offered_capability():
    requirement = "招标文件明确要求本系统应支持多副本和在线扩展。"
    tender = draft(prose() + "\n\n" + requirement)
    tender["claims"] = [
        {
            "text": requirement,
            "claim_type": "tender_fact",
            "source_ids": ["RP-CAPABILITY"],
            "evidence_ids": [],
            "human_review_required": False,
        }
    ]
    assert inspect_unconfirmed_commitments(tender, confirmed_facts={}) == []
    offered = draft(
        prose()
        + "\n\n"
        + requirement
        + "\n\n本系统支持多副本和在线扩展，并确保扩展过程业务不中断。"
    )
    offered["claims"] = tender["claims"]
    errors = inspect_unconfirmed_commitments(offered, confirmed_facts={})
    assert any("unconfirmed_product_capability" in item for item in errors)


def test_confirmed_product_capability_is_allowed():
    value = draft(prose() + "\n\n本系统支持多副本并采用反亲和配置。")
    value["claims"] = [
        {
            "text": "本系统支持多副本并采用反亲和配置",
            "claim_type": "confirmed_enterprise_fact",
            "source_ids": [],
            "evidence_ids": ["CONFIRMED-PRODUCT-1"],
            "human_review_required": False,
        }
    ]
    assert inspect_unconfirmed_commitments(
        value,
        confirmed_facts={},
        confirmed_evidence_ids={"CONFIRMED-PRODUCT-1"},
    ) == []


def test_design_principle_is_not_product_capability():
    value = draft(
        prose()
        + "\n\n本方案以高可用、可扩展和易维护为设计目标，"
        "遵循高可靠、平滑演进原则，围绕安全稳定目标进行方案设计。"
    )
    assert inspect_unconfirmed_commitments(value, confirmed_facts={}) == []


def test_design_goal_does_not_confirm_offered_feature():
    value = draft(prose() + "\n\n以在线扩展和业务连续性为设计目标。")
    assert inspect_unconfirmed_commitments(value, confirmed_facts={}) == []


def test_negated_acceptance_commitment_boundary_is_neutral_methodology():
    value = draft("实施执行人员不负责修改验收判据，也不直接对外作出承诺。")
    value["sections"][0]["section_id"] = "WC-005-01"
    ledger = [
        {
            "section_id": "WC-005-01",
            "section_title": "实施方案",
            "generation_permissions": {},
        }
    ]

    assert audit_generation_permissions(value, fact_ledger=ledger)["passed"] is True


def test_positive_acceptance_commitment_remains_blocked():
    value = draft("实施执行人员负责验收判据，并直接对外作出承诺。")
    value["sections"][0]["section_id"] = "WC-005-01"
    ledger = [
        {
            "section_id": "WC-005-01",
            "section_title": "实施方案",
            "generation_permissions": {},
        }
    ]

    audit = audit_generation_permissions(value, fact_ledger=ledger)
    assert audit["passed"] is False
    assert audit["unauthorized_bidder_commitments"][0]["offending_span"] == "验收"


def test_technical_response_point_cross_check_is_not_service_commitment():
    value = draft(
        "组织架构对各技术响应点的承接，以招标正文原文页码、条款号和"
        "适用范围的核对结果为准。"
    )
    value["sections"][0]["section_id"] = "WC-005-01"
    ledger = [
        {
            "section_id": "WC-005-01",
            "section_title": "实施方案",
            "generation_permissions": {},
        }
    ]

    assert audit_generation_permissions(value, fact_ledger=ledger)["passed"] is True


def test_positive_technical_response_service_commitment_remains_blocked():
    value = draft("我方承诺提供技术响应服务。")
    value["sections"][0]["section_id"] = "WC-005-01"
    ledger = [
        {
            "section_id": "WC-005-01",
            "section_title": "实施方案",
            "generation_permissions": {},
        }
    ]

    audit = audit_generation_permissions(value, fact_ledger=ledger)
    assert audit["passed"] is False
    assert audit["unauthorized_bidder_commitments"][0]["offending_span"] == "技术响应"


def test_system_supports_online_expansion_remains_blocked():
    value = draft(prose() + "\n\n本系统支持在线扩展并具备多副本能力。")
    errors = inspect_unconfirmed_commitments(value, confirmed_facts={})
    assert any("unconfirmed_product_capability" in item for item in errors)


def test_failure_simulation_as_verification_target_is_allowed():
    value = draft(
        prose()
        + "\n\n测试阶段模拟故障场景并验证招标要求，"
        "核验高可用、数据保护和故障恢复机制是否满足要求，"
        "记录实际结果并按照测试判据判断。"
    )
    assert inspect_unconfirmed_commitments(value, confirmed_facts={}) == []


def test_test_target_does_not_confirm_product_capability():
    value = draft(
        prose()
        + "\n\n测试目标是验证本系统是否支持在线扩展，"
        "实际结果以确认后的技术应答项和测试记录为准。"
    )
    assert inspect_unconfirmed_commitments(value, confirmed_facts={}) == []


def test_asserted_test_result_remains_blocked():
    value = draft(
        prose() + "\n\n测试证明本系统必然实现业务不中断和故障自动恢复。"
    )
    errors = inspect_unconfirmed_commitments(value, confirmed_facts={})
    assert any("unconfirmed_product_capability" in item for item in errors)


def test_exact_tender_required_service_level_is_allowed():
    sentence = "我方保证24小时内安排维修工程师到达现场。"
    value = draft(prose() + "\n\n" + sentence)
    value["claims"] = [
        {
            "text": "投标人应保证24小时内安排维修工程师到达现场。",
            "claim_type": "tender_fact",
            "source_ids": ["RP-SERVICE-24H"],
            "evidence_ids": [],
            "human_review_required": False,
        }
    ]
    assert inspect_unconfirmed_commitments(value, confirmed_facts={}) == []


def test_exact_7x24_tender_service_requirement_is_allowed():
    sentence = "我方提供7×24小时技术响应服务。"
    value = draft(prose() + "\n\n" + sentence)
    value["claims"] = [
        {
            "text": "供应商应提供7×24小时技术响应服务。",
            "claim_type": "tender_fact",
            "source_ids": ["RP-SERVICE-7X24"],
            "evidence_ids": [],
            "human_review_required": False,
        }
    ]
    assert inspect_unconfirmed_commitments(value, confirmed_facts={}) == []


def test_better_than_tender_service_level_requires_confirmation():
    sentence = "我方保证2小时内安排维修工程师到达现场。"
    value = draft(prose() + "\n\n" + sentence)
    value["claims"] = [
        {
            "text": "投标人应保证24小时内安排维修工程师到达现场。",
            "claim_type": "tender_fact",
            "source_ids": ["RP-SERVICE-24H"],
            "evidence_ids": [],
            "human_review_required": False,
        }
    ]
    errors = inspect_unconfirmed_commitments(value, confirmed_facts={})
    assert any("numeric_commitment" in item for item in errors)


def test_tender_maximum_delivery_period_defaults_to_exact_no_deviation():
    sentence = "我方承诺30日内完成交货。"
    value = draft(prose() + "\n\n" + sentence)
    value["claims"] = [
        {
            "text": "招标文件规定交货期最长为30日，投标人须响应。",
            "claim_type": "tender_fact",
            "source_ids": ["RP-DELIVERY-30D"],
            "evidence_ids": [],
            "human_review_required": False,
        }
    ]
    errors = inspect_unconfirmed_commitments(value, confirmed_facts={})
    assert errors == []


def test_unverified_standard_number_is_blocked_when_web_unavailable():
    value = draft(prose() + "\n\n验收确定以 GB/T 99999-2026 作为适用依据。")
    value["claims"] = [
        {
            "text": "验收确定以 GB/T 99999-2026 作为适用依据",
            "claim_type": "proposal_statement",
            "source_ids": [],
            "evidence_ids": [],
            "human_review_required": False,
        }
    ]
    errors = inspect_unverified_standard_references(
        value,
        web_verification={"status": "unavailable"},
    )
    assert any("unverified_standard_reference" in item for item in errors)


def test_standard_from_tender_source_is_allowed():
    sentence = "招标文件明确要求验收依据为 GB/T 12345-2020。"
    value = draft(prose() + "\n\n" + sentence)
    value["claims"] = [
        {
            "text": sentence,
            "claim_type": "tender_fact",
            "source_ids": ["RP-STANDARD"],
            "evidence_ids": [],
            "human_review_required": False,
        }
    ]
    assert inspect_unverified_standard_references(
        value,
        web_verification={"status": "unavailable"},
    ) == []


def test_standard_from_confirmed_local_material_is_allowed():
    sentence = "测试采用 GB/T 54321-2024 作为已确认的本地材料依据。"
    value = draft(prose() + "\n\n" + sentence)
    value["claims"] = [
        {
            "text": sentence,
            "claim_type": "confirmed_enterprise_fact",
            "source_ids": [],
            "evidence_ids": ["CONFIRMED-STANDARD-1"],
            "human_review_required": False,
        }
    ]
    assert inspect_unverified_standard_references(
        value,
        web_verification={"status": "unavailable"},
        confirmed_evidence_ids={"CONFIRMED-STANDARD-1"},
    ) == []


def test_pending_standard_placeholder_is_allowed_but_not_submission_ready():
    placeholder_id = "PH-EXTERNAL-STANDARD"
    value = draft(
        prose() + f"\n\n适用标准编号为{{{{{placeholder_id}}}}}，核验完成后再引用。"
    )
    value["sections"][0]["placeholder_ids"] = [placeholder_id]
    value["placeholders_used"] = [placeholder_id]
    pending = [
        {
            "field": "适用外部标准",
            "placeholder_id": placeholder_id,
            "blocking": True,
        }
    ]
    validation = validate_technical_chapter(
        value,
        schema=load_chapter_draft_schema(),
        target={"title": "技术方案", "children": []},
        confirmed_facts={},
        pending_confirmations=pending,
        tender_tables=[],
        web_verification={"status": "unavailable"},
    )
    status, _, submission_ready = chapter_run_status(value, validation, pending)
    assert validation["valid"] is True
    assert validation["validation_status"] == "passed_with_warnings"
    assert validation["candidate_generated"] is True
    assert validation["candidate_saved"] is False
    assert validation["candidate_available"] is False
    assert validation["candidate_reviewable"] is True
    assert validation["submission_ready"] is False
    assert any(
        item["severity"] == "warning"
        and item["category"] == "placeholder"
        and item["code"] == "placeholder_pending_completion"
        for item in validation["issues"]
    )
    assert status == "completed"
    assert submission_ready is False


def test_validation_issues_have_stable_shape_and_blocking_fact_severity():
    value = draft(prose() + "\n\n本系统支持在线扩展并确保业务不中断。")
    validation = validate_technical_chapter(
        value,
        schema=load_chapter_draft_schema(),
        target={"title": "技术方案", "children": []},
        confirmed_facts={},
        pending_confirmations=[],
        tender_tables=[],
    )
    issue = next(
        item
        for item in validation["issues"]
        if item["code"] == "unconfirmed_product_capability"
    )
    assert {
        "severity",
        "category",
        "code",
        "section_id",
        "section_title",
        "message",
        "repair_disposition",
        "offending_span",
    } <= set(issue)
    assert issue["severity"] == "blocking"
    assert issue["category"] == "fact_boundary"
    assert validation["validation_status"] == "blocked"
    assert validation["candidate_generated"] is True
    assert validation["candidate_saved"] is False
    assert validation["candidate_available"] is False
    assert validation["candidate_reviewable"] is True
    assert validation["submission_ready"] is False
    assert chapter_run_status(value, validation, []) == (
        "completed",
        "reviewable_draft_with_blockers",
        False,
    )


def test_schema_issue_is_fatal_and_candidate_is_not_reviewable():
    value = draft(prose())
    value["unexpected_schema_field"] = True
    validation = validate_technical_chapter(
        value,
        schema=load_chapter_draft_schema(),
        target={"title": "技术方案", "children": []},
        confirmed_facts={},
        pending_confirmations=[],
        tender_tables=[],
    )
    assert validation["validation_status"] == "fatal"
    assert validation["candidate_generated"] is True
    assert validation["candidate_saved"] is False
    assert validation["candidate_available"] is False
    assert validation["candidate_reviewable"] is False
    assert validation["submission_ready"] is False
    assert validation["issue_counts"]["fatal"] == 1
    assert any(
        item["severity"] == "fatal"
        and item["category"] == "schema"
        and item["code"] == "schema_validation_failed"
        for item in validation["issues"]
    )
    assert chapter_run_status(value, validation, []) == (
        "failed",
        "invalid_draft",
        False,
    )


def test_writing_quality_issue_blocks_submission_without_failing_generation():
    value = draft("仅有一句简短说明。")
    validation = validate_technical_chapter(
        value,
        schema=load_chapter_draft_schema(),
        target={"title": "技术方案", "children": []},
        confirmed_facts={},
        pending_confirmations=[],
        tender_tables=[],
    )
    assert any(
        item["severity"] == "blocking"
        and item["category"] == "writing_quality"
        for item in validation["issues"]
    )
    assert validation["candidate_reviewable"] is True
    assert validation["submission_ready"] is False
    assert chapter_run_status(value, validation, [])[0] == "completed"


def test_local_deterministic_json_repair_preserves_section_content_sha256():
    original_content = "第一行\n第二行"
    parsed = parse_json_object(
        '{"sections":[{"content":"第一行\n第二行",},],"items":[1,2,],}'
    )
    repaired_content = parsed["sections"][0]["content"]
    assert hashlib.sha256(repaired_content.encode("utf-8")).hexdigest() == (
        hashlib.sha256(original_content.encode("utf-8")).hexdigest()
    )
    assert repaired_content == original_content


def test_generated_supporting_tables_is_optional_but_strictly_validated_by_schema():
    schema = load_chapter_draft_schema()
    validator = Draft202012Validator(schema)
    without_metadata = draft(prose())
    validator.validate(without_metadata)
    with_metadata = draft(
        prose(),
        metadata=[
            {
                "section_id": "实施方案",
                "title": "阶段成果表",
                "purpose": "逐项核对阶段成果",
                "why_table_is_better_than_prose": "字段对应关系需要横向核对",
            }
        ],
    )
    validator.validate(with_metadata)
    assert schema["additionalProperties"] is False
    assert (
        schema["properties"]["generated_supporting_tables"]["items"][
            "additionalProperties"
        ]
        is False
    )


def test_declared_generated_table_must_exist_in_section():
    metadata = [
        {
            "section_id": "实施方案",
            "title": f"辅助表{i}",
            "purpose": "辅助核对",
            "why_table_is_better_than_prose": "字段需要横向对应",
        }
        for i in range(1, 4)
    ]
    review = review_chapter_quality(
        draft(prose(), metadata=metadata),
        tender_tables=[],
    )
    assert any(
        "generated_supporting_table:实施方案:declared_but_missing" == item
        for item in review["quality_errors"]
    )


def test_dangling_table_reference_is_blocked():
    review = review_chapter_quality(
        draft(prose() + "\n\n具体明细将在下表中列出，但正文到此结束。"),
        tender_tables=[],
    )
    assert any(
        item == "section_quality:实施方案:dangling_table_reference"
        for item in review["quality_errors"]
    )


def test_generated_table_and_metadata_are_bidirectionally_consistent():
    content = (
        prose()
        + "\n\n以下阶段核对表用于对应检查事项与成果。"
        + "\n\n表1 阶段核对表"
        + "\n\n| 检查事项 | 成果 |\n| --- | --- |\n| 输入核验 | 核验记录 |"
        + "\n\n阶段核对表完成后继续执行结果复核和问题闭环。"
    )
    metadata = [
        {
            "section_id": "实施方案",
            "title": "阶段核对表",
            "purpose": "对应检查事项与成果",
            "why_table_is_better_than_prose": "字段需要横向对应",
        }
    ]
    review = review_chapter_quality(
        draft(content, metadata=metadata),
        tender_tables=[],
    )
    assert not any(
        "generated_supporting_table" in item
        for item in review["quality_errors"]
    )


def test_empty_generated_table_metadata_is_valid_when_no_tables_exist():
    review = review_chapter_quality(
        draft(prose(), metadata=[]),
        tender_tables=[],
    )
    assert not any(
        "generated_supporting_table" in item
        for item in review["quality_errors"]
    )


def test_docx_source_sha_record_matches_and_detects_stale_markdown(tmp_path: Path):
    markdown = tmp_path / "chapter.md"
    docx = tmp_path / "chapter.docx"
    markdown.write_text("# 当前正文\n", encoding="utf-8")
    docx.write_bytes(b"fake-docx-for-source-tracking")
    record = record_docx_source(markdown, docx, converter="fake-converter")
    assert record["source_markdown_sha256"]
    assert verify_docx_source(markdown, docx)["matches"] is True
    markdown.write_text("# 已修改正文\n", encoding="utf-8")
    stale = verify_docx_source(markdown, docx)
    assert stale["matches"] is False
    assert stale["message"] == MISMATCH_MESSAGE
