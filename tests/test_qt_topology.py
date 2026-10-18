"""Topology exports use current drafts and never save/generate the workspace."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtTest, QtWidgets

from plm.core.workspace import scaffold_default_content
from plm.frontend.qt.views.config_page import ConfigPage
from plm.frontend.qt.views.topology_dialog import TopologyDialog
from qt_test_support import (
    dispose_new_widgets, isolate_host_formatter, preserve_application_state, wait_for_idle,
)


class DiagramStub(QtWidgets.QWidget):
    """UI behavior is isolated from Chromium; a separate probe renders for real."""
    renderFinished = QtCore.pyqtSignal(bool, str)

    def invalidate(self, message=""):
        self.source = ""

    def set_source(self, source):
        self.source = source
        self.renderFinished.emit(True, "")


class TopologyGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        preserve_application_state(cls, cls.app)

    def setUp(self):
        isolate_host_formatter(self, self.app)
        patcher = patch("plm.frontend.qt.views.topology_dialog.MermaidView", DiagramStub)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(dispose_new_widgets, self.app, set(self.app.topLevelWidgets()))
        self.root = Path(self.temp.name)
        self.payload = scaffold_default_content(self.root)

    def open_dialog(self, payload=None):
        dialog = TopologyDialog(self.payload if payload is None else payload)
        dialog.show()
        wait_for_idle(self.app)
        return dialog

    def test_button_exports_unsaved_edits_without_writing_workspace(self):
        page = ConfigPage()
        page.set_content(self.payload)
        page.resize(1000, 700)
        page.show()
        table = page.controllers_editor.table
        table.item(0, 1).setText("DraftController")
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        QtTest.QTest.mouseClick(page.topology_button, QtCore.Qt.LeftButton)
        wait_for_idle(self.app)
        dialog = page.findChild(TopologyDialog)
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.preview.toPlainText().startswith("graph LR\n"))
        self.assertIn("DraftController", dialog.preview.toPlainText())
        self.assertTrue(page.dirty)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertFalse((self.root / "rtl").exists())

    def test_no_project_disables_button(self):
        page = ConfigPage()
        page.set_editable(False)
        self.assertFalse(page.topology_button.isEnabled())
        page.show_topology()
        self.assertIsNone(page.findChild(TopologyDialog))

    def test_generation_architecture_and_dpr_are_used(self):
        self.payload["gen_flags"] = {
            "arch": "serdes", "pclk-mode": "pclk_as_phy_input", "dpr": True,
        }
        dialog = self.open_dialog()
        text = dialog.preview.toPlainText()
        self.assertIn("pipe_lane_clk_gate", text)
        self.assertIn('dpr["DPR"]', text)
        self.assertIn('clk_or_0 -.->|"phy_pclk#91;0#93;"| phy_0', text)

    def test_copy_and_save_preserve_complete_utf8_text(self):
        dialog = self.open_dialog()
        text = dialog.preview.toPlainText()
        QtTest.QTest.mouseClick(dialog.copy_button, QtCore.Qt.LeftButton)
        self.assertEqual(self.app.clipboard().text(), text)
        path = self.root / "topology.mmd"
        with patch("plm.frontend.qt.views.topology_dialog.QtWidgets.QFileDialog") as factory:
            chooser = factory.return_value
            chooser.exec_.return_value = QtWidgets.QDialog.Accepted
            chooser.selectedFiles.return_value = [str(path)]
            dialog.save_button.click()
            chooser.setDefaultSuffix.assert_called_once_with("mmd")
        self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_cancel_and_save_error_leave_text_available(self):
        dialog = self.open_dialog()
        with patch("plm.frontend.qt.views.topology_dialog.QtWidgets.QFileDialog") as factory:
            factory.return_value.exec_.return_value = QtWidgets.QDialog.Rejected
            dialog.save_text()
            factory.return_value.selectedFiles.assert_not_called()
        with patch("plm.frontend.qt.views.topology_dialog.QtWidgets.QFileDialog") as factory:
            factory.return_value.exec_.return_value = QtWidgets.QDialog.Accepted
            factory.return_value.selectedFiles.return_value = [str(self.root / "missing" / "topology.mmd")]
            dialog.save_text()
        self.assertIn("保存失败", dialog.status.text())
        self.assertTrue(dialog.copy_button.isEnabled())

    def test_invalid_mapping_displays_error_and_disables_export(self):
        self.payload["mapping"]["rows"][0]["lane0"] = "99-0"
        dialog = self.open_dialog()
        self.assertIn("无法生成拓扑", dialog.status.text())
        self.assertIn("99", dialog.status.text())
        self.assertFalse(dialog.copy_button.isEnabled())
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertFalse(dialog.preview.toPlainText())

    def test_editing_updates_existing_nonmodal_diagram(self):
        page = ConfigPage()
        page.set_content(self.payload)
        page.show_topology()
        wait_for_idle(self.app)
        dialog = page.findChild(TopologyDialog)
        self.assertFalse(dialog.isModal())
        table = page.controllers_editor.table
        table.item(0, 1).setText("LiveController")
        self.assertFalse(dialog.copy_button.isEnabled())
        self.assertFalse(dialog.preview.toPlainText())
        wait_for_idle(self.app)
        self.assertIn("LiveController", dialog.graph.source)
        page.show_topology()
        self.assertEqual(len(page.findChildren(TopologyDialog)), 1)

    def test_invalid_edit_clears_old_graph_and_recovers_automatically(self):
        dialog = self.open_dialog()
        self.payload["mapping"]["rows"][0]["lane0"] = "99-0"
        dialog.set_payload(self.payload)
        self.assertFalse(dialog.graph.source)
        wait_for_idle(self.app)
        self.assertIn("无法生成拓扑", dialog.status.text())
        self.payload["mapping"]["rows"][0]["lane0"] = "0-0"
        dialog.set_payload(self.payload)
        wait_for_idle(self.app)
        self.assertTrue(dialog.graph.source.startswith("graph LR"))
        self.assertTrue(dialog.copy_button.isEnabled())

    def test_close_cancels_pending_debounce_and_no_project_closes_view(self):
        page = ConfigPage()
        page.set_content(self.payload)
        page.show_topology()
        dialog = page.findChild(TopologyDialog)
        self.assertTrue(dialog.timer.isActive())
        page.set_editable(False)
        self.assertFalse(dialog.timer.isActive())
        self.assertFalse(dialog.isVisible())


if __name__ == "__main__":
    unittest.main()
