from io import BytesIO
import json
import zipfile

from lxml import etree
import pytest

from app.services.template_package_inventory import ALLOWLIST_FORMAT, inventory_template_package

CT = "http://schemas.openxmlformats.org/package/2006/content-types"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
X = "urn:unrecognized-extension"


def q(namespace, local):
    return f"{{{namespace}}}{local}"


def package(document, extras=None):
    content_types = f'''<Types xmlns="{CT}">
        <Default Extension="xml" ContentType="application/xml"/>
        <Default Extension="bin" ContentType="application/octet-stream"/>
        <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
    </Types>'''
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", document)
        for name, data in (extras or {}).items():
            archive.writestr(name, data)
    return output.getvalue()


def basic_document(body='<w:p><w:r><w:t>PRIVATE TEXT</w:t></w:r></w:p>'):
    return f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'


def policy():
    ct_root = [q(CT, "Types")]
    doc_root = [q(W, "document"), q(W, "body")]
    paragraph = doc_root + [q(W, "p")]
    run = paragraph + [q(W, "r")]
    return {
        "format": ALLOWLIST_FORMAT,
        "version": "test-only/1",
        "parts": {"[Content_Types].xml": "xml", "word/document.xml": "xml"},
        "namespaces": [CT, W],
        "elements": [
            {"part": "[Content_Types].xml", "path": ct_root, "attributes": {}},
            {"part": "[Content_Types].xml", "path": ct_root + [q(CT, "Default")],
             "attributes": {"Extension": {"values": ["xml", "bin"]},
                            "ContentType": {"values": ["application/xml", "application/octet-stream"]}}},
            {"part": "[Content_Types].xml", "path": ct_root + [q(CT, "Override")],
             "attributes": {"PartName": {"values": ["/word/document.xml"]},
                            "ContentType": {"values": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"]}}},
            {"part": "word/document.xml", "path": [q(W, "document")], "attributes": {}},
            {"part": "word/document.xml", "path": doc_root, "attributes": {}},
            {"part": "word/document.xml", "path": paragraph, "attributes": {}},
            {"part": "word/document.xml", "path": run, "attributes": {}},
            {"part": "word/document.xml", "path": run + [q(W, "t")], "attributes": {}},
        ],
        "fields": [],
    }


def codes(report):
    return {finding["code"] for finding in report["findings"]}


def test_known_structure_matches_but_is_not_formal_admission():
    data = package(basic_document())
    report = inventory_template_package(data, policy())
    assert report["status"] == "structurally_matched"
    assert report["formal_admission"] is False
    assert report["finding_count"] == 0
    assert len(report["allowlist_sha256"]) == 64
    assert len(report["source_sha256"]) == 64
    assert "PRIVATE TEXT" not in json.dumps(report)
    assert inventory_template_package(data)["status"] == "inventory_only"


def test_unknown_part_attribute_and_wrong_parent_are_located():
    data = package(basic_document('<w:p w:unknown="PRIVATE ATTRIBUTE"><w:t>text</w:t></w:p>'),
                   {"word/secret.bin": b"sample"})
    report = inventory_template_package(data, policy())
    assert report["status"] == "unknown_structure"
    assert {"unknown_part", "unknown_element", "unknown_attribute"} <= codes(report)
    assert any(item["part"] == "word/secret.bin" for item in report["findings"])
    assert any(item["qname"] == q(W, "unknown") for item in report["findings"] if item["code"] == "unknown_attribute")
    assert "PRIVATE ATTRIBUTE" not in json.dumps(report)


def test_unknown_alternate_content_fallback_is_not_hidden_by_ignorable():
    document = f'''<w:document xmlns:w="{W}" xmlns:mc="{MC}" xmlns:x="{X}" mc:Ignorable="x">
        <w:body><mc:AlternateContent><mc:Choice Requires="x"><x:extension/></mc:Choice>
        <mc:Fallback><w:p><w:r><w:t>fallback</w:t></w:r></w:p></mc:Fallback>
        </mc:AlternateContent></w:body></w:document>'''
    report = inventory_template_package(package(document), policy())
    assert report["status"] == "unknown_structure"
    assert any(item.get("qname") == q(X, "extension") for item in report["findings"])
    assert any(q(MC, "Fallback") in item["path"] for item in report["elements"])


def test_compatibility_prefix_must_resolve_to_an_approved_namespace():
    configured = policy()
    configured["namespaces"].append(MC)
    configured["elements"][3]["attributes"] = {q(MC, "Ignorable"): {"values": ["missing"]}}
    document = f'<w:document xmlns:w="{W}" xmlns:mc="{MC}" mc:Ignorable="missing"><w:body/></w:document>'
    report = inventory_template_package(package(document), configured)
    assert "unknown_compatibility_prefix" in codes(report)


def test_xml_processing_instructions_are_inventoried_and_blocked():
    document = f'<?xml version="1.0"?><?example private?><w:document xmlns:w="{W}"><w:body><!--private--></w:body></w:document>'
    report = inventory_template_package(package(document), policy())
    assert "unknown_xml_node" in codes(report)
    assert {item["kind"] for item in report["xml_nodes"]} == {"processing_instruction", "comment"}
    assert "private" not in json.dumps(report)


def test_approved_attribute_value_does_not_approve_other_values():
    configured = policy()
    configured["elements"][5]["attributes"] = {q(W, "rsidR"): {"values": ["1234"]}}
    good = package(basic_document('<w:p w:rsidR="1234"><w:r><w:t>A</w:t></w:r></w:p>'))
    bad = package(basic_document('<w:p w:rsidR="5678"><w:r><w:t>A</w:t></w:r></w:p>'))
    assert inventory_template_package(good, configured)["status"] == "structurally_matched"
    assert "unsupported_attribute_value" in codes(inventory_template_package(bad, configured))


def field_policy():
    configured = policy()
    paragraph = [q(W, "document"), q(W, "body"), q(W, "p")]
    run = paragraph + [q(W, "r")]
    char_path = run + [q(W, "fldChar")]
    configured["elements"].extend([
        {"part": "word/document.xml", "path": char_path,
         "attributes": {q(W, "fldCharType"): {"values": ["begin", "separate", "end"]}}},
        {"part": "word/document.xml", "path": run + [q(W, "instrText")], "attributes": {}},
    ])
    configured["fields"] = [{"part": "word/document.xml", "path": char_path,
                              "operation": "PAGE", "pattern": "PAGE"}]
    return configured


def field_document(instructions):
    return basic_document(f'''<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>
        <w:r><w:instrText>{instructions[0]}</w:instrText></w:r>
        <w:r><w:instrText>{instructions[1]}</w:instrText></w:r>
        <w:r><w:fldChar w:fldCharType="separate"/></w:r>
        <w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>''')


def test_split_fields_are_checked_as_one_instruction():
    configured = field_policy()
    good = inventory_template_package(package(field_document((" PA", "GE "))), configured)
    assert good["status"] == "structurally_matched"
    assert good["fields"][0]["operation"] == "PAGE"
    bad = inventory_template_package(package(field_document((" INCLUDE", "TEXT secret "))), configured)
    assert "unknown_field" in codes(bad)
    assert "secret" not in json.dumps(bad)


def test_broken_fields_duplicate_parts_and_unsafe_xml_fail_closed():
    bad = package(basic_document('<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r></w:p>'))
    assert "invalid_field_structure" in codes(inventory_template_package(bad, field_policy()))
    malformed = package(f'<!DOCTYPE w:document [<!ENTITY e SYSTEM "file:///etc/passwd">]><w:document xmlns:w="{W}"><w:body>&e;</w:body></w:document>')
    with pytest.raises(ValueError, match="DTD"):
        inventory_template_package(malformed)
    duplicate = BytesIO()
    with zipfile.ZipFile(BytesIO(package(basic_document()))) as source, zipfile.ZipFile(duplicate, "w") as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        with pytest.warns(UserWarning, match="Duplicate name"):
            target.writestr("word/document.xml", b"second")
    with pytest.raises(ValueError, match="重复"):
        inventory_template_package(duplicate.getvalue())


def test_policy_requires_explicit_attributes_and_concrete_namespaces():
    configured = policy()
    configured["namespaces"].append("*")
    with pytest.raises(ValueError, match="命名空间"):
        inventory_template_package(package(basic_document()), configured)
    configured = policy()
    configured["elements"][5]["attributes"] = {q(W, "rsidR"): {}}
    with pytest.raises(ValueError, match="允许值或表达式"):
        inventory_template_package(package(basic_document()), configured)
