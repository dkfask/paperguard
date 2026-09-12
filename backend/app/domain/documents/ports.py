"""Small ports for introducing the document domain without changing storage."""

from __future__ import annotations

from typing import Protocol

from app.domain.documents.schemas import DocumentTemplate, TemplateVersion
from app.domain.documents.snapshots import ProjectSnapshot
from app.domain.documents.specs import SpecSet, SpecVersion


TemplateAggregate = tuple[DocumentTemplate, TemplateVersion]
SpecAggregate = tuple[SpecSet, SpecVersion]


class TemplateRepository(Protocol):
    def get(self, template_id: str) -> TemplateAggregate | None: ...

    def list(self) -> list[TemplateAggregate]: ...


class SpecRepository(Protocol):
    def get(self, spec_id: str) -> SpecAggregate | None: ...

    def list(self) -> list[SpecAggregate]: ...


class ProjectSnapshotRepository(Protocol):
    def get(self, project_id: str) -> ProjectSnapshot | None: ...


__all__ = ["ProjectSnapshotRepository", "SpecRepository", "TemplateAggregate", "TemplateRepository"]
