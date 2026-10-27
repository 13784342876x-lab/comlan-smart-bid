from __future__ import annotations

from pathlib import Path
from typing import Any

from app.technical_fulltext_audit_finalizer import TechnicalFulltextAuditFinalizer
from app.technical_fulltext_auditor import TechnicalFulltextAuditor
from app.technical_fulltext_human_review import TechnicalFulltextHumanReviewService
from app.technical_fulltext_hygiene import TechnicalFulltextHygiene
from app.technical_fulltext_review_package import (
    TechnicalFulltextReviewPackageAssembler,
)
from app.technical_fulltext_scoring_assessment import (
    TechnicalFulltextScoringAssessor,
)
from app.technical_fulltext_semantic_audit import TechnicalFulltextSemanticAuditor


def review_stage10_revision(
    *,
    fulltext_json_path: str | Path,
    semantic_llm_client: Any = None,
) -> dict[str, Any]:
    """Run the existing Stage11/12 review path for one committed Stage10 artifact."""
    hygiene = TechnicalFulltextHygiene().run(
        fulltext_json_path=fulltext_json_path
    )
    deterministic = TechnicalFulltextAuditor().audit(
        fulltext_json_path=hygiene["outputs"]["technical_fulltext_cleaned_json"]
    )
    semantic = TechnicalFulltextSemanticAuditor(
        llm_client=semantic_llm_client
    ).audit(
        audit_json_path=deterministic["outputs"]["technical_fulltext_audit_json"]
    )
    scoring = TechnicalFulltextScoringAssessor().assess(
        audit_json_path=semantic["outputs"]["technical_fulltext_audit_json"]
    )
    finalization = TechnicalFulltextAuditFinalizer().finalize(
        audit_json_path=scoring["outputs"]["technical_fulltext_audit_json"]
    )
    review_package = TechnicalFulltextReviewPackageAssembler().assemble(
        audit_json_path=finalization["outputs"]["technical_fulltext_audit_json"]
    )
    human_review = TechnicalFulltextHumanReviewService().initialize(
        review_package_json_path=review_package["outputs"][
            "technical_fulltext_review_package_json"
        ]
    )
    return {
        "status": "human_review_pending",
        "hygiene": hygiene,
        "deterministic_audit": deterministic,
        "semantic_audit": semantic,
        "scoring_assessment": scoring,
        "audit_finalization": finalization,
        "review_package": review_package,
        "human_review": human_review,
    }
