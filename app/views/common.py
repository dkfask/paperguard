"""跨视图错误翻译常量和 show_error 工具。

为 PyQt 视图层提供统一的"把异常翻译给用户"入口：

- 异常类型名 → 用户友好的中文标题 / 正文
- 弹 :class:`QMessageBox`（critical / warning / information）
- 同步写状态栏（如果有 :class:`QMainWindow` 祖先）

测试时注意：``show_error`` 内部调用的 :class:`QMessageBox` 是本模块 import 的
引用，patch 路径应为 ``app.views.common.QMessageBox.<method>`` 而非
``app.views.main_window.QMessageBox.<method>``。
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import QMainWindow, QMessageBox, QWidget


# 异常类型名 → (标题后缀, 正文模板, QMessageBox 方法名)
#
# - 标题后缀：拼到 ``"DOCX {context} {title_suffix}"`` 标题里
# - 正文模板：支持 ``{path}`` 和 ``{error}`` 占位
# - QMessageBox 方法名：``critical`` / ``warning`` / ``information``
_EXCEPTION_TRANSLATIONS: dict[str, tuple[str, str, str]] = {
    "FileNotFoundError": (
        "文件未找到",
        "找不到文件 {path}。\n\n请确认文件路径正确，且文件未被其他程序占用。",
        "warning",
    ),
    "PermissionError": (
        "权限不足",
        "无法写入目标路径 {path}。\n\n请确认目标文件夹有写入权限，或选择其他目录。",
        "critical",
    ),
    "IndexError": (
        "索引越界",
        "无效的修复编号。\n\n请重新选择要修复的条目。",
        "warning",
    ),
}


def show_error(
    parent: Optional[QWidget],
    error: BaseException,
    *,
    context: Optional[str] = None,
    docx_path: Optional[str] = None,
) -> tuple[str, str]:
    """把异常翻译为用户友好的 ``QMessageBox`` 弹窗 + 状态栏提示。

    Args:
        parent: 父级 :class:`QWidget`（用于弹窗居中和查找状态栏）。
        error: 要展示的异常对象。
        context: 额外上下文（如 ``"导入"`` / ``"检查"`` / ``"修复"`` / ``"导出"``）。
        docx_path: 当前操作的 docx 路径（用于填充正文 ``{path}`` 占位）。

    Returns:
        ``(title, first_line)`` 二元组。``title`` 是弹窗标题（如
        ``"DOCX 导入 文件未找到"``）；``first_line`` 是正文第一行
        （用于其他地方展示，比如错误状态标签）。

    Notes:
        - 异常类型不在翻译表时，标题为 ``"DOCX {context} 操作失败"``，
          正文为 ``str(error)``，弹 warning。
        - ``parent`` 为 ``None`` 时弹窗仍能弹出，仅不写状态栏。
        - 找不到 :class:`QMainWindow` 祖先时静默跳过状态栏写入。
    """
    error_type_name = type(error).__name__
    template = _EXCEPTION_TRANSLATIONS.get(error_type_name)

    if template is not None:
        title_suffix, msg_template, box_method = template
    else:
        title_suffix = "操作失败"
        msg_template = "{error}"
        box_method = "warning"

    message = _fill_template(
        msg_template,
        {"path": docx_path or "", "error": str(error)},
    )

    if context:
        title = f"DOCX {context} {title_suffix}"
    else:
        title = f"DOCX {title_suffix}"

    _set_status_bar_message(parent, title, message)

    getattr(QMessageBox, box_method)(parent, title, message)

    first_line = message.split("\n", 1)[0]
    return title, first_line


def _fill_template(template: str, substitutions: dict[str, str]) -> str:
    """简单模板填充，支持 ``{key}`` 占位。

    找不到对应 key 时保留原 ``{key}`` 字符串（避免静默丢字）。
    """
    result = template
    for key, value in substitutions.items():
        result = result.replace(f"{{{key}}}", value)
    return result


def _set_status_bar_message(
    parent: Optional[QWidget],
    title: str,
    message: str,
) -> None:
    """沿 ``parent`` 链向上找 :class:`QMainWindow`，写入状态栏（5 秒后清除）。

    找不到时静默降级，不抛异常。
    """
    widget: Optional[QWidget] = parent
    while widget is not None:
        if isinstance(widget, QMainWindow):
            first_line = message.split("\n", 1)[0]
            widget.statusBar().showMessage(
                f"[错误] {title}：{first_line}",
                5000,
            )
            return
        widget = widget.parentWidget()


__all__ = ["show_error"]
