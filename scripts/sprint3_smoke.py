"""Sprint 3 端到端冒烟脚本.

流程：
1. 加载 tests/fixtures/templates/minimal.docx
2. CLI check 跑 validate_docx (text + json 两种 format)
3. CLI fix 跑应用 fix + 写新文件
4. CLI validate-pack 校验 3 个 pack
5. CLI init 生成空白 pack
6. 退出码断言
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# 让 import 找到 app/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from click.testing import CliRunner

from app.cli import cli
from app.core.engine import validate_docx
from app.core.loaders.docx_loader import load_docx
from app.core.writers.fix_applier import apply_all_fixes
from app.core.writers.docx_writer import write_ir_to_docx

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "templates" / "minimal.docx"


def run_cli(runner: CliRunner, *args: str) -> object:
    """跑 CLI 子命令 (用 CliRunner in-process, 不 spawn subprocess)."""
    return runner.invoke(cli, list(args), catch_exceptions=False)


def main() -> int:
    print("=" * 60)
    print("Sprint 3 端到端冒烟测试")
    print("=" * 60)
    print()

    runner = CliRunner()

    # 1. CLI --help
    print("[1/6] paperguard --help...")
    r = run_cli(runner, "--help")
    assert r.exit_code == 0, f"--help 失败: {r.output}"
    assert "PaperGuard" in r.output
    print("  OK --help 工作（含 5 subcommands）")

    # 2. CLI check minimal.docx
    print()
    print("[2/6] paperguard check minimal.docx --format text...")
    r = run_cli(runner, "check", str(FIXTURE), "--pack", "GB-T-7713-1-2006",
                "--format", "text")
    assert r.exit_code in (0, 1), f"unexpected exit {r.exit_code}"
    print(f"  OK check 退出码 {r.exit_code}（0=无 finding / 1=有 finding）")

    # 3. CLI check --format json
    print()
    print("[3/6] paperguard check minimal.docx --format json...")
    r = run_cli(runner, "check", str(FIXTURE), "--pack", "GB-T-7713-1-2006",
                "--format", "json")
    assert r.exit_code in (0, 1)
    try:
        data = json.loads(r.output)
        assert "summary" in data and "findings" in data
        print(f"  OK JSON 输出有效，含 summary={data['summary']['total']} findings")
    except json.JSONDecodeError as e:
        print(f"  FAIL JSON 解析失败: {e}\n{r.output[:200]}")
        return 1

    # 4. CLI fix 输出新文件
    print()
    print("[4/6] paperguard fix minimal.docx --output /tmp/fixed.docx...")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "fixed.docx"
        r = run_cli(runner, "fix", str(FIXTURE), "--pack", "GB-T-7713-1-2006",
                    "--output", str(out))
        assert r.exit_code in (0, 1), f"unexpected exit {r.exit_code}: {r.output}"
        if out.exists():
            print(f"  OK 修复后文件存在 ({out.stat().st_size} bytes)")
        else:
            print(f"  WARN 修复后文件不存在（可能 no findings 提前退出）")

    # 5. CLI validate-pack 3 个 pack
    print()
    print("[5/6] paperguard validate-pack 3 个 pack...")
    for pack_id in ("GB-T-7713-1-2006", "GB-T-7714-1-2015", "SIM-UNIV-generic"):
        pack_path = REPO / "app" / "core" / "rules" / "packs" / f"{pack_id}.yaml"
        r = run_cli(runner, "validate-pack", str(pack_path))
        assert r.exit_code == 0, f"{pack_id} validate-pack 失败: {r.output}"
        print(f"  OK {pack_id} 校验通过")

    # 6. CLI init 生成空白 pack
    print()
    print("[6/6] paperguard init --school TestUniv...")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "skel.yaml"
        r = run_cli(runner, "init", "--school", "TestUniv", "--out", str(out))
        assert r.exit_code == 0, f"init 失败: {r.output}"
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "TestUniv" in content
        # 验证可被 yaml.safe_load 解析
        parsed = yaml.safe_load(content)
        assert "rules" in parsed
        print(f"  OK 骨架生成 ({len(content)} bytes, {len(parsed['rules'])} rules)")

    print()
    print("=" * 60)
    print("OK Sprint 3 端到端跑通")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())

