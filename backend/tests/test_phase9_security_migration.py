"""Phase 9 security boundaries and migration evidence."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from app.db import Store
from app.domain.documents.schemas import TemplateAsset, TemplateDefinition
from app.services.document_access import (
    audit_document_access_denied,
    document_context_visible,
)
from app.services.package_engine import PackagePublishError, publish_package_candidates
from app.services.template_engine import TemplateParseCandidate, parse_docx_template
from app.services.template_source import resolve_template_source
from app.services.spec_engine import SpecParseCandidate


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "templates" / "minimal.docx"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_regular_users_do_not_match_values_across_scope_keys(tmp_path: Path) -> None:
    store = Store(tmp_path / "scope.db")
    user = {"id": "student-1", "role": "student", "organization_context": {"school_id": "scope-a"}}

    assert document_context_visible(store, user, {"school_id": "scope-a"})
    assert not document_context_visible(store, user, {"college_id": "scope-a"})
    assert document_context_visible(store, user, {})


def test_denied_generic_access_is_content_free_and_audited(tmp_path: Path) -> None:
    store = Store(tmp_path / "audit.db")
    user = {"id": "student-2", "role": "student", "organization_context": {"school_id": "school-a"}}
    audit_document_access_denied(
        store,
        user,
        resource_type="document_project",
        resource_id="docproj-secret",
        context={"school_id": "school-b"},
    )

    event = store.list_audit_events(limit=1)[0]
    assert event["action"] == "document.access.denied"
    assert event["result"] == "failure"
    assert event["reason"] == "access_denied"
    assert "secret" not in event["reason"]


def test_template_source_resolution_rejects_symlink_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_dir = tmp_path / "templates"
    source_dir.mkdir()
    outside = tmp_path / "outside.docx"
    outside.write_bytes(FIXTURE.read_bytes())
    (source_dir / "safe.docx").symlink_to(outside)
    monkeypatch.setenv("PAPERGUARD_TEMPLATE_DIR", str(source_dir))

    assert resolve_template_source("safe.docx") is None
    assert resolve_template_source("../outside.docx") is None


def test_package_publish_rechecks_template_asset_integrity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERGUARD_TEMPLATE_DIR", str(tmp_path))
    source = tmp_path / "source.docx"
    source.write_bytes(FIXTURE.read_bytes())
    data = source.read_bytes()
    candidate = parse_docx_template(
        source,
        candidate_id="candidate-security",
        template_id="template-security",
        filename="source.docx",
        source_asset=TemplateAsset(
            id="candidate-security:source",
            storage_key="source.docx",
            content_type=DOCX_TYPE,
            size_bytes=len(data),
            checksum=hashlib.sha256(b"tampered").hexdigest(),
            purpose="template-source",
            scan_status="validated",
        ),
    ).model_copy(update={"status": "confirmed"})
    spec = SpecParseCandidate(
        candidate_id="spec-security",
        spec_set_id="spec-security",
        name="Security spec",
        status="confirmed",
    )

    with pytest.raises(PackagePublishError, match="checksum"):
        publish_package_candidates(
            candidate,
            spec,
            document_type_id="generic",
            published_by="manager-1",
        )


def test_phase9_schema_ledger_and_indexes_are_applied(tmp_path: Path) -> None:
    store = Store(tmp_path / "migration.db")
    versions = {item["version"] for item in store.list_schema_migrations()}
    assert "0007_document_scope_indexes" in versions

    with sqlite3.connect(tmp_path / "migration.db") as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(document_projects)")}
        workflow_indexes = {row[1] for row in conn.execute("PRAGMA index_list(workflow_records)")}
    assert "idx_document_projects_package_owner" in indexes
    assert "idx_workflow_records_document_project" in workflow_indexes
