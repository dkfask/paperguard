"""Recheck frozen master output made before the visibility rules existed."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile

import pytest
from docx import Document
from docx.shared import RGBColor
from lxml import etree

from app.api import workflow
from app.core.extract.xml_utils import NS, qn
from app.db import Store
from app.services.docx_output_check import (
    DocxOutputCheckError,
    output_check_identity,
    verify_report_binding,
)
from app.services.project_versions import project_attachment_refs, project_content_hash
from tests.test_master_cover_label_visibility import _recompile
from tests.test_source_template_profile import inspect, master, rendered, rewrite


@pytest.fixture(params=["visible", "hidden", "hidden-cover-label", "shaded", "black-highlight"])
def historical_artifact(request, master, tmp_path, monkeypatch):
    source, template, project, _ = master
    if request.param in {"hidden-cover-label", "shaded", "black-highlight"}:
        document = Document(source)
        if request.param == "hidden-cover-label":
            document.tables[0].cell(0, 0).paragraphs[0].runs[0].font.hidden = True
        else:
            document.styles["Normal"].font.color.rgb = RGBColor(0, 0, 0)
        document.save(source)
        master = _recompile(master)
        source, template, project, _ = master
    if request.param == "black-highlight":
        project.sections[-1].paragraphs[0].highlight = "black"
    project.template_snapshot = template.model_dump(mode="json")
    data = rendered(master, tmp_path)

    if request.param == "hidden":
        block_id = project.sections[-1].paragraphs[0].id

        def hide_body(root):
            runs = root.xpath(
                ".//w:sdt[w:sdtPr/w:tag/@w:val=$tag]/w:sdtContent//w:r[w:t]",
                namespaces=NS, tag="pg-block:" + block_id,
            )
            assert len(runs) == 1
            rpr = runs[0].find(qn("w:rPr"))
            assert rpr is not None
            visibility = rpr.find(qn("w:vanish"))
            if visibility is None:
                visibility = etree.SubElement(rpr, qn("w:vanish"))
            visibility.set(qn("w:val"), "1")

        data = rewrite(data, "word/document.xml", hide_body)
    elif request.param == "shaded":
        block_id = project.sections[-1].paragraphs[0].id

        def shade_body(root):
            runs = root.xpath(
                ".//w:sdt[w:sdtPr/w:tag/@w:val=$tag]/w:sdtContent//w:r[w:t]",
                namespaces=NS, tag="pg-block:" + block_id,
            )
            assert len(runs) == 1
            properties = runs[0].find(qn("w:rPr"))
            shading = etree.SubElement(properties, qn("w:shd"))
            for name, value in (("val", "clear"), ("color", "auto"), ("fill", "000000")):
                shading.set(qn("w:" + name), value)

        data = rewrite(data, "word/document.xml", shade_body)

    current_report = inspect(master, data)
    expected_report_status = "fail" if request.param in {
        "hidden", "hidden-cover-label", "shaded", "black-highlight",
    } else "pass"
    assert current_report["status"] == expected_report_status
    if request.param != "visible":
        expected_rule = {"hidden": "master.content_visibility",
                         "hidden-cover-label": "master.field_label_visibility",
                         "shaded": "master.content_direct_shading",
                         "black-highlight": "master.highlight_color_distinct"}[request.param]
        assert any(item["rule"] == expected_rule and item["status"] == "fail"
                   for item in current_report["checks"])

    # Model a frozen report from before these rules: retain the actual output
    # and every other check, changing only the missing visibility capability.
    old_report = copy.deepcopy(current_report)
    old_report.pop("visibility_check_version")
    old_report.pop("cover_label_visibility_check_version", None)
    old_report.pop("direct_shading_check_version", None)
    old_report.pop("highlight_color_check_version", None)
    old_report["checks"] = [item for item in old_report["checks"] if item["rule"] not in {
        "master.content_visibility", "master.heading_visibility", "master.field_label_visibility",
        "master.content_direct_shading", "master.highlight_color_distinct",
    }]
    assert all(item["status"] == "pass" for item in old_report["checks"])
    old_report["status"] = "pass"
    old_report["check_count"] = old_report["passed_count"] = len(old_report["checks"])
    old_report["inspector_version"] = "docx-output-check/9-master"
    for key in ("acceptance_contract", "format_accuracy", "hard_blockers", "acceptance_status"):
        old_report.pop(key, None)
    for key in ("rule_accuracy", "failed_checks", "unverifiable_checks", "excluded_checks"):
        old_report.pop(key, None)
    for item in old_report["checks"]:
        item.pop("check_id", None)
        item.pop("classification", None)
    assert not verify_report_binding(old_report, data)
    assert verify_report_binding(old_report, data, allow_previous_inspector=True)

    artifact_path = tmp_path / "historical.docx"
    artifact_path.write_bytes(data)
    monkeypatch.setattr(workflow, "_candidate_artifact_path", lambda _candidate_id: artifact_path)
    monkeypatch.setattr(workflow, "resolve_template_source", lambda _name: source)
    version = {
        "id": "historical-version",
        "project_id": project.id,
        "content_hash": project_content_hash(project),
        "content_snapshot": project.model_dump(mode="json"),
        "attachment_refs": project_attachment_refs(project),
    }
    candidate = {
        "id": "historical-candidate",
        "project_id": project.id,
        "project_version_id": version["id"],
        "content_hash": version["content_hash"],
        "artifact": {"docx_sha256": hashlib.sha256(data).hexdigest(), "output_check": old_report},
    }
    return request.param, source, data, old_report, candidate, version


def test_historical_candidate_is_rechecked_with_current_visibility_rules(historical_artifact):
    case, _source, data, _old_report, candidate, version = historical_artifact
    if case in {"hidden", "hidden-cover-label", "shaded", "black-highlight"}:
        with pytest.raises(DocxOutputCheckError):
            workflow._verified_candidate_artifact(None, candidate, version)
    else:
        checked, report = workflow._verified_candidate_artifact(None, candidate, version)
        assert checked == data
        assert report["visibility_check_version"] == "word-run-visibility/1"


def test_historical_archive_is_rechecked_with_current_visibility_rules(historical_artifact, tmp_path, monkeypatch):
    case, source, data, old_report, candidate, version = historical_artifact
    store = Store(tmp_path / "historical.db")
    submission = {"id": "historical-submission", "project_version_id": version["id"],
                  "candidate_id": candidate["id"]}
    for kind, record in (("project_version", version), ("candidate", candidate), ("submission", submission)):
        assert store.save_workflow_record(kind, record)

    manifest = {
        "archive_id": "historical-archive", "submission_id": submission["id"],
        "output_check": old_report, "output_check_identity": output_check_identity(old_report),
        "files": [{"name": "final.docx", "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}],
    }
    archive_path = tmp_path / "historical-archive.zip"
    with zipfile.ZipFile(archive_path, "w") as bundle:
        bundle.writestr("final.docx", data)
        bundle.writestr("manifest.json", json.dumps(manifest))
    archive = {
        "id": manifest["archive_id"], "status": "succeeded", "submission_id": submission["id"],
        "file_path": str(archive_path), "manifest_hash": workflow._hash(manifest),
        "source_template_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    monkeypatch.setattr(workflow, "_archive_dir", lambda: tmp_path)

    if case in {"hidden", "hidden-cover-label", "shaded", "black-highlight"}:
        with pytest.raises(DocxOutputCheckError):
            workflow._verified_archive_output(store, archive["id"], archive)
    else:
        _archive_bytes, checked, report = workflow._verified_archive_output(store, archive["id"], archive)
        assert checked == data
        assert report["visibility_check_version"] == "word-run-visibility/1"


@pytest.mark.parametrize("historical_artifact", ["visible"], indirect=True)
def test_previous_master_report_rejects_unknown_version_and_changed_files(historical_artifact, tmp_path):
    _case, source, data, old_report, candidate, version = historical_artifact
    artifact_path = tmp_path / "historical.docx"
    checked, fresh = workflow._verified_candidate_artifact(None, candidate, version)
    assert checked == data
    assert fresh["inspector_version"] == "docx-output-check/10-master"
    assert old_report["inspector_version"] == "docx-output-check/9-master"

    original_profile = old_report["source_profile_sha256"]
    old_report["source_profile_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        workflow._verified_candidate_artifact(None, candidate, version)
    old_report["source_profile_sha256"] = original_profile

    old_report["inspector_version"] = "docx-output-check/7-master"
    with pytest.raises(ValueError):
        workflow._verified_candidate_artifact(None, candidate, version)
    old_report["inspector_version"] = "docx-output-check/9-master"

    artifact_path.write_bytes(data + b"tampered")
    with pytest.raises(ValueError):
        workflow._verified_candidate_artifact(None, candidate, version)
    artifact_path.write_bytes(data)

    original_source = source.read_bytes()
    source.write_bytes(original_source + b"tampered")
    with pytest.raises(ValueError):
        workflow._verified_candidate_artifact(None, candidate, version)
    source.write_bytes(original_source)
    assert workflow._verified_candidate_artifact(None, candidate, version)[0] == data
