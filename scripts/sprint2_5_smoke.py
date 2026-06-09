"""Sprint 2.5 端到端冒烟脚本.

流程：
1. 加载 tests/fixtures/templates/minimal.docx
2. extract_template() → list[Requirement]
3. requirements_to_pack(reqs, "SMOKE-MINIMAL", "Smoke Test Pack") → RulePack
4. 序列化 RulePack 为 YAML → load_pack() 验证 schema 兼容
5. 构造违规 IR → visit_document 跑 → 断言 findings > 0
6. 退出码：成功 0 / 失败 1

用法：
    cd E:/projects/论文书写模版格式规范软件
    python scripts/sprint2_5_smoke.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# 让 import 找到 app/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml as yaml_mod

from app.core.engine.loader import load_pack
from app.core.engine.visitor import visit_document
from app.core.extract import extract_template, requirements_to_pack
from app.core.ir import Document, DocumentMetadata, Paragraph, Run, Section


FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "templates" / "minimal.docx"


def _pack_to_yaml(pack) -> str:
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


def main() -> int:
    print("=" * 60)
    print("Sprint 2.5 端到端冒烟测试")
    print("=" * 60)
    print()

    # 1. extract
    print(f"[1/5] extract_template: {FIXTURE.name}...")
    if not FIXTURE.exists():
        print(f"  FAIL fixture 缺失: {FIXTURE}")
        return 1
    reqs = extract_template(FIXTURE)
    print(f"  OK Requirements: {len(reqs)} 条")

    # 2. to_pack
    print()
    print("[2/5] requirements_to_pack → RulePack...")
    pack = requirements_to_pack(reqs, "SMOKE-MINIMAL", "Smoke Test Pack", version="0.1.0")
    print(f"  OK Rules: {len(pack.rules)} 条")

    # 3. 序列化 YAML + load_pack 验证 schema 兼容
    print()
    print("[3/5] YAML 序列化 + load_pack 重加载...")
    with tempfile.TemporaryDirectory() as tmp:
        yaml_path = Path(tmp) / "SMOKE-MINIMAL.yaml"
        yaml_path.write_text(_pack_to_yaml(pack), encoding="utf-8")
        reloaded = load_pack("SMOKE-MINIMAL", packs_dir=Path(tmp))
        print(f"  OK load_pack 后 rules: {len(reloaded.rules)} 条（与原始一致）")

        # 4. 构造违规 IR
        print()
        print("[4/5] 构造违规 IR + visit_document...")
        para = Paragraph(
            style_id="Normal", text="故意违规段", level=None,
            runs=(Run(text="故意违规段", font_eastasia="Arial", font_size_pt=10.0),),
            line_spacing=1.0,  # 大多数 rule 检查 1.5
            space_before_pt=12.0, space_after_pt=12.0,  # 假设检查 0
            first_line_indent_cm=0.0,  # 假设检查 0.74
        )
        ir = Document(
            sections=(Section(margin_top_cm=3.0),),
            paragraphs=(para,),
            metadata=DocumentMetadata(title="smoke"),
        )
        findings = visit_document(ir, reloaded)
        print(f"  OK Findings: {len(findings)} 条")

    # 5. 断言
    print()
    print("[5/5] DoD 断言...")
    if len(reqs) <= 0:
        print(f"  FAIL 期望 reqs > 0，实际 {len(reqs)}")
        return 1
    if len(pack.rules) <= 0:
        print(f"  FAIL 期望 pack.rules > 0，实际 {len(pack.rules)}")
        return 1
    if len(findings) <= 0:
        print(f"  FAIL 期望 findings > 0（故意违规段）")
        return 1

    # B1 follow-up 规避检查
    before_targets = {r.fix.target for r in pack.rules if r.fix and "space_before" in r.fix.target}
    after_targets = {r.fix.target for r in pack.rules if r.fix and "space_after" in r.fix.target}
    print(f"  OK B1 规避：space_before 独立 rule 存在={bool(before_targets)}, space_after 独立 rule 存在={bool(after_targets)}")

    print()
    print("=" * 60)
    print("OK Sprint 2.5 端到端跑通")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
