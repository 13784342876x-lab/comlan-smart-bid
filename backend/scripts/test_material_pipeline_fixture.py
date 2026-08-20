from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.material_gap_analyzer import analyze_material_gaps
from app.response_matrix_builder import build_response_matrix


EXPECTED = {
    "task_ready": {
        "readiness": "ready",
        "risk_level": "low",
        "matrix_status": "ready_for_writing",
    },
    "task_partial": {
        "readiness": "partial",
        "risk_level": "medium",
        "matrix_status": "needs_review",
    },
    "task_missing": {
        "readiness": "missing",
        "risk_level": "high",
        "matrix_status": "needs_materials",
    },
    "task_not_specified": {
        "readiness": "not_specified",
        "risk_level": "low",
        "matrix_status": "needs_review",
    },
}


def main() -> int:
    project = build_mock_project()
    match_result = build_mock_match_result()

    gap_result = analyze_material_gaps(project, match_result)
    matrix_result = build_response_matrix(project, match_result, gap_result)

    gaps_by_task = {item["task_id"]: item for item in gap_result["items"]}
    matrix_by_task = {item["task_id"]: item for item in matrix_result["items"]}

    failures: list[str] = []
    for task_id, expected in EXPECTED.items():
        gap = gaps_by_task.get(task_id, {})
        matrix = matrix_by_task.get(task_id, {})
        readiness = gap.get("readiness")
        risk_level = gap.get("risk_level")
        matrix_status = matrix.get("status")

        print(
            f"{task_id}: readiness={readiness}, "
            f"risk_level={risk_level}, status={matrix_status}"
        )

        if readiness != expected["readiness"]:
            failures.append(f"{task_id} readiness expected {expected['readiness']}, got {readiness}")
        if risk_level != expected["risk_level"]:
            failures.append(f"{task_id} risk_level expected {expected['risk_level']}, got {risk_level}")
        if matrix_status != expected["matrix_status"]:
            failures.append(f"{task_id} status expected {expected['matrix_status']}, got {matrix_status}")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PASS")
    return 0


def build_mock_project() -> dict:
    score_items = [
        make_score_item("score_ready", "business", "质量管理体系"),
        make_score_item("score_partial", "technical", "类似项目业绩"),
        make_score_item("score_missing", "technical", "项目团队"),
        make_score_item("score_not_specified", "business", "整体响应"),
    ]
    return {
        "score_items": score_items,
        "generation_tasks": [
            make_task(
                "task_ready",
                "business",
                "商务响应表",
                ["score_ready"],
                ["ISO9001证书", "有效期页"],
            ),
            make_task(
                "task_partial",
                "technical",
                "技术响应",
                ["score_partial"],
                ["项目合同", "中标通知书", "验收证明"],
            ),
            make_task(
                "task_missing",
                "technical",
                "项目团队",
                ["score_missing"],
                ["团队人员名单", "社保证明"],
            ),
            make_task(
                "task_not_specified",
                "business",
                "商务响应表",
                ["score_not_specified"],
                [],
            ),
        ],
    }


def build_mock_match_result() -> dict:
    return {
        "status": "success",
        "total_tasks": 4,
        "matched_count": 2,
        "results": [
            make_match(
                "task_ready",
                [
                    "资料名称：ISO9001证书",
                    "证书有效期页显示有效期至2030年12月31日",
                ],
            ),
            make_match(
                "task_partial",
                [
                    "资料名称：项目合同",
                    "合同内容覆盖类似项目业绩。",
                ],
            ),
            make_match("task_missing", []),
            make_match("task_not_specified", []),
        ],
    }


def make_score_item(score_id: str, category: str, name: str) -> dict:
    return {
        "id": score_id,
        "category": category,
        "item_name": name,
        "score_rule": f"{name}评分规则",
        "target_section": "响应文件",
        "generation_instruction": f"生成{name}响应内容。",
    }


def make_task(
    task_id: str,
    task_type: str,
    target_section: str,
    score_ids: list[str],
    evidence_needed: list[str],
) -> dict:
    return {
        "task_id": task_id,
        "target_section": target_section,
        "task_type": task_type,
        "related_score_item_ids": score_ids,
        "related_requirements": [f"{task_id} requirement"],
        "evidence_needed": evidence_needed,
        "kb_mapping_hint": ["fixture"],
        "generation_instruction": f"Generate response for {task_id}.",
        "status": "pending",
    }


def make_match(task_id: str, material_texts: list[str]) -> dict:
    return {
        "task_id": task_id,
        "task_type": "",
        "target_section": "",
        "query": task_id,
        "evidence_needed": [],
        "kb_mapping_hint": [],
        "matched_materials": [
            {
                "rank": index + 1,
                "file_id": f"fixture_file_{index + 1}",
                "split_item_id": f"fixture_item_{index + 1}",
                "original_name": f"{task_id}_material_{index + 1}.txt",
                "category": "fixture",
                "tags": "fixture",
                "matched_text": text,
                "distance": 0.1,
                "matched_evidence": [],
                "relevance": "medium",
                "usability": "usable",
                "usability_reason": "Fixture material is treated as direct evidence.",
            }
            for index, text in enumerate(material_texts)
        ],
        "status": "matched" if material_texts else "no_match",
        "trace": ["fixture"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
