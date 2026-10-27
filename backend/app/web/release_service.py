from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Callable
from typing import Any

from app.technical_fulltext_composer import sha256_file
from app.technical_fulltext_release import (
    TechnicalFulltextReleaseBlocked,
    TechnicalFulltextReleaseError,
    TechnicalFulltextReleaseStale,
    evaluate_release_gate,
    generate_release_artifacts,
    inspect_release_manifest,
)
from app.web.dtos import RequiredSubmissionItemDTO, ReleaseArtifactDTO, ReleaseStatusDTO
from app.web.project_resolver import ProjectArtifacts, ReadOnlyProjectResolver


class ReleaseArtifactNotFound(TechnicalFulltextReleaseError):
    pass


class TechnicalFulltextReleaseService:
    def __init__(
        self,
        resolver: ReadOnlyProjectResolver,
        required_submission_query: Callable[[str], list[RequiredSubmissionItemDTO]] | None = None,
    ) -> None:
        self.resolver = resolver
        self.required_submission_query = required_submission_query

    def status(self, project_id: str) -> ReleaseStatusDTO:
        artifacts = self.resolver.resolve(project_id)
        gate = self._gate(artifacts)
        manifest = inspect_release_manifest(
            artifact_root=artifacts.root,
            expected_output_key=gate["output_key"],
            expected_cleaned_sha256=gate["cleaned_content_sha256"],
        )
        return self._projection(
            project_id,
            gate,
            manifest if gate["releaseable"] else None,
            self._required_submission_items(project_id),
        )

    def publish(self, project_id: str) -> ReleaseStatusDTO:
        artifacts = self.resolver.resolve(project_id)
        gate = self._gate(artifacts)
        if not gate["releaseable"]:
            raise TechnicalFulltextReleaseBlocked(gate["blocking_reasons"])
        root = artifacts.root.resolve()
        output_key = gate["output_key"]
        cleaned_content_sha = gate["cleaned_content_sha256"]
        finalization_sha = gate["finalization_sha256"]

        def stale_check() -> None:
            current = self.resolver.resolve(project_id)
            current_gate = self._gate(current)
            if (
                current.root.resolve() != root
                or current_gate["output_key"] != output_key
                or current_gate["cleaned_content_sha256"] != cleaned_content_sha
                or current_gate["finalization_sha256"] != finalization_sha
                or not current_gate["releaseable"]
            ):
                raise TechnicalFulltextReleaseStale(
                    "current artifact or finalized review changed during release"
                )

        manifest = generate_release_artifacts(
            cleaned_fulltext_path=artifacts.cleaned_fulltext_json,
            artifact_root=root,
            project_name=self._project_name(project_id),
            stale_check=stale_check,
        )
        return self._projection(
            project_id,
            gate,
            manifest,
            self._required_submission_items(project_id),
        )

    def download(self, project_id: str, kind: str) -> tuple[Path, str]:
        status = self.status(project_id)
        artifact = status.docx if kind == "docx" else status.pdf
        if artifact is None:
            raise ReleaseArtifactNotFound(
                f"current project has no published {kind.upper()} artifact"
            )
        root = self.resolver.resolve(project_id).root.resolve()
        manifest = json.loads(
            (root / "fulltext" / "release" / "technical_fulltext.release.json").read_text(
                encoding="utf-8"
            )
        )
        path = (root / manifest[kind]["path"]).resolve()
        path.relative_to(root)
        if sha256_file(path) != artifact.sha256:
            raise TechnicalFulltextReleaseError(f"release {kind} changed after validation")
        return path, artifact.filename

    @staticmethod
    def _gate(artifacts: ProjectArtifacts) -> dict[str, Any]:
        return evaluate_release_gate(
            cleaned_fulltext_path=artifacts.cleaned_fulltext_json,
            review_package_path=artifacts.review_package_json,
            human_review_path=artifacts.human_review_json,
            finalization_path=artifacts.finalization_json,
        )

    def _project_name(self, project_id: str) -> str:
        manifest_path = (
            self.resolver.outputs_root / "web_projects" / project_id / "project.json"
        )
        if not manifest_path.is_file():
            return project_id
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return str(manifest.get("project_name") or project_id)

    def _required_submission_items(self, project_id: str) -> list[RequiredSubmissionItemDTO]:
        if self.required_submission_query is None:
            return []
        return self.required_submission_query(project_id)

    @staticmethod
    def _projection(
        project_id: str,
        gate: dict[str, Any],
        manifest: dict[str, Any] | None,
        required_submission_items: list[RequiredSubmissionItemDTO],
    ) -> ReleaseStatusDTO:
        def artifact(kind: str) -> ReleaseArtifactDTO | None:
            if manifest is None:
                return None
            value = manifest[kind]
            return ReleaseArtifactDTO(
                filename=value["download_filename"],
                sha256=value["sha256"],
                size_bytes=value["validation"]["size_bytes"],
            )

        return ReleaseStatusDTO(
            project_id=project_id,
            current_output_key=gate["output_key"],
            releaseable=gate["releaseable"],
            blocking_reasons=gate["blocking_reasons"],
            status=(
                "published"
                if manifest is not None
                else "ready"
                if gate["releaseable"]
                else "blocked"
            ),
            generated_at=manifest.get("generated_at") if manifest else None,
            docx=artifact("docx"),
            pdf=artifact("pdf"),
            required_submission_items=required_submission_items,
        )
