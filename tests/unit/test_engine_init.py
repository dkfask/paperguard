"""Tests for app.core.engine.__init__ — Sprint 2 入口路由.

3 RED tests：
- validate_docx 转发到 load_pack + visit_document（端到端）
- unknown pack 抛 FileNotFoundError
- 签名与 LiveFormatChecker.validate_docx 调用兼容（位置参数 + 关键字 pack_name）

设计契约：
- validate_docx(ir, pack, packs_dir=None) -> list[Finding]
- 默认 pack = "GB-T-7713-1-2006"（Sprint 1.5 stub-... 字串将在 C8 切换）
- packs_dir 参数注入便于测试，LiveFormatChecker 不传（走默认目录）
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.engine import validate_docx
from app.core.ir import (
    Document,
    DocumentMetadata,
    Finding,
    Paragraph,
    Run,
)


_TEST_PACK_YAML = """\
id: TEST-INIT-PACK
name: "测试入口 pack"
version: 1.0.0
rules:
  - id: PG-LINE-CHECK
    severity: warning
    scope: "paragraphs[*]"
    when:
      "!=":
        - var: "line_spacing"
        - 1.5
    message: "行距不对"
    fix:
      action: set
      target: "paragraph.line_spacing"
      value: 1.5
"""


def _make_doc(line_spacing: float) -> Document:
    para = Paragraph(
        style_id="Body Text",
        text="正文",
        level=None,
        runs=(Run(text="正文", font_eastasia="宋体", font_size_pt=12.0),),
        line_spacing=line_spacing,
        space_before_pt=0.0,
        space_after_pt=0.0,
        first_line_indent_cm=0.74,
    )
    return Document(
        sections=(),
        paragraphs=(para,),
        metadata=DocumentMetadata(title="test"),
    )


class TestValidateDocxEntryPoint(unittest.TestCase):

    def test_validate_docx_forwards_to_loader_and_visitor(self):
        """validate_docx 端到端：YAML pack → load → visit → Finding[]。"""
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "TEST-INIT-PACK.yaml").write_text(_TEST_PACK_YAML, encoding="utf-8")
            doc = _make_doc(line_spacing=1.0)  # 违规
            findings = validate_docx(doc, pack="TEST-INIT-PACK", packs_dir=tmpdir)
            self.assertEqual(len(findings), 1)
            self.assertIsInstance(findings[0], Finding)
            self.assertEqual(findings[0].rule_id, "PG-LINE-CHECK")

    def test_validate_docx_unknown_pack_raises(self):
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            doc = _make_doc(line_spacing=1.5)
            with self.assertRaises(FileNotFoundError):
                validate_docx(doc, pack="NONEXISTENT", packs_dir=tmpdir)

    def test_validate_docx_signature_compatible_with_live_format_checker(self):
        """LiveFormatChecker 调用形如 validate_docx(ir, self._pack_name)；
        签名应支持位置参数 (ir, pack) 且 pack 字符串可选默认值。"""
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "TEST-INIT-PACK.yaml").write_text(_TEST_PACK_YAML, encoding="utf-8")
            doc = _make_doc(line_spacing=1.5)  # 合规
            # 模拟 LiveFormatChecker 的调用方式
            findings = validate_docx(doc, "TEST-INIT-PACK", packs_dir=tmpdir)
            self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
