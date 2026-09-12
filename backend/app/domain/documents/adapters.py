"""Read adapters over the existing SQLite JSON Store."""

from __future__ import annotations

from app.db import Store
from app.domain.documents.mappers import paper_template_to_domain
from app.domain.documents.ports import SpecAggregate, TemplateAggregate
from app.domain.documents.snapshots import ProjectSnapshot
from app.domain.documents.specs import requirement_set_to_spec


class LegacyStoreTemplateRepository:
    """Expose PaperTemplate rows as generic domain template aggregates."""

    def __init__(self, store: Store):
        self.store = store

    def get(self, template_id: str) -> TemplateAggregate | None:
        template = self.store.get_template(template_id)
        return paper_template_to_domain(template) if template else None

    def list(self) -> list[TemplateAggregate]:
        return [paper_template_to_domain(template) for template in self.store.list_templates()]


class LegacyStoreSpecRepository:
    """Expose RequirementSet rows as generic specification aggregates."""

    def __init__(self, store: Store):
        self.store = store

    def get(self, spec_id: str) -> SpecAggregate | None:
        requirement_set = self.store.get_requirement_set(spec_id)
        return requirement_set_to_spec(requirement_set) if requirement_set else None

    def list(self) -> list[SpecAggregate]:
        return [requirement_set_to_spec(item) for item in self.store.list_requirement_sets()]


class LegacyStoreProjectSnapshotRepository:
    """Build a read-only project snapshot from the existing project row."""

    def __init__(self, store: Store):
        self.store = store

    def get(self, project_id: str) -> ProjectSnapshot | None:
        project = self.store.get_project(project_id)
        return ProjectSnapshot.from_legacy_project(project) if project else None


__all__ = [
    "LegacyStoreProjectSnapshotRepository",
    "LegacyStoreSpecRepository",
    "LegacyStoreTemplateRepository",
]
