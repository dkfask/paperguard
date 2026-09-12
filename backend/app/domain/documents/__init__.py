"""Phase 1 document-domain contracts and legacy adapters."""

from app.domain.documents.adapters import (
    LegacyStoreProjectSnapshotRepository,
    LegacyStoreSpecRepository,
    LegacyStoreTemplateRepository,
)
from app.domain.documents.mappers import (
    domain_to_paper_template,
    paper_template_to_domain,
)
from app.domain.documents.schemas import (
    DocumentPackage,
    DocumentProject,
    DocumentTemplate,
    DocumentType,
    ContentBlock,
    StyleDefinition,
    TemplateAsset,
    TemplateDefinition,
    TemplateField,
    TemplateSection,
    TemplateVersion,
    ValidationFinding,
    ValidationRun,
)
from app.domain.documents.snapshots import ProjectSnapshot, package_from_snapshot
from app.domain.documents.specs import SpecRule, SpecSet, SpecVersion, requirement_set_to_spec
from app.domain.documents.hashing import document_project_content_hash

__all__ = [
    "DocumentPackage",
    "DocumentProject",
    "DocumentTemplate",
    "DocumentType",
    "ContentBlock",
    "LegacyStoreProjectSnapshotRepository",
    "LegacyStoreSpecRepository",
    "LegacyStoreTemplateRepository",
    "ProjectSnapshot",
    "SpecRule",
    "SpecSet",
    "SpecVersion",
    "StyleDefinition",
    "TemplateAsset",
    "TemplateDefinition",
    "TemplateField",
    "TemplateSection",
    "TemplateVersion",
    "ValidationFinding",
    "ValidationRun",
    "domain_to_paper_template",
    "document_project_content_hash",
    "package_from_snapshot",
    "paper_template_to_domain",
    "requirement_set_to_spec",
]