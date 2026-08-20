from __future__ import annotations

from app.technical_writing_quality import review_chapter_quality


def paragraph(topic: str) -> str:
    return (
        f"本段以完成{topic}并形成可核验成果为目标。在招标要求、现场条件和接口资料已经确认的"
        "前提下，项目团队按责任边界组织执行，明确采用的方法、关键控制点和过程记录，保证每项"
        "操作都能够追踪到输入条件、执行动作与输出成果。遇到条件变化或检查不通过时，先登记"
        "问题、分析影响并完成处置复核，满足完成判据后再进入下一环节。"
    )


def section_review(content: str, block_type: str = "technical_solution"):
    return review_chapter_quality(
        {
            "sections": [
                {
                    "heading": "安装部署",
                    "block_type": block_type,
                    "content": content,
                }
            ]
        },
        tender_tables=[],
    )


def bullets(count: int, *, long: bool = False) -> str:
    if long:
        return "\n".join(
            f"- 检查项{i}需要核对输入条件、执行状态、过程记录和输出成果，并在异常时完成问题关闭。"
            for i in range(1, count + 1)
        )
    return "\n".join(f"- 检查项{i}" for i in range(1, count + 1))


def test_ten_bullets_without_prose_must_fail():
    review = section_review(bullets(10))
    assert any("only_tables_or_lists" in item for item in review["quality_errors"])
    metrics = review["section_metrics"][0]
    assert metrics["bullet_item_count"] == 10
    assert metrics["short_bullet_item_count"] == 10
    assert metrics["list_dominant_sections"] == ["安装部署"]


def test_transition_and_bullets_do_not_count_as_prose():
    review = section_review("主要措施如下：\n\n" + bullets(6))
    metrics = review["section_metrics"][0]
    assert metrics["prose_paragraph_count"] == 0
    assert "安装部署" in review["sections_with_insufficient_prose"]
    assert any("no_complete_prose" in item for item in review["quality_errors"])


def test_bullet_group_without_intro_and_followup_is_a_quality_error():
    review = section_review(bullets(3))
    metrics = review["section_metrics"][0]
    assert metrics["bullet_groups_without_intro"] == 1
    assert metrics["bullet_groups_without_followup"] == 1
    assert any("bullet_groups_without_intro" in item for item in review["quality_errors"])
    assert any("bullet_groups_without_followup" in item for item in review["quality_errors"])


def test_three_bullet_groups_without_complete_prose_fail():
    content = (
        "- 第一组检查项\n\n具体如下：\n\n"
        "- 第二组检查项\n\n包括以下内容：\n\n"
        "- 第三组检查项"
    )
    review = section_review(content)
    metrics = review["section_metrics"][0]
    assert metrics["bullet_group_count"] == 3
    assert metrics["consecutive_list_group_count"] == 2
    assert any("consecutive_list_groups:2" in item for item in review["quality_errors"])


def test_bullet_characters_exceeding_prose_produces_warning_or_error():
    content = (
        paragraph("部署准备")
        + "\n\n为核对实施条件，列出以下检查项目并逐项留痕。\n\n"
        + bullets(12, long=True)
        + "\n\n上述检查结果由责任岗位复核，问题完成关闭且记录齐全后方可进入后续环节。"
        + "\n\n"
        + paragraph("部署收尾")
    )
    review = section_review(content)
    metrics = review["section_metrics"][0]
    assert metrics["bullet_character_count"] > metrics["prose_character_count"]
    combined = review["quality_errors"] + review["quality_warnings"]
    assert any("bullet_dominant" in item for item in combined)


def test_many_short_bullets_are_detected_as_ppt_outline():
    content = paragraph("方案说明") + "\n\n列示以下简短事项用于归纳。\n\n" + bullets(8) + "\n\n归纳项完成后统一核验成果。"
    review = section_review(content)
    assert any("ppt_style_short_bullets" in item for item in review["quality_errors"])


def test_named_numbered_steps_with_complete_explanations_count_as_prose():
    content = (
        "1. 环境核验。该步骤以确认部署输入和现场接口为目标，在资料齐备后核对环境条件、责任边界"
        "和依赖关系，记录检查结果；发现异常时登记问题并完成处置复核，形成环境确认记录后结束。"
        "\n\n"
        "2. 安装实施。该步骤按照已确认条件组织安装和配置，执行过程中检查操作结果、接口状态和"
        "过程记录，出现不满足项时暂停后续动作并完成整改，最终以成果完整和检查通过作为完成判据。"
    )
    review = section_review(content, "implementation_plan")
    metrics = review["section_metrics"][0]
    assert metrics["numbered_step_count"] == 2
    assert metrics["prose_paragraph_count"] == 2
    assert not any("insufficient_prose" in item for item in review["quality_errors"])


def test_inline_step_chain_is_rejected_for_narrative_section():
    content = (
        paragraph("安装准备")
        + "\n\n步骤一核对输入并登记结果；步骤二执行安装并检查接口；"
        "步骤三完成配置并复核参数；步骤四组织测试并关闭异常。"
    )
    review = section_review(content, "implementation_plan")
    assert any(
        item.endswith(":inline_step_chain")
        for item in review["quality_errors"]
    )


def test_complete_numbered_step_paragraphs_are_valid():
    content = (
        "1. 输入核验。项目团队先核对招标要求、现场条件和责任接口，登记缺失资料并完成复核，"
        "以输入完整且问题关闭作为进入实施阶段的判据。\n\n"
        "2. 安装实施。执行人员依据已确认的工作边界完成安装和配置，记录关键动作、检查结果"
        "及异常处置过程，以过程记录完整且接口检查通过作为完成判据。\n\n"
        "3. 测试复核。测试人员按照确认后的验收要求执行功能核验，发现偏差时登记、整改并"
        "重新验证，以测试记录完整且异常闭环作为阶段成果。\n\n"
        "4. 成果移交。项目团队汇总配置、测试和问题记录，与接收方逐项核对交付内容，确认"
        "资料可追溯且交接事项关闭后完成移交。"
    )
    review = section_review(content, "implementation_plan")
    assert not any(
        "inline_step_chain" in item for item in review["quality_errors"]
    )
    assert review["section_metrics"][0]["numbered_step_count"] == 4


def test_long_but_vague_boilerplate_does_not_pass_density_gate():
    vague = (
        "本项目高度重视整体建设，具有重要意义，将采用科学合理、先进可靠的总体思路，全面提升"
        "整体水平并为业务发展保驾护航。随着相关领域不断发展，本项目将继续高度重视各项工作。"
    )
    review = section_review(vague + "\n\n" + vague)
    assert review["section_metrics"][0]["prose_paragraph_count"] == 0
    assert any("no_complete_prose" in item for item in review["quality_errors"])


def test_sufficient_prose_with_one_necessary_bullet_group_passes():
    content = (
        paragraph("安装准备")
        + "\n\n为保证前述方法执行一致，以下仅归纳需要逐项核对的输入材料。\n\n"
        "- 现场接口资料\n- 已确认配置清单\n- 责任边界记录"
        + "\n\n上述材料由责任岗位核验并留痕，缺项进入问题闭环，资料完整后方可开始安装。"
        + "\n\n"
        + paragraph("安装执行")
    )
    review = section_review(content)
    assert review["quality_errors"] == []
    metrics = review["section_metrics"][0]
    assert metrics["bullet_group_count"] == 1
    assert metrics["prose_ratio"] > metrics["bullet_ratio"]


def test_natural_list_section_uses_independent_thresholds():
    content = (
        "以下清单用于核对随附材料是否齐全，并明确本清单只承担资料索引用途。"
        "\n\n"
        + bullets(8)
        + "\n\n清单核验后形成签收记录，缺失材料登记责任人并跟踪补齐状态。"
    )
    review = section_review(content, "personnel_detail")
    assert not any("only_tables_or_lists" in item for item in review["quality_errors"])
    assert not any("ppt_style_short_bullets" in item for item in review["quality_errors"])
    assert any("ppt_style_short_bullets" in item for item in review["quality_warnings"])


def test_requested_density_metrics_exist_at_section_and_chapter_level():
    review = section_review(paragraph("准备") + "\n\n" + paragraph("执行"))
    expected = {
        "prose_paragraph_count",
        "prose_character_count",
        "average_prose_paragraph_length",
        "longest_prose_paragraph_length",
        "prose_ratio",
        "section_total_character_count",
        "bullet_group_count",
        "bullet_item_count",
        "bullet_character_count",
        "bullet_ratio",
        "consecutive_list_group_count",
        "short_bullet_item_count",
        "bullet_groups_without_intro",
        "bullet_groups_without_followup",
        "list_dominant_sections",
    }
    assert expected <= set(review["section_metrics"][0])
    assert expected <= set(review["chapter_metrics"])
