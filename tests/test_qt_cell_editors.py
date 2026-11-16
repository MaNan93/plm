"""Exercise Qt-created cell editors, whose protected APIs PyQt cannot call."""

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtGui, QtTest, QtWidgets

from plm.frontend.qt.widgets.common import align_cell_text_editor, install_button_sizing
from plm.frontend.qt.widgets.lanegrid import LaneGrid, _LaneCellDelegate
from plm.frontend.qt.widgets.tables import _PaddedItemDelegate
from plm.frontend.qt.styles import get_app_qss
from qt_test_support import preserve_application_state


class CellEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        preserve_application_state(cls, cls.app)

    def setUp(self):
        preserve_application_state(self, self.app)
        self.app.setStyleSheet("")

    def test_default_delegate_editors_layout_and_commit(self):
        for delegate_type in (_PaddedItemDelegate, _LaneCellDelegate):
            with self.subTest(delegate=delegate_type.__name__):
                parent = QtWidgets.QWidget()
                delegate = (delegate_type(None, parent) if delegate_type is _LaneCellDelegate
                            else delegate_type(parent))
                model = QtGui.QStandardItemModel(1, 1, parent)
                index = model.index(0, 0)
                model.setData(index, "before")
                option = QtWidgets.QStyleOptionViewItem()
                option.rect = QtCore.QRect(20, 30, 160, 32)
                editor = delegate.createEditor(parent, option, index)
                try:
                    self.assertIsInstance(editor, QtWidgets.QLineEdit)
                    delegate.setEditorData(editor, index)
                    delegate.updateEditorGeometry(editor, option, index)
                    self.assertEqual(editor.height(), 32)
                    self.assertTrue(editor.property("cellTextEditor"))
                    editor.setText("after")
                    delegate.setModelData(editor, model, index)
                    self.assertEqual(model.data(index), "after")
                finally:
                    parent.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_public_frame_keeps_native_text_inset(self):
        editor = QtWidgets.QLineEdit()
        try:
            for framed in (False, True):
                for read_only in (False, True):
                    for stylesheet in ("", "QLineEdit { border: 1px solid gray; padding: 3px; }"):
                        with self.subTest(framed=framed, read_only=read_only, style=stylesheet):
                            editor.setFrame(framed)
                            editor.setReadOnly(read_only)
                            editor.setStyleSheet(stylesheet)
                            editor.setTextMargins(3, 0, 5, 0)
                            option = QtWidgets.QStyleOptionViewItem()
                            option.rect = QtCore.QRect(20, 30, 160, 32)
                            option.displayAlignment = QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
                            align_cell_text_editor(editor, option, 12)
                            # This editor is Python-created, so the native initializer
                            # is accessible as an independent geometry reference.
                            native = QtWidgets.QStyleOptionFrame()
                            editor.initStyleOption(native)
                            contents = editor.style().subElementRect(
                                QtWidgets.QStyle.SE_LineEditContents, native, editor
                            )
                            self.assertEqual(editor.geometry(), option.rect)
                            self.assertEqual(contents.left() + editor.textMargins().left() + 2, 12)
                            self.assertEqual(editor.rect().right() - contents.right()
                                             + editor.textMargins().right() + 2, 12)
                            self.assertEqual(editor.alignment(), option.displayAlignment)
        finally:
            editor.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_lane_editors_match_cells_with_application_style_and_sizing(self):
        self.app.setStyleSheet(get_app_qss())
        install_button_sizing(self.app)
        grid = LaneGrid()
        grid.set_columns(8)
        grid.set_controllers([{"ctrl_id": "0", "ctrl_name": "C", "max_width": "8"}])
        grid.set_rows([{"mode": str(r), **{f"lane{i}": f"0-{i}" for i in range(8)}}
                       for r in range(12)])
        grid.resize(500, 200)
        grid.show()
        errors = []
        try:
            with patch.object(sys, "excepthook", side_effect=lambda *args: errors.append(args)):
                for row, col in ((0, 0), (0, 1), (11, 7)):
                    with self.subTest(row=row, col=col):
                        item = grid.table.item(row, col)
                        grid.table.scrollToItem(item)
                        grid.table.editItem(item)
                        self.app.processEvents()
                        editor = grid.table.findChild(QtWidgets.QLineEdit)
                        self.assertIsNotNone(editor)
                        self.assertEqual(editor.geometry(), grid.table.visualItemRect(item))
                        if col:
                            self.assertEqual(editor.alignment(), QtCore.Qt.AlignCenter)
                        QtTest.QTest.keyClick(editor, QtCore.Qt.Key_Escape)
                        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
                self.assertEqual(errors, [])
        finally:
            grid.close()
            grid.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_double_click_editor_commits_without_callback_errors(self):
        table = QtWidgets.QTableWidget(1, 1)
        table.setItemDelegate(_PaddedItemDelegate(table))
        table.setItem(0, 0, QtWidgets.QTableWidgetItem("before"))
        table.resize(300, 120)
        errors = []
        try:
            with patch.object(sys, "excepthook", side_effect=lambda *args: errors.append(args)):
                table.show()
                self.app.processEvents()
                pos = table.visualItemRect(table.item(0, 0)).center()
                QtTest.QTest.mouseClick(table.viewport(), QtCore.Qt.LeftButton, pos=pos)
                QtTest.QTest.mouseDClick(table.viewport(), QtCore.Qt.LeftButton, pos=pos)
                self.app.processEvents()
                editor = table.findChild(QtWidgets.QLineEdit)
                self.assertIsNotNone(editor)
                editor.selectAll()
                QtTest.QTest.keyClicks(editor, "after")
                QtTest.QTest.keyClick(editor, QtCore.Qt.Key_Return)
                self.app.processEvents()
                self.assertEqual(table.item(0, 0).text(), "after")
                self.assertEqual(errors, [])
        finally:
            table.close()
            table.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
