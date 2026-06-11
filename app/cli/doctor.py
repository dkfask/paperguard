"""paperguard doctor — environment diagnostics."""

from __future__ import annotations

import importlib
import sys
import urllib.request
from pathlib import Path

import click

from app.core.engine.loader import list_packs


_PACKS_DIR = Path(__file__).parent.parent / "core" / "rules" / "packs"
_CORE_DEPENDENCIES = [
    "click",
    "docx",
    "jinja2",
    "jmespath",
    "json_logic",
    "lxml",
    "pydantic",
    "yaml",
]


@click.command()
@click.option("--check-network", is_flag=True, default=False,
              help="执行可选网络连通性检查")
def doctor(check_network: bool) -> None:
    """检查 PaperGuard 本地运行环境."""
    checks = [
        ("Python", _check_python()),
        ("Dependencies", _check_dependencies()),
        ("Rule packs", _check_packs()),
        ("PyQt5", _check_pyqt()),
        ("Network", _check_network() if check_network else ("skipped", "use --check-network")),
    ]
    for name, (status, detail) in checks:
        click.echo(f"{name}: {status} ({detail})")


def _check_python() -> tuple[str, str]:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 10):
        return "pass", version
    return "fail", f"{version}; requires >= 3.10"


def _check_dependencies() -> tuple[str, str]:
    missing: list[str] = []
    for name in _CORE_DEPENDENCIES:
        try:
            importlib.import_module(name)
        except Exception:
            missing.append(name)
    if missing:
        return "fail", "missing " + ", ".join(missing)
    return "pass", "core imports available"


def _check_packs() -> tuple[str, str]:
    pack_ids = list_packs(_PACKS_DIR)
    if not _PACKS_DIR.exists():
        return "fail", f"missing {_PACKS_DIR}"
    if not pack_ids:
        return "fail", "no *.yaml packs found"
    return "pass", f"{len(pack_ids)} packs"


def _check_pyqt() -> tuple[str, str]:
    try:
        importlib.import_module("PyQt5")
    except Exception as exc:
        return "fail", type(exc).__name__
    return "pass", "PyQt5 import ok"


def _check_network() -> tuple[str, str]:
    try:
        with urllib.request.urlopen("https://github.com", timeout=3) as response:
            return "pass", f"HTTP {response.status}"
    except Exception as exc:
        return "fail", type(exc).__name__


__all__ = ["doctor"]
