"""Integration: extract → to_pack → engine.validate_docx 全流程 (Sprint 2.5).

3 e2e tests 验证：
- extract → to_pack 生成的 RulePack 可被 Sprint 2 engine 消费
- 跑 validate_docx 产出 ≥ 1 Finding
- B1 follow-up 规避：模板含 space_before/after 时生成 pack 不再触发 B1
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import yaml as yaml_mod

from app.core.engine import validate_docx
from app.core.engine.loader import load_pack
from app.core.extract import extract_template, requirements_to_pack
from app.core.ir import Document, DocumentMetadata, Paragraph, Run, Section
from app.core.engine.visitor import visit_document


FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "templates" / "minimal.docx"


def _pack_to_yaml(pack) -> str:
    """Serialize RulePack dataclass to YAML string for load_pack()."""
    rules = []
    for r in pack.rules:
        rules.append({
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
    return yaml_mod.dump({
        "id": pack.id,
        "name": pack.name,
        "version": pack.version,
        "rules": rules,
    }, allow_unicode=True, sort_keys=False)


class TestExtractToEngineE2E(unittest.TestCase):

    def test_extract_to_pack_yaml_loadable(self):
        """extract → to_pack → 写 YAML → load_pack 不抛"""
        reqs = extract_template(FIXTURE)
        pack = requirements_to_pack(reqs, "E2E-1", "e2e test", version="0.1.0")
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = Path(tmp) / "E2E-1.yaml"
            yaml_path.write_text(_pack_to_yaml(pack), encoding="utf-8")
            reloaded = load_pack("E2E-1", packs_dir=Path(tmp))
            self.assertEqual(reloaded.id, "E2E-1")
            self.assertEqual(len(reloaded.rules), len(pack.rules))

    def test_extracted_pack_consumable_by_visitor(self):
        """提取的 pack 可被 visit_document 消费"""
        reqs = extract_template(FIXTURE)
        pack = requirements_to_pack(reqs, "E2E-2", "e2e test", version="0.1.0")
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = Path(tmp) / "E2E-2.yaml"
            yaml_path.write_text(_pack_to_yaml(pack), encoding="utf-8")
            rule_pack = load_pack("E2E-2", packs_dir=Path(tmp))

            # 构造一个违规 IR — 拿 pack 里的第一个 Rule，故意反一反
            para = Paragraph(
                style_id="Normal",
                text="故意违规段",
                level=None,
                runs=(Run(text="故意违规段", font_eastasia="Arial", font_size_pt=10.0),),
                line_spacing=1.0,  # 假设 rule 检查 1.5
                space_before_pt=12.0,  # 假设 rule 检查 0
                space_after_pt=12.0,
                first_line_indent_cm=0.0,  # 假设 rule 检查 0.74
            )
            ir = Document(
                sections=(Section(margin_top_cm=3.0),),
                paragraphs=(para,),
                metadata=DocumentMetadata(title="t"),
            )
            findings = visit_document(ir, rule_pack)
            # 应该至少出 1 个 finding（因为我们故意违规多个属性）
            self.assertGreater(len(findings), 0,
                               f"visitor 应对故意违规段产出 findings；rules={len(rule_pack.rules)}")

    def test_b1_followup_avoidance_in_generated_pack(self):
        """**B1 follow-up 规避** — 生成的 pack 中**不存在**同时 fix 包含
        'space_before_pt' AND 'space_after_pt' 的 Rule。

        Sprint 2 follow-up B1 风险：单条 rule 的 fix 同时改 before 和 after
        但只设其中一个。本测试断言：每条 rule 的 fix.target 只命中一个
        字段，规避 B1 复现。
        """
        reqs = extract_template(FIXTURE)
        # 注入一对模拟触发 B1 场景的 reqs
        from app.core.extract.requirement import Requirement
        reqs.append(Requirement(
            id="R9998", category="section", target="section:0",
            property="paragraph.spacing.before", expected="0",
            source_part="word/document.xml", evidence="", auto_applicable=True,
        ))
        reqs.append(Requirement(
            id="R9999", category="section", target="section:0",
            property="paragraph.spacing.after", expected="0",
            source_part="word/document.xml", evidence="", auto_applicable=True,
        ))
        pack = requirements_to_pack(reqs, "B1-E2E", "b1 e2e")

        # **B1 规避核心断言**：不存在 fix.target 同时含 space_before AND space_after
        for rule in pack.rules:
            if not rule.fix:
                continue
            target = rule.fix.target
            self.assertNotRegex(
                target, r"^paragraph\.space_(before|after)_pt.*(before|after)",
                f"B1 bug 复发：rule {rule.id} fix.target={target!r} 同时含 before+after"
            )

        # 验证 before / after 至少各 1 条（to_pack 拆开了）
        before_rules = [r for r in pack.rules if r.fix and r.fix.target == "paragraph.space_before_pt"]
        after_rules = [r for r in pack.rules if r.fix and r.fix.target == "paragraph.space_after_pt"]
        self.assertGreaterEqual(len(before_rules), 1)
        self.assertGreaterEqual(len(after_rules), 1)


if __name__ == "__main__":
    unittest.main()
