"""Version-frozen project and package contracts for legacy compatibility."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.documents.hashing import canonical_hash, project_content_hash
from app.domain.documents.mappers import project_template_version_id
from app.domain.documents.schemas import DocumentPackage
from app.models.project import WritingProject


class ProjectSnapshot(BaseModel):
    """Immutable execution input assembled from an existing WritingProject."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    template_version_id: str = Field(min_length=1)
    spec_version_id: str = Field(min_length=1)
    renderer_version: str = Field(min_length=1)
    rule_engine_version: str = Field(min_length=1)
    content_hash: str = ""
    project_data: dict[str, Any] = Field(default_factory=dict)
    template_snapshot: dict[str, Any] | None = None
    template_source_snapshot: dict[str, Any] | None = None
    requirement_snapshot: dict[str, Any] | None = None
    created_by: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_legacy_project(
        cls,
        project: WritingProject,
        *,
        renderer_version: str = "legacy-docx-v1",
        rule_engine_version: str = "legacy-check-v1",
        snapshot_id: str | None = None,
    ) -> "ProjectSnapshot":
        project_data = project.model_dump(mode="json")
        template_version = project_template_version_id(project)
        requirement = project.requirement_snapshot or {}
        requirement_hash = str(requirement.get("content_hash") or "legacy")
        spec_version_id = f"legacy-spec@{requirement_hash}"
        package_id = f"legacy-package:{project.template_id}:{spec_version_id}"
        return cls(
            id=snapshot_id or f"snapshot:{project.id}:{project.revision}",
            project_id=project.id,
            package_id=package_id,
            template_version_id=template_version,
            spec_version_id=spec_version_id,
            renderer_version=renderer_version,
            rule_engine_version=rule_engine_version,
            content_hash=project_content_hash(project_data),
            project_data=project_data,
            template_snapshot=project.template_snapshot,
            template_source_snapshot=project.template_source_snapshot,
            requirement_snapshot=project.requirement_snapshot,
            created_by=project.owner_id,
            created_at=project.updated_at or project.created_at,
            metadata={"source": "WritingProject"},
        )


def package_from_snapshot(snapshot: ProjectSnapshot) -> DocumentPackage:
    """Build a deterministic package reference without persisting a new package."""
    return DocumentPackage(
        id=snapshot.package_id,
        document_type_id="thesis",
        template_version_id=snapshot.template_version_id,
        spec_version_id=snapshot.spec_version_id,
        renderer_version=snapshot.renderer_version,
        rule_engine_version=snapshot.rule_engine_version,
        status="published",
        metadata={"source": "legacy-project-snapshot", "content_hash": snapshot.content_hash},
    )


__all__ = ["ProjectSnapshot", "package_from_snapshot"]
