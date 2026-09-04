from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from app.services.docx_package_fixer import strip_comments_from_docx


def _commented_docx_bytes() -> bytes:
    content_types = b'''<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/><Override PartName="/word/people.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.people+xml"/></Types>'''
    document = b'''<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:commentRangeStart w:id="0"/><w:r><w:t>body</w:t></w:r><w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r></w:p><w:sectPr/></w:body></w:document>'''
    comments = b'''<?xml version="1.0"?><w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:comment w:id="0" w:author="reviewer"><w:p><w:r><w:t>comment</w:t></w:r></w:p></w:comment></w:comments>'''
    rels = b'''<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'''
    buf = BytesIO()
    with ZipFile(buf, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/comments.xml", comments)
        archive.writestr("word/people.xml", b"<people/>")
        archive.writestr("word/_rels/document.xml.rels", rels)
        archive.writestr("word/styles.xml", b"<styles/>")
        archive.writestr("customXml/item1.xml", b"<custom>kept</custom>")
    return buf.getvalue()


def test_strip_comments_removes_parts_relationships_and_anchors(tmp_path):
    source = tmp_path / "source.docx"
    output = tmp_path / "clean.docx"
    source.write_bytes(_commented_docx_bytes())

    result = strip_comments_from_docx(source, output)

    assert result["parts"] == 2
    assert result["anchors"] == 3
    with ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "word/comments.xml" not in names
        assert "word/people.xml" not in names
        assert archive.read("customXml/item1.xml") == b"<custom>kept</custom>"
        assert b"commentRange" not in archive.read("word/document.xml")
        assert b"commentReference" not in archive.read("word/document.xml")
        assert b"comments" not in archive.read("word/_rels/document.xml.rels")
        assert b"comments" not in archive.read("[Content_Types].xml")
