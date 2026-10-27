from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from config import BACKEND_ROOT


class ProjectNotFoundError(LookupError):
    pass


class ProjectArtifactReferenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectArtifacts:
    project_id: str
    root: Path
    fulltext_json: Path
    commit_json: Path
    cleaned_fulltext_json: Path
    audit_json: Path
    scoring_json: Path
    review_package_json: Path
    human_review_json: Path
    finalization_json: Path | None


class ReadOnlyProjectResolver:
    """Discover canonical Stage10/11/12 project outputs without mutating them."""

    def __init__(self, outputs_root: str | Path | None = None) -> None:
        self.outputs_root = (
            Path(outputs_root).expanduser().resolve()
            if outputs_root is not None
            else (BACKEND_ROOT / "outputs").resolve()
        )

    def list_projects(self) -> list[ProjectArtifacts]:
        if not self.outputs_root.is_dir():
            return []
        projects: list[ProjectArtifacts] = self._registered_projects()
        registered_ids = {item.project_id for item in projects}
        for child in sorted(self.outputs_root.iterdir(), key=lambda path: path.name):
            if not child.is_dir():
                continue
            candidate = self._candidate(child)
            if candidate is not None and candidate.project_id not in registered_ids:
                projects.append(candidate)
        return projects

    def _registered_projects(self) -> list[ProjectArtifacts]:
        """Map Web project IDs to the canonical fulltext output of their current run."""
        registry = self.outputs_root / "web_projects"
        values: list[ProjectArtifacts] = []
        if not registry.is_dir():
            return values
        for manifest_path in sorted(registry.glob("*/project.json")):
            candidate = self._registered_candidate(manifest_path)
            if candidate is not None:
                values.append(candidate)
        return values

    def _registered_candidate(self, manifest_path: Path) -> ProjectArtifacts | None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            project_id = str(manifest.get("project_id") or "")
            if not project_id or project_id != manifest_path.parent.name:
                return None
            reference = manifest.get("current_artifact_set")
            if reference is not None:
                if not isinstance(reference, dict):
                    raise ProjectArtifactReferenceError(
                        f"current artifact-set reference is invalid for project {project_id}"
                    )
                return self.resolve_artifact_set(
                    project_id=project_id,
                    fulltext_output_dir=str(
                        reference.get("fulltext_output_dir") or ""
                    ),
                    output_key=str(reference.get("output_key") or ""),
                )
            run_id = str(manifest.get("current_run_id") or "")
            if not run_id:
                return None
            state_path = self.outputs_root / "bid_runs" / run_id / "pipeline_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("run_id") != run_id:
                return None
            routing = state.get("routing") or {}
            root = (
                Path(str(routing.get("fulltext_output_dir") or ""))
                / str(routing.get("fulltext_output_key") or "")
            ).resolve()
            root.relative_to(self.outputs_root)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
        return self._candidate(root, project_id=project_id)

    def resolve_artifact_set(
        self,
        *,
        project_id: str,
        fulltext_output_dir: str | Path,
        output_key: str,
    ) -> ProjectArtifacts:
        if not output_key or Path(output_key).name != output_key:
            raise ProjectArtifactReferenceError(
                f"current artifact-set output key is invalid for project {project_id}"
            )
        try:
            root = (Path(fulltext_output_dir).expanduser() / output_key).resolve()
            root.relative_to(self.outputs_root)
        except (OSError, ValueError) as exc:
            raise ProjectArtifactReferenceError(
                f"current artifact-set root is invalid for project {project_id}"
            ) from exc
        candidate = self._candidate(root, project_id=project_id)
        if candidate is None:
            raise ProjectArtifactReferenceError(
                f"current artifact set is unavailable for project {project_id}"
            )
        return candidate

    def resolve(self, project_id: str) -> ProjectArtifacts:
        registry = self.outputs_root / "web_projects"
        try:
            manifest_path = (registry / project_id / "project.json").resolve()
            manifest_path.relative_to(registry.resolve())
        except ValueError:
            manifest_path = registry / "__invalid__"
        if manifest_path.is_file():
            project = self._registered_candidate(manifest_path)
            if project is not None:
                return project
            raise ProjectNotFoundError(f"project is not available: {project_id}")
        for child in self.outputs_root.iterdir() if self.outputs_root.is_dir() else ():
            if child.is_dir() and child.name == project_id:
                project = self._candidate(child)
                if project is not None:
                    return project
        raise ProjectNotFoundError(f"project is not available: {project_id}")

    @staticmethod
    def _candidate(root: Path, *, project_id: str | None = None) -> ProjectArtifacts | None:
        fulltext = root / "fulltext"
        review = fulltext / "review"
        finalization = review / "technical_fulltext.review_finalization.json"
        artifacts = ProjectArtifacts(
            project_id=project_id or root.name,
            root=root,
            fulltext_json=fulltext / "technical_fulltext.json",
            commit_json=fulltext / "technical_fulltext.commit.json",
            cleaned_fulltext_json=fulltext / "hygiene" / "technical_fulltext.cleaned.json",
            audit_json=fulltext / "audit" / "technical_fulltext.audit.json",
            scoring_json=fulltext / "audit" / "technical_fulltext.scoring_assessment.json",
            review_package_json=review / "technical_fulltext.review_package.json",
            human_review_json=review / "technical_fulltext.human_review.json",
            finalization_json=finalization if finalization.exists() else None,
        )
        required = (
            artifacts.fulltext_json,
            artifacts.commit_json,
            artifacts.cleaned_fulltext_json,
            artifacts.audit_json,
            artifacts.scoring_json,
            artifacts.review_package_json,
            artifacts.human_review_json,
        )
        return artifacts if all(path.is_file() for path in required) else None
