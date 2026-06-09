"""paperguard extract — Sprint 3 stub (commit #14 实现完整功能)."""
import click

@click.command()
@click.argument("template")
@click.option("--out", "-o", default=None)
def extract(template, out):
    """从 TEMPLATE 抽取 YAML pack (Sprint 3 commit #14 完整实现)."""
    raise NotImplementedError("Sprint 3 commit #14")

__all__ = ["extract"]
