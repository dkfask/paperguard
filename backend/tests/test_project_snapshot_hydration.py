"""Legacy snapshot hydration must preserve concurrent edits and report binding."""
import pytest
from fastapi import HTTPException

from app.api import projects as api
from app.db import Store
from app.models.project import ParagraphBlock, ProjectSection, WritingProject
from app.services.projects import default_template
from app.services.project_versions import project_content_hash


def _setup(tmp_path):
    store = Store(tmp_path / "snapshots.db")
    template = default_template()
    store.save_template(template)
    project = WritingProject(id="legacy", template_id=template.id, sections=[
        ProjectSection(key="body", type="body", title="正文", paragraphs=[ParagraphBlock(id="p", text="原稿")]),
    ])
    store.save_project(project)
    return store, project, template


def test_template_hydration_rejects_stale_manuscript_instead_of_overwriting(monkeypatch, tmp_path):
    store, stale, _ = _setup(tmp_path)
    hydrate = api.hydrate_snapshot_format_overrides

    def concurrent_edit(snapshot):
        current = store.get_project(stale.id)
        current.sections[0].paragraphs[0].text = "另一窗口的新正文"
        assert store.update_project_if_revision(current, current.revision)
        return hydrate(snapshot)

    monkeypatch.setattr(api, "hydrate_snapshot_format_overrides", concurrent_edit)
    with pytest.raises(HTTPException) as error:
        api._project_template(stale, store)
    assert error.value.status_code == 409
    assert store.get_project(stale.id).sections[0].paragraphs[0].text == "另一窗口的新正文"


def test_requirement_hydration_checks_content_hash_even_without_revision_change(monkeypatch, tmp_path):
    store, stale, template = _setup(tmp_path)

    def concurrent_policy_change(*_args, **_kwargs):
        current = store.get_project(stale.id)
        current.waivers = [{"id": "new-waiver", "status": "approved"}]
        store.save_project(current)
        return {"content_hash": "old-requirements"}

    monkeypatch.setattr(api, "build_requirement_snapshot", concurrent_policy_change)
    with pytest.raises(HTTPException) as error:
        api._ensure_requirement_snapshot(stale, template, store)
    assert error.value.status_code == 409
    current = store.get_project(stale.id)
    assert current.waivers[0]["id"] == "new-waiver"
    assert not current.requirement_snapshot


def test_snapshot_hydration_preserves_check_metadata_and_changes_content_binding(tmp_path):
    store, project, template = _setup(tmp_path)
    original_hash = project_content_hash(project)
    store.update_project_check_runs(project.id, ["run-new"], project.revision)
    saved = store.update_project_snapshots(
        project.id, project.revision, original_hash,
        template_snapshot=template.model_dump(mode="json"), requirement_snapshot={"content_hash": "requirements"},
    )
    assert saved is not None
    assert saved.check_run_ids == ["run-new"]
    assert saved.revision == project.revision
    assert project_content_hash(saved) != original_hash
    final = store.update_project_check_runs(project.id, ["run-final"], project.revision)
    assert final.template_snapshot == saved.template_snapshot
    assert final.requirement_snapshot == saved.requirement_snapshot
    assert project_content_hash(final) == project_content_hash(saved)


def test_legacy_table_without_borders_retains_hash_after_model_hydration():
    from app.models.project import BlockType, TableBlock, TableBorders
    from app.services.table_borders import default_table_borders

    project = WritingProject(id="legacy-table", template_id="tpl", sections=[
        ProjectSection(key="body", type="body", title="正文", paragraphs=[
            ParagraphBlock(id="table", type=BlockType.TABLE,
                           table=TableBlock(rows=1, cols=1, cells=[["数据"]])),
        ]),
    ])
    legacy = project.model_dump(mode="json")
    del legacy["sections"][0]["paragraphs"][0]["table"]["borders"]
    assert project_content_hash(WritingProject.model_validate(legacy)) == project_content_hash(legacy)
    current_snapshot = project.model_dump(mode="json")
    assert project_content_hash(current_snapshot) == project_content_hash(legacy)
    assert "borders" in current_snapshot["sections"][0]["paragraphs"][0]["table"]
    bordered = project.model_copy(deep=True)
    bordered.sections[0].paragraphs[0].table.borders = TableBorders.model_validate(default_table_borders())
    assert project_content_hash(bordered) != project_content_hash(legacy)


def test_table_default_hash_normalization_does_not_remove_template_content():
    project = WritingProject(id="template-hash", template_id="tpl", template_snapshot={
        "template_table_metadata": {"rows": 1, "cols": 1, "cells": [["x"]], "borders": None},
    })
    changed = project.model_copy(deep=True)
    del changed.template_snapshot["template_table_metadata"]["borders"]
    assert project_content_hash(project) != project_content_hash(changed)
