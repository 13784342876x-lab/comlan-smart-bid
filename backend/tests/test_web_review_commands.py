from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.technical_fulltext_human_review import TechnicalFulltextHumanReviewService
from app.technical_fulltext_human_review_finalization import TechnicalFulltextHumanReviewFinalizer
from app.web.api import create_app


BACKEND_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PROJECT = BACKEND_ROOT / "outputs" / "hospital_hci_001_fulltext_real_20260806_02"
PROJECT_ID = "hospital_hci_review_command_fixture"
TR003_ISSUE = "FAI-8A16A33D982E3BF8"


@pytest.fixture()
def command_client(tmp_path: Path) -> tuple[TestClient, Path, str]:
    outputs = tmp_path / "outputs"
    project = outputs / PROJECT_ID
    shutil.copytree(SOURCE_PROJECT, project)
    review_dir = project / "fulltext" / "review"
    package_path = review_dir / "technical_fulltext.review_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    material = next(item for item in package["fact_material_queue"] if item["source_type"] in {"EVIDENCE", "EVIDENCE_QUERY"})
    material["status"] = "candidate_found"
    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in ("technical_fulltext.human_review.json", "technical_fulltext.human_review.md", "technical_fulltext.review_finalization.json", "technical_fulltext.review_finalization.md", "technical_fulltext.stage13_handoff.json"):
        path = review_dir / name
        if path.exists():
            path.unlink()
    TechnicalFulltextHumanReviewService().initialize(review_package_json_path=package_path, output_dir=review_dir)
    TechnicalFulltextHumanReviewFinalizer().finalize(
        review_package_json_path=package_path,
        human_review_json_path=review_dir / "technical_fulltext.human_review.json",
        output_dir=review_dir,
    )
    return TestClient(create_app(outputs)), project, str(material["source_id"])


def test_commands_use_canonical_service_refresh_finalization_and_reject_stale_write(command_client: tuple[TestClient, Path, str]) -> None:
    client, project, material_id = command_client
    review_url = f"/api/projects/{PROJECT_ID}/review"
    initial = client.get(review_url)
    assert initial.status_code == 200
    token_a = initial.headers["etag"]
    assert initial.json()["actionability_counts"] == {"must_decide": 10, "verification": 32, "no_action": 3, "informational": 1}

    issue = client.patch(f"{review_url}/issues/{TR003_ISSUE}", headers={"If-Match": token_a}, json={"decision": "accepted", "use_machine_location": True})
    assert issue.status_code == 200, issue.text
    token_b = issue.headers["etag"]
    assert token_b != token_a
    assert issue.json()["review"]["progress"]["issue_decisions_completed"] == 1
    tr003_after = next(item for group in issue.json()["review"]["groups"] for item in group["items"] if item["trace_id"] == TR003_ISSUE)
    assert tr003_after["change_history"]
    assert tr003_after["machine_revision_available"] is True

    stale = client.patch(f"{review_url}/issues/{TR003_ISSUE}", headers={"If-Match": token_a}, json={"decision": "rejected", "rejection_reason": "stale overwrite", "use_machine_location": True})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_review"
    current_issue = next(item for group in client.get(review_url).json()["groups"] for item in group["items"] if item["trace_id"] == TR003_ISSUE)
    assert current_issue["current_decision"] == "accepted"

    fact = client.patch(f"{review_url}/facts/FACT-013", headers={"If-Match": token_b}, json={"confirmation_status": "confirmed", "confirmed_value": "天津市；022-00000000；技术人员 12 人", "source_description": "fixture 人工确认", "confirmation_basis": "human_provided", "confirmation_note": "仅用于隔离测试"})
    assert fact.status_code == 200, fact.text
    token_c = fact.headers["etag"]
    fact_item = next(item for item in fact.json()["review"]["fact_confirmations"] if item["trace_id"] == "FACT-013")
    assert fact_item["confirmation_status"] == "confirmed"
    assert fact_item["change_history"]

    material = client.patch(f"{review_url}/materials/{material_id}", headers={"If-Match": token_c}, json={"verification_status": "verified", "material_name": "fixture ISO certificate", "subject_match": True, "verification_note": "fixture document verified", "candidate_reference": "fixture-candidate-001"})
    assert material.status_code == 200, material.text
    token_d = material.headers["etag"]
    assert material.json()["review"]["progress"]["material_verifications_completed"] == 1

    another_issue = next(item["trace_id"] for group in material.json()["review"]["groups"] if group["key"] == "must_decide" for item in group["items"] if item["trace_id"] != TR003_ISSUE)
    risk = client.post(f"{review_url}/issues/{another_issue}/risk-acceptances", headers={"If-Match": token_d}, json={"reason": "fixture accepts the known release risk", "scope": "fixture current reviewed version", "confirmed": True})
    assert risk.status_code == 200, risk.text
    assert risk.json()["review"]["progress"]["issue_decisions_completed"] == 2
    assert risk.json()["overview"]["revision_ready"] is False
    assert str(project) not in json.dumps(risk.json(), ensure_ascii=False)

    human = json.loads((project / "fulltext" / "review" / "technical_fulltext.human_review.json").read_text(encoding="utf-8"))
    assert human["summary"]["accepted_risk_count"] == 1
    assert human["risk_acceptances"][0]["source_refs"] == [another_issue]
    finalization = json.loads((project / "fulltext" / "review" / "technical_fulltext.review_finalization.json").read_text(encoding="utf-8"))
    assert finalization["source_lineage"]["stage12b_human_review"]["sha256"] == risk.json()["version_token"]
    accepted_candidate = next(
        item
        for item in finalization["revision_candidates"]
        if item["source_id"] == TR003_ISSUE
    )
    assert accepted_candidate["human_decision"] == "accepted"
    assert accepted_candidate["effective_revision_instruction"] == accepted_candidate["machine_recommended_action"]["text"]
    assert not any(
        item["source_id"] == another_issue
        for item in finalization["revision_candidates"]
    )
    requirement = client.get(f"/api/projects/{PROJECT_ID}/requirements/TR-003")
    assert requirement.status_code == 200
    assert any(item["current_decision"] == "accepted" for item in requirement.json()["review_states"])
    assert next(item for item in requirement.json()["fact_dependencies"] if item["trace_id"] == "FACT-013")["status"] == "confirmed"
    facts_materials = client.get(f"/api/projects/{PROJECT_ID}/facts-materials").json()
    assert next(item for item in facts_materials["items"] if item["trace_id"] == "FACT-013")["status"] == "confirmed"
    assert next(item for item in facts_materials["items"] if item["trace_id"] == material_id)["status"] == "verified"
    overview = client.get(f"/api/projects/{PROJECT_ID}/overview")
    assert overview.json()["review_progress"]["issue_decisions_completed"] == 2


def test_command_validation_and_project_boundaries(command_client: tuple[TestClient, Path, str]) -> None:
    client, _, material_id = command_client
    review_url = f"/api/projects/{PROJECT_ID}/review"
    token = client.get(review_url).headers["etag"]
    assert client.patch(f"{review_url}/issues/{TR003_ISSUE}", json={"decision": "accepted", "use_machine_location": True}).status_code == 428
    assert client.patch(f"{review_url}/issues/FAI-NOT-IN-PROJECT", headers={"If-Match": token}, json={"decision": "accepted", "use_machine_location": True}).status_code == 404
    assert client.post(f"{review_url}/issues/FAI-NOT-IN-PROJECT/risk-acceptances", headers={"If-Match": token}, json={"reason": "invalid linkage", "scope": "fixture", "confirmed": True}).status_code == 404
    assert client.patch(f"{review_url}/issues/{TR003_ISSUE}", headers={"If-Match": token}, json={"decision": "modified", "use_machine_location": True}).status_code == 422
    assert client.patch(f"{review_url}/facts/FACT-013", headers={"If-Match": token}, json={"confirmation_status": "confirmed"}).status_code == 422
    assert client.patch(f"{review_url}/facts/FACT-013", headers={"If-Match": token}, json={"confirmation_status": "invented"}).status_code == 422
    assert client.patch(f"{review_url}/materials/{material_id}", headers={"If-Match": token}, json={"verification_status": "invented"}).status_code == 422
    query_not_executed = next(item["trace_id"] for item in client.get(review_url).json()["material_verifications"] if item["trace_id"] != material_id and item["upstream_status"] == "query_not_executed")
    unavailable = client.patch(f"{review_url}/materials/{query_not_executed}", headers={"If-Match": token}, json={"verification_status": "not_provided", "verification_note": "not queried"})
    assert unavailable.status_code == 422
    assert "尚未查询不是材料不存在" in unavailable.json()["error"]["message"]
