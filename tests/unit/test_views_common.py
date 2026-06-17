"""``app.views.common`` 的单元测试。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QMainWindow  # noqa: E402

from app.views.common import (  # noqa: E402
    _EXCEPTION_TRANSLATIONS,
    _fill_template,
    show_error,
)


class TestFillTemplate(unittest.TestCase):
    """``_fill_template`` 行为。"""

    def test_replaces_single_placeholder(self) -> None:
        self.assertEqual(
            _fill_template("Hello {name}", {"name": "World"}),
            "Hello World",
        )

    def test_replaces_multiple_placeholders(self) -> None:
        self.assertEqual(
            _fill_template("{a} and {b}", {"a": "1", "b": "2"}),
            "1 and 2",
        )

    def test_missing_key_keeps_placeholder(self) -> None:
        # 未提供的 key 保留原 {key} 字符串
        self.assertEqual(
            _fill_template("Hello {missing}", {"name": "World"}),
            "Hello {missing}",
        )

    def test_empty_value_replaces(self) -> None:
        self.assertEqual(
            _fill_template("Path: {path}", {"path": ""}),
            "Path: ",
        )


class TestExceptionTranslations(unittest.TestCase):
    """翻译表内容（防回归）。"""

    def test_filenotfound_chinese_title(self) -> None:
        title_suffix, _, _ = _EXCEPTION_TRANSLATIONS["FileNotFoundError"]
        self.assertEqual(title_suffix, "文件未找到")

    def test_permissionerror_uses_critical(self) -> None:
        _, _, box_method = _EXCEPTION_TRANSLATIONS["PermissionError"]
        self.assertEqual(box_method, "critical")

    def test_indexerror_uses_warning(self) -> None:
        _, _, box_method = _EXCEPTION_TRANSLATIONS["IndexError"]
        self.assertEqual(box_method, "warning")


class TestShowError(unittest.TestCase):
    """``show_error`` 端到端行为（mock QMessageBox + QMainWindow）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_unknown_exception_uses_fallback(self) -> None:
        # 未在翻译表中的异常 → "DOCX 操作失败" + str(error) + warning
        with patch("app.views.common.QMessageBox.warning") as warning_box:
            show_error(None, RuntimeError("boom"), context="检查")

            warning_box.assert_called_once()
            args = warning_box.call_args[0]
            # args = (parent, title, message)
            self.assertEqual(args[1], "DOCX 检查 操作失败")
            self.assertEqual(args[2], "boom")

    def test_filenotfound_translates_to_chinese(self) -> None:
        with patch("app.views.common.QMessageBox.warning") as warning_box:
            show_error(
                None,
                FileNotFoundError("not found"),
                context="导入",
                docx_path="/x/y.docx",
            )

            warning_box.assert_called_once()
            args = warning_box.call_args[0]
            self.assertEqual(args[1], "DOCX 导入 文件未找到")
            self.assertIn("/x/y.docx", args[2])

    def test_permissionerror_uses_critical_method(self) -> None:
        with patch("app.views.common.QMessageBox.critical") as critical_box:
            show_error(
                None,
                PermissionError("denied"),
                context="导出",
                docx_path="/root/x.docx",
            )

            critical_box.assert_called_once()
            args = critical_box.call_args[0]
            self.assertEqual(args[1], "DOCX 导出 权限不足")
            self.assertIn("/root/x.docx", args[2])

    def test_indexerror_message_no_path(self) -> None:
        # IndexError 翻译不含 {path} 占位
        with patch("app.views.common.QMessageBox.warning") as warning_box:
            show_error(None, IndexError("out of range"), context="修复")

            warning_box.assert_called_once()
            args = warning_box.call_args[0]
            self.assertEqual(args[1], "DOCX 修复 索引越界")
            self.assertNotIn("out of range", args[2])

    def test_status_bar_writes_error_prefix(self) -> None:
        # 测 show_error 调用了 _set_status_bar_message 内部 helper
        with patch("app.views.common._set_status_bar_message") as mock_helper, \
                patch("app.views.common.QMessageBox.warning"):
            show_error(QMainWindow(), RuntimeError("boom"), context="检查")

            mock_helper.assert_called_once()
            args = mock_helper.call_args[0]
            # args = (parent, title, message)
            self.assertIn("DOCX 检查", args[1])
            self.assertIn("boom", args[2])

    def test_no_parent_does_not_crash(self) -> None:
        # parent=None 不崩；QMessageBox 仍弹，无状态栏
        with patch("app.views.common.QMessageBox.warning") as warning_box:
            show_error(None, RuntimeError("boom"))
            warning_box.assert_called_once()
            # parent 传 None
            self.assertIsNone(warning_box.call_args[0][0])

    def test_no_context_omits_context_segment(self) -> None:
        with patch("app.views.common.QMessageBox.warning") as warning_box:
            show_error(None, RuntimeError("boom"))

            args = warning_box.call_args[0]
            # 不带 context 时，标题为 "DOCX 操作失败"
            self.assertEqual(args[1], "DOCX 操作失败")


class TestSetStatusBarMessage(unittest.TestCase):
    """``_set_status_bar_message`` 单独行为。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_skips_when_no_main_window_ancestor(self) -> None:
        # 普通 widget 没有 statusBar，不应崩
        from PyQt5.QtWidgets import QWidget

        from app.views.common import _set_status_bar_message

        widget = QWidget()
        # 不抛异常即可
        _set_status_bar_message(widget, "Title", "Message line 1\nline 2")

    def test_skips_when_parent_is_none(self) -> None:
        from app.views.common import _set_status_bar_message

        _set_status_bar_message(None, "Title", "Message")


if __name__ == "__main__":
    unittest.main()
