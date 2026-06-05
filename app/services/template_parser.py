"""Load and save .ptpl template files."""

from __future__ import annotations

import json
from pathlib import Path

from app.models.template import Template


TEMPLATE_EXTENSION = ".ptpl"


def load_template(file_path: str | Path) -> Template:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"模板文件不存在: {file_path}")
    if path.suffix.lower() != TEMPLATE_EXTENSION:
        raise ValueError(f"文件格式错误，需要 {TEMPLATE_EXTENSION} 文件")
    template = Template.from_dict(json.loads(path.read_text(encoding="utf-8")))
    template.file_path = str(path)
    return template


def save_template(template: Template, file_path: str | Path) -> None:
    path = Path(file_path)
    if path.suffix.lower() != TEMPLATE_EXTENSION:
        path = path.with_suffix(TEMPLATE_EXTENSION)
    path.write_text(template.to_json(), encoding="utf-8")
    template.file_path = str(path)


def list_templates(directory: str | Path) -> list[Path]:
    return sorted(Path(directory).glob(f"*{TEMPLATE_EXTENSION}"))


def create_sample_template() -> Template:
    return Template(
        name="本科毕业论文模板",
        author="系统内置",
        description="适用于本科毕业论文的标准模板，包含封面、摘要、目录、正文、参考文献和致谢。",
    )
