from __future__ import annotations

import json
from pathlib import Path
from typing import Any


LOT_NAME = "0425139003001-6582"


def write_case_inputs(tmp_path: Path) -> Path:
    outline_dir = tmp_path / LOT_NAME / "outline"
    outline_dir.mkdir(parents=True, exist_ok=True)
    (outline_dir / "scoring_action_plan.json").write_text(json.dumps(sample_scoring_plan(), ensure_ascii=False, indent=2), encoding="utf-8")
    (outline_dir / "outline_debug.json").write_text(json.dumps(sample_outline_debug(), ensure_ascii=False, indent=2), encoding="utf-8")
    return outline_dir


def sample_scoring_plan() -> dict[str, Any]:
    return {
        "version": "v0",
        "lot_name": LOT_NAME,
        "sections": [
            section("应答文件技术部分总体评价", "应答文件技术部分总体评价", [6, 10]),
            section("项目负责人情况", "项目负责人情况", [6, 10], candidate=2),
            section("项目组织与团队配置", "项目团队人员结构", [6, 10], candidate=2),
            section(
                "业绩与项目经验",
                "业绩情况",
                [12, 20],
                candidate=2,
                target="达到该评分项上限需准备5项合格公司业绩",
                calculation="1项=12分；最高加8分；每增加1项+2分；因此共需1+4=5项",
            ),
            section("总体服务方案", "服务方案", [18, 30]),
            section("实施计划与进度保障", "工作进度及保证措施", [3, 8]),
            section("工作基础与既有能力", "工作基础", [0, 5], candidate=2),
            section(
                "专利 / 知识产权 / 创新成果",
                "专利数量",
                [0, 2],
                target="达到该项上限需准备2项采购相关专利",
                calculation="每项加1分；上限2分；因此需准备2项采购相关发明专利或实用新型专利",
            ),
            section("绩效评价材料", "绩效评价", [1, 5], unusable=3),
        ],
    }


def section(
    title: str,
    score_item: str,
    score_range: list[int],
    candidate: int = 0,
    unusable: int = 0,
    target: str = "",
    calculation: str = "",
) -> dict[str, Any]:
    return {
        "section_title": title,
        "score_item": score_item,
        "score_range": score_range,
        "max_score_preparation": {"target": target, "calculation": calculation, "not_a_score_prediction": True},
        "kb_material_findings": {
            "confirmed_usable": [],
            "needs_manual_review": [{"material_type": "person_project_experience", "title": "吴云畅个人项目经验"} for _ in range(candidate)],
            "not_usable": [{"material_type": "company_qualification", "title": "资质证书"} for _ in range(unusable)],
        },
    }


def sample_outline_debug() -> dict[str, Any]:
    technical_ref = {
        "requirement_type": "content_response",
        "source_file_id": "file_spec",
        "source_file_name": "技术规范书(基于区块链的工业数据管理技术研究).docx",
        "source_type": "technical_spec",
        "section_path": "1、基于区块链的工业数据存证技术",
        "original_excerpt": (
            "研究基于区块链的工业数据存证技术。本研究设计工业数据的链上链下协同安全存储方法，"
            "采用主链和企业链协同存储以保护不同敏感程度的工业数据安全；确保工业数据信息检索过程中的安全性。"
            "另外，研究基于同态哈希的完整性验证、基于Schnorr 签名的数字签名验证以及基于业务逻辑的合规性验证等密态数据验证技术，"
            "构建面向密态电子数据可查验需求的智能合约组件。"
        ),
    }
    deliverable_ref = {
        "requirement_type": "patent_proof",
        "source_file_id": "file_spec",
        "source_file_name": "技术规范书(基于区块链的工业数据管理技术研究).docx",
        "source_type": "technical_spec",
        "section_path": "6.2 测试验收内容",
        "original_excerpt": "提交《基于区块链的工业数据管理技术研究》技术报告。受理发明专利1项。形成基于区块链的工业数据管理技术国际标准初稿1份。",
    }
    candidates = [
        person("冯振书", "云服务部", 6),
        person("吴云畅", "数字化办公事业部", 5),
    ]
    return {
        "version": "v1.5",
        "lot_name": LOT_NAME,
        "sections": [
            {"section_title": "项目负责人情况", "person_candidates": candidates[:1], "response_requirement_refs": []},
            {"section_title": "项目组织与团队配置", "person_candidates": candidates, "response_requirement_refs": []},
            {"section_title": "总体服务方案", "response_requirement_refs": [technical_ref]},
            {"section_title": "实施计划与进度保障", "response_requirement_refs": [technical_ref, deliverable_ref]},
            {"section_title": "专利 / 知识产权 / 创新成果", "response_requirement_refs": [deliverable_ref]},
        ],
    }


def person(name: str, department: str, years: int) -> dict[str, Any]:
    return {
        "name": name,
        "department": department,
        "department_match_status": "cross_department_candidate",
        "work_years": years,
        "skills": ["Python", "M365"],
        "certificates": ["个人证书"],
        "project_experience": ["人员项目经验"],
        "match_reason": "需人工确认是否匹配本项目方向。",
    }


def table_by_id(payload: dict[str, Any], table_id: str) -> dict[str, Any]:
    return next(item for item in payload["tables"] if item["table_id"] == table_id)
