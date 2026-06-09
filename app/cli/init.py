"""paperguard init — Sprint 3 stub (commit #14 实现完整功能)."""
import click

@click.command()
@click.option("--school", "-s", default=None)
@click.option("--base", "-b", default="GB-T-7713-1-2006")
@click.option("--out", "-o", required=True)
def init(school, base, out):
    """生成空白 pack 骨架 (Sprint 3 commit #14 完整实现)."""
    raise NotImplementedError("Sprint 3 commit #14")

__all__ = ["init"]
