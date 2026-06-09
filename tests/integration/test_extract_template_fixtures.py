"""Integration: 5 fixture docx templates end-to-end.

Sprint 2.5 commit #14: 5 integration tests (1 per fixture).
验证 extract_template + requirements_to_pack + load_pack 全流程不抛错。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.engine.loader import load_pack
from app.core.extract import extract_template, requirements_to_pack


FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "templates"


class TestFixtureIntegration(unittest.TestCase):

    def _process(self, name: str) -> None:
        docx_path = FIXTURES / name
        if not docx_path.exists():
            self.skipTest(f"Fixture missing: {docx_path}")

        reqs = extract_template(docx_path)
        self.assertGreater(len(reqs), 0, f"{name} should yield Requirements")

        # 转成 RulePack
        pack_id = f"TPL-{name.replace('.docx', '').upper()}"
        pack = requirements_to_pack(
            reqs, pack_id=pack_id, name=f"Auto from {name}", version="0.1.0"
        )
        self.assertGreater(len(pack.rules), 0,
                           f"{name} should yield at least 1 Rule")

        # 验证 RulePack 可被 Sprint 2 loader 加载（pydantic schema 兼容）
        # 用 tmp 目录写 YAML → load_pack 验证
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            yaml_path = tmpdir / f"{pack_id}.yaml"
            from app.core.engine import loader as engine_loader
            # 序列化 RulePack 为 YAML 字典（loader.load_pack 吃 yaml.safe_load 出的 dict）
            from dataclasses import asdict
            import yaml as yaml_mod
            rules_dict = []
            for r in pack.rules:
                rules_dict.append({
                    "id": r.id,
                    "severity": r.severity.value,
                    "scope": r.scope,
                    "when": r.when,
                    "message": r.message,
                    "fix": {
                        "action": r.fix.action,
                        "target": r.fix.target,
                        "value": r.fix.value,
                    } if r.fix else None,
                })
            yaml_dict = {
                "id": pack.id,
                "name": pack.name,
                "version": pack.version,
                "rules": rules_dict,
            }
            yaml_path.write_text(
                yaml_mod.dump(yaml_dict, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            reloaded = load_pack(pack_id, packs_dir=tmpdir)
            self.assertEqual(reloaded.id, pack.id)
            self.assertEqual(len(reloaded.rules), len(pack.rules))

    def test_minimal_docx(self):
        self._process("minimal.docx")

    def test_gb7713_official_docx(self):
        self._process("gb7713-official.docx")

    def test_tsinghua_style_docx(self):
        self._process("tsinghua-style.docx")

    def test_zhejiang_style_docx(self):
        self._process("zhejiang-style.docx")

    def test_wuhan_style_docx(self):
        self._process("wuhan-style.docx")


if __name__ == "__main__":
    unittest.main()
