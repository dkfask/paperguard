"""Image paragraphs must not inherit a clipping fixed line height."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile

import pytest
from docx import Document
from docx.shared import Pt
from lxml import etree
from PIL import Image

from app.api import workflow
from app.core.extract.xml_utils import NS, qn
from app.db import Store
from app.models.project import ImageBlock, ParagraphBlock
from app.services.document_assets import document_upload_dir
from app.services.docx_output_check import DocxOutputCheckError, output_check_identity, verify_report_binding
from app.services.master_docx_check import _picture_line_allows_growth
from app.services.project_versions import project_attachment_refs, project_content_hash
from tests.test_master_cover_label_visibility import _recompile
from tests.test_source_template_profile import inspect, master, rendered, rewrite


@pytest.fixture
def image_case(master, tmp_path, monkeypatch):
    source = master[0]
    document = Document(source)
    document.styles["Normal"].paragraph_format.line_spacing = Pt(12)
    document.save(source)
    case = _recompile(master)
    monkeypatch.setenv("PAPERGUARD_DATA_DIR", str(tmp_path / "data"))
    uploads = document_upload_dir()
    uploads.mkdir(parents=True)
    image = uploads / (case[2].id + "-line.png")
    Image.new("RGB", (300, 200), "blue").save(image)
    case[2].sections[-1].paragraphs.append(ParagraphBlock(
        id="line-image", type="image", image=ImageBlock(
            url="/media/uploads/" + image.name, caption="示意图", width_cm=6,
        ),
    ))
    return case


def _paragraph(root, tag):
    matches = root.xpath(
        './/w:sdt[w:sdtPr/w:tag/@w:val=$tag]/w:sdtContent/w:p[1]',
        namespaces=NS, tag=tag,
    )
    assert len(matches) == 1
    return matches[0]


def _image_check(report):
    checks = [item for item in report["checks"]
              if item["rule"] == "master.image_line_spacing" and item["object"] == "line-image"]
    assert len(checks) == 1
    return checks[0]["status"]


def test_fixed_body_spacing_is_not_applied_as_fixed_image_height(image_case, tmp_path):
    source_hash = hashlib.sha256(image_case[0].read_bytes()).hexdigest()
    data = rendered(image_case, tmp_path)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    figure = _paragraph(root, "pg-block:line-image")
    body = _paragraph(root, "pg-block:" + image_case[2].sections[-1].paragraphs[0].id)
    figure_spacing = figure.find(f"{qn('w:pPr')}/{qn('w:spacing')}")
    body_spacing = body.find(f"{qn('w:pPr')}/{qn('w:spacing')}")
    assert figure_spacing.get(qn("w:lineRule")) == "atLeast"
    assert figure_spacing.get(qn("w:line")) == "240"
    assert body_spacing.get(qn("w:lineRule")) == "exact"
    assert body_spacing.get(qn("w:line")) == "240"
    assert hashlib.sha256(image_case[0].read_bytes()).hexdigest() == source_hash
    report = inspect(image_case, data)
    assert report["status"] == "pass", [item for item in report["checks"] if item["status"] != "pass"]
    assert _image_check(report) == "pass"


@pytest.mark.parametrize(("rule", "line", "expected"), [
    ("exact", "240", "fail"),
    ("exact", "2400", "fail"),
    ("auto", "120", "fail"),
    ("auto", "240", "pass"),
    ("atLeast", "240", "pass"),
    ("atLeast", "0", "fail"),
    ("atLeast", "-1", "fail"),
    ("bogus", "240", "fail"),
    ("auto", None, "fail"),
    (None, None, "fail"),  # Inherits the exact source style.
])
def test_image_spacing_is_rechecked_independently(image_case, tmp_path, rule, line, expected):
    data = rendered(image_case, tmp_path)

    def alter(root):
        paragraph = _paragraph(root, "pg-block:line-image")
        spacing = paragraph.find(f"{qn('w:pPr')}/{qn('w:spacing')}")
        for key, value in (("lineRule", rule), ("line", line)):
            if value is None:
                spacing.attrib.pop(qn("w:" + key), None)
            else:
                spacing.set(qn("w:" + key), value)

    changed = rewrite(data, "word/document.xml", alter)
    report = inspect(image_case, changed)
    assert _image_check(report) == expected
    if expected == "fail":
        assert report["status"] == "fail"


def test_duplicate_image_spacing_is_not_trusted(image_case, tmp_path):
    data = rendered(image_case, tmp_path)

    def alter(root):
        ppr = _paragraph(root, "pg-block:line-image").find(qn("w:pPr"))
        duplicate = etree.SubElement(ppr, qn("w:spacing"))
        duplicate.set(qn("w:lineRule"), "exact")
        duplicate.set(qn("w:line"), "240")

    report = inspect(image_case, rewrite(data, "word/document.xml", alter))
    assert _image_check(report) == "fail"
    assert report["status"] == "fail"


def test_safe_inherited_spacing_in_old_output_is_still_accepted(image_case, tmp_path):
    source = image_case[0]
    document = Document(source)
    document.styles["Normal"].paragraph_format.line_spacing = 1.5
    document.save(source)
    case = _recompile(image_case)
    data = rendered(case, tmp_path)

    def inherit(root):
        spacing = _paragraph(root, "pg-block:line-image").find(f"{qn('w:pPr')}/{qn('w:spacing')}")
        spacing.attrib.pop(qn("w:lineRule"), None)
        spacing.attrib.pop(qn("w:line"), None)

    report = inspect(case, rewrite(data, "word/document.xml", inherit))
    assert _image_check(report) == "pass"
    assert report["status"] == "pass", [item for item in report["checks"] if item["status"] != "pass"]


def _inherited_picture_spacing():
    """Build one paragraph whose safe line height comes from a parent style."""
    styles = etree.Element(qn("w:styles"), nsmap={"w": NS["w"]})
    normal = etree.SubElement(styles, qn("w:style"), {
        qn("w:type"): "paragraph", qn("w:styleId"): "Normal", qn("w:default"): "1",
    })
    normal_ppr = etree.SubElement(normal, qn("w:pPr"))
    etree.SubElement(normal_ppr, qn("w:spacing"), {
        qn("w:lineRule"): "auto", qn("w:line"): "240",
    })
    child = etree.SubElement(styles, qn("w:style"), {
        qn("w:type"): "paragraph", qn("w:styleId"): "Picture",
    })
    etree.SubElement(child, qn("w:basedOn"), {qn("w:val"): "Normal"})
    paragraph = etree.Element(qn("w:p"))
    ppr = etree.SubElement(paragraph, qn("w:pPr"))
    etree.SubElement(ppr, qn("w:pStyle"), {qn("w:val"): "Picture"})
    return paragraph, styles, normal, child


def test_picture_line_spacing_resolves_safe_inherited_and_overridden_rules():
    paragraph, styles, normal, child = _inherited_picture_spacing()
    assert _picture_line_allows_growth(paragraph, styles)

    # A fixed parent must not hide an explicit, expandable child rule.
    normal.find(f"{qn('w:pPr')}/{qn('w:spacing')}").set(qn("w:lineRule"), "exact")
    child_ppr = etree.SubElement(child, qn("w:pPr"))
    etree.SubElement(child_ppr, qn("w:spacing"), {
        qn("w:lineRule"): "atLeast", qn("w:line"): "240",
    })
    assert _picture_line_allows_growth(paragraph, styles)


@pytest.mark.parametrize("defect", ["cycle", "missing", "duplicate", "wrong_type", "multiple_defaults"])
def test_picture_line_spacing_rejects_ambiguous_inherited_styles(defect):
    paragraph, styles, normal, child = _inherited_picture_spacing()
    if defect == "cycle":
        etree.SubElement(normal, qn("w:basedOn"), {qn("w:val"): "Picture"})
    elif defect == "missing":
        child.find(qn("w:basedOn")).set(qn("w:val"), "Missing")
    elif defect == "duplicate":
        styles.append(copy.deepcopy(normal))
    elif defect == "wrong_type":
        normal.set(qn("w:type"), "character")
    else:
        child.set(qn("w:default"), "1")
    assert not _picture_line_allows_growth(paragraph, styles)


@pytest.mark.parametrize(("rule", "line", "expected"), [
    ("auto", "239", False),
    ("auto", "240", True),
    ("atLeast", "1", True),
    ("atLeast", "0", False),
    ("atLeast", "2147483648", False),
    ("atLeast", "99999999999", False),
    ("atLeast", "1.5", False),
])
def test_picture_line_spacing_checks_numeric_boundaries(rule, line, expected):
    paragraph, styles, _, _ = _inherited_picture_spacing()
    ppr = paragraph.find(qn("w:pPr"))
    etree.SubElement(ppr, qn("w:spacing"), {
        qn("w:lineRule"): rule, qn("w:line"): line,
    })
    assert _picture_line_allows_growth(paragraph, styles) is expected


def test_subsingle_body_spacing_does_not_shrink_the_image_line(image_case, tmp_path):
    source = image_case[0]
    document = Document(source)
    document.styles["Normal"].paragraph_format.line_spacing = 0.5
    document.save(source)
    case = _recompile(image_case)
    data = rendered(case, tmp_path)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    figure = _paragraph(root, "pg-block:line-image")
    body = _paragraph(root, "pg-block:" + case[2].sections[-1].paragraphs[0].id)
    figure_spacing = figure.find(f"{qn('w:pPr')}/{qn('w:spacing')}")
    body_spacing = body.find(f"{qn('w:pPr')}/{qn('w:spacing')}")
    assert (figure_spacing.get(qn("w:lineRule")), figure_spacing.get(qn("w:line"))) == ("auto", "240")
    assert (body_spacing.get(qn("w:lineRule")), body_spacing.get(qn("w:line"))) == ("auto", "120")
    assert _image_check(inspect(case, data)) == "pass"


@pytest.mark.parametrize("clipping_rule", [False, True])
def test_frozen_candidate_and_archive_are_rechecked_for_picture_line_spacing(
        image_case, tmp_path, monkeypatch, clipping_rule):
    source, template, project, _ = image_case
    project.template_snapshot = template.model_dump(mode="json")
    data = rendered(image_case, tmp_path)
    if clipping_rule:
        def restore_old_fixed_line(root):
            spacing = _paragraph(root, "pg-block:line-image").find(f"{qn('w:pPr')}/{qn('w:spacing')}")
            spacing.set(qn("w:lineRule"), "exact")
            spacing.set(qn("w:line"), "240")
        data = rewrite(data, "word/document.xml", restore_old_fixed_line)
    report = inspect(image_case, data)
    assert _image_check(report) == ("fail" if clipping_rule else "pass")

    # A frozen report from the previous inspector did not contain this rule.
    old_report = copy.deepcopy(report)
    old_report.pop("image_line_spacing_check_version")
    old_report["checks"] = [item for item in old_report["checks"]
                            if item["rule"] != "master.image_line_spacing"]
    assert all(item["status"] == "pass" for item in old_report["checks"])
    old_report["status"] = "pass"
    old_report["check_count"] = old_report["passed_count"] = len(old_report["checks"])
    assert verify_report_binding(old_report, data)

    artifact = tmp_path / "old-image.docx"
    artifact.write_bytes(data)
    monkeypatch.setattr(workflow, "_candidate_artifact_path", lambda _candidate_id: artifact)
    monkeypatch.setattr(workflow, "resolve_template_source", lambda _name: source)
    version = {"id": "old-image-version", "project_id": project.id,
               "content_hash": project_content_hash(project),
               "content_snapshot": project.model_dump(mode="json"),
               "attachment_refs": project_attachment_refs(project)}
    candidate = {"id": "old-image-candidate", "project_id": project.id,
                 "project_version_id": version["id"], "content_hash": version["content_hash"],
                 "artifact": {"docx_sha256": hashlib.sha256(data).hexdigest(), "output_check": old_report}}
    if clipping_rule:
        with pytest.raises(DocxOutputCheckError):
            workflow._verified_candidate_artifact(None, candidate, version)
    else:
        checked, fresh = workflow._verified_candidate_artifact(None, candidate, version)
        assert checked == data
        assert fresh["image_line_spacing_check_version"] == "word-image-line-spacing/1"

    store = Store(tmp_path / "old-image.db")
    submission = {"id": "old-image-submission", "project_version_id": version["id"],
                  "candidate_id": candidate["id"]}
    for kind, record in (("project_version", version), ("candidate", candidate), ("submission", submission)):
        assert store.save_workflow_record(kind, record)
    manifest = {"archive_id": "old-image-archive", "submission_id": submission["id"],
                "output_check": old_report, "output_check_identity": output_check_identity(old_report),
                "files": [{"name": "final.docx", "sha256": hashlib.sha256(data).hexdigest(),
                           "size_bytes": len(data)}]}
    archive_path = tmp_path / "old-image-archive.zip"
    with zipfile.ZipFile(archive_path, "w") as archive_file:
        archive_file.writestr("final.docx", data)
        archive_file.writestr("manifest.json", json.dumps(manifest))
    archive = {"id": manifest["archive_id"], "status": "succeeded", "submission_id": submission["id"],
               "file_path": str(archive_path), "manifest_hash": workflow._hash(manifest),
               "source_template_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    monkeypatch.setattr(workflow, "_archive_dir", lambda: tmp_path)
    if clipping_rule:
        with pytest.raises(DocxOutputCheckError):
            workflow._verified_archive_output(store, archive["id"], archive)
    else:
        _, checked, fresh = workflow._verified_archive_output(store, archive["id"], archive)
        assert checked == data
        assert fresh["image_line_spacing_check_version"] == "word-image-line-spacing/1"
