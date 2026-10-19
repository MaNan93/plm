"""Topology exports use current drafts and never save/generate the workspace."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtTest, QtWidgets

from plm.core.workspace import scaffold_default_content
from plm.core.topology import topology_mode_color
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

    def two_mode_payload(self):
        self.payload["controllers"] = [
            {"ctrl_id": "0", "ctrl_name": "CtrlA", "max_width": "4"},
            {"ctrl_id": "1", "ctrl_name": "CtrlB", "max_width": "2"},
        ]
        self.payload["mapping"]["rows"].append({
            "mode": "1", "lane0": "0-0", "lane1": "0-1", "lane2": "1-0", "lane3": "1-1",
        })
        return self.payload

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
        dialog.compact_checkbox.setChecked(False)
        dialog.path_selector.setCurrentIndex(dialog.path_selector.findData("clock"))
        text = dialog.preview.toPlainText()
        self.assertIn("Clock switch", text)
        self.assertIn('dpr["DPR"]', text)
        self.assertIn('clk_switch_0 -.->|"phy_pclk[0]"| phy_0', text)

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

    def test_four_categories_include_all_lanes_without_lane_selector(self):
        dialog = self.open_dialog()
        self.assertFalse(hasattr(dialog, "lane_selector"))
        self.assertEqual(dialog.path_selector.currentData(), "clock")
        self.assertTrue(dialog.compact_checkbox.isChecked())
        self.assertIn("PHY Lanes 0..3", dialog.graph.source)
        dialog.compact_checkbox.setChecked(False)
        self.assertEqual([dialog.path_selector.itemText(i) for i in range(dialog.path_selector.count())],
                         ["时钟", "复位", "数据 M2P", "数据 P2M", "子模块 data_mux"])
        with patch("plm.frontend.qt.views.topology_dialog.WorkspaceService.validate",
                   side_effect=AssertionError("Category selection should reuse the validated snapshot")):
            for category in ("clock", "reset", "m2p", "p2m"):
                dialog.path_selector.setCurrentIndex(dialog.path_selector.findData(category))
                self.assertEqual(dialog.graph.source.count('["PHY Lane '), 4)
        text = dialog.preview.toPlainText()
        self.assertEqual(text, dialog.graph.source)
        self.assertIn('phy_3["PHY Lane 3"]', text)
        dialog.copy_text()
        self.assertEqual(self.app.clipboard().text(), text)

    def test_category_switch_displays_only_selected_function(self):
        self.payload["gen_flags"]["dpr"] = True
        dialog = self.open_dialog()
        dialog.compact_checkbox.setChecked(False)
        self.assertIn("clk_switch_2", dialog.graph.source)
        dialog.path_selector.setCurrentIndex(dialog.path_selector.findData("reset"))
        self.assertIn("reset_rst_n_2", dialog.graph.source)
        self.assertNotIn("clk_switch_", dialog.graph.source)

    def test_data_mux_module_view_exports_and_restores_mapping_controls(self):
        dialog = self.open_dialog(self.two_mode_payload())
        dialog.mode_selector.setCurrentIndex(dialog.mode_selector.findData(1))
        with patch("plm.frontend.qt.views.topology_dialog.WorkspaceService.validate",
                   side_effect=AssertionError("Module view must reuse the validated snapshot")):
            dialog.path_selector.setCurrentIndex(dialog.path_selector.findData("data_mux"))
        self.assertIn('pipe_lane_data_mux / parameters N, WIDTH', dialog.graph.source)
        self.assertIn('sel=0: dout=safe', dialog.graph.source)
        self.assertFalse(dialog.mode_selector.isEnabled())
        self.assertFalse(dialog.compact_checkbox.isEnabled())
        self.assertTrue(dialog.active_key.isHidden())
        self.assertTrue(dialog.inactive_key.isHidden())
        self.assertIn('pipe_lane_data_mux', dialog.status.text())
        dialog.copy_text()
        self.assertEqual(self.app.clipboard().text(), dialog.graph.source)
        with patch("plm.frontend.qt.views.topology_dialog.QtWidgets.QFileDialog") as factory:
            factory.return_value.exec_.return_value = QtWidgets.QDialog.Rejected
            dialog.save_text()
            self.assertEqual(factory.call_args.args[2], "pipe_lane_data_mux.mmd")
        dialog.path_selector.setCurrentIndex(dialog.path_selector.findData("clock"))
        self.assertTrue(dialog.mode_selector.isEnabled())
        self.assertTrue(dialog.compact_checkbox.isEnabled())
        self.assertEqual(dialog.mode_selector.currentData(), 1)
        dialog.path_selector.setCurrentIndex(dialog.path_selector.findData("m2p"))
        self.assertIn("mux_2", dialog.graph.source)
        self.assertNotIn("p2m_mux_", dialog.graph.source)
        dialog.path_selector.setCurrentIndex(dialog.path_selector.findData("p2m"))
        self.assertIn("p2m_mux_", dialog.graph.source)
        self.assertNotIn("clk_switch_", dialog.graph.source)

    def test_category_survives_live_edits_and_lane_count_changes(self):
        dialog = self.open_dialog()
        dialog.path_selector.setCurrentIndex(dialog.path_selector.findData("p2m"))
        self.payload["controllers"][0]["ctrl_name"] = "RenamedController"
        dialog.set_payload(self.payload)
        wait_for_idle(self.app)
        self.assertEqual(dialog.path_selector.currentData(), "p2m")
        self.assertIn("RenamedController", dialog.graph.source)
        self.payload["mapping"] = {
            "lane_count": 2, "rows": [{"mode": "0", "lane0": "0-0", "lane1": "0-1"}],
        }
        self.payload["controllers"][0]["max_width"] = "2"
        dialog.set_payload(self.payload)
        wait_for_idle(self.app)
        self.assertIn("PHY Lanes 0..1", dialog.graph.source)
        self.assertEqual(dialog.path_selector.currentData(), "p2m")
        self.assertIn("phy_0", dialog.graph.source)
        self.assertNotIn("phy_3", dialog.graph.source)

    def test_compact_toggle_changes_replication_and_export_together(self):
        self.payload["gen_flags"]["dpr"] = True
        dialog = self.open_dialog()
        dialog.path_selector.setCurrentIndex(dialog.path_selector.findData("m2p"))
        compact = dialog.preview.toPlainText()
        self.assertIn("PHY Lanes 0..3", compact)
        self.assertIn("x4 independent", compact)
        self.assertEqual(compact.count('["pipe_lane_data_mux'), 1)
        dialog.compact_checkbox.setChecked(False)
        detailed = dialog.preview.toPlainText()
        self.assertEqual(detailed.count('["PHY Lane '), 4)
        self.assertEqual(detailed.count('["pipe_lane_data_mux'), 4)
        dialog.copy_text()
        self.assertEqual(self.app.clipboard().text(), detailed)
        dialog.compact_checkbox.setChecked(True)
        self.assertEqual(dialog.preview.toPlainText(), compact)

    def test_mode_selector_highlights_selected_routes_and_exports_styles(self):
        dialog = self.open_dialog(self.two_mode_payload())
        self.assertEqual([dialog.mode_selector.itemData(i) for i in range(dialog.mode_selector.count())], [0, 1])
        self.assertEqual([dialog.mode_selector.itemText(i) for i in range(dialog.mode_selector.count())], ['Mode 0', 'Mode 1'])
        self.assertEqual(dialog.mode_selector.currentData(), 0)
        original = dialog.preview.toPlainText()
        with patch("plm.frontend.qt.views.topology_dialog.WorkspaceService.validate",
                   side_effect=AssertionError("Mode selection must reuse the validated snapshot")):
            dialog.mode_selector.setCurrentIndex(dialog.mode_selector.findData(1))
        text = dialog.preview.toPlainText()
        color = topology_mode_color(1, (0, 1))
        self.assertNotEqual(original, text)
        self.assertIn(f"stroke:{color}", text)
        self.assertIn(color, dialog.active_key.styleSheet())
        self.assertIn("Mode 1", dialog.status.text())
        dialog.copy_text()
        self.assertEqual(self.app.clipboard().text(), text)
        with patch("plm.frontend.qt.views.topology_dialog.QtWidgets.QFileDialog") as factory:
            factory.return_value.exec_.return_value = QtWidgets.QDialog.Rejected
            dialog.save_text()
            self.assertIn("mode_1.mmd", factory.call_args.args[2])
        dialog.mode_selector.setCurrentIndex(0)
        self.assertEqual(dialog.mode_selector.currentData(), 0)
        self.assertIn("linkStyle", dialog.preview.toPlainText())
        self.assertFalse(dialog.active_key.isHidden())

    def test_mode_selection_survives_live_edits_and_removal_falls_back(self):
        dialog = self.open_dialog(self.two_mode_payload())
        dialog.mode_selector.setCurrentIndex(dialog.mode_selector.findData(1))
        self.payload["controllers"][0]["ctrl_name"] = "RenamedA"
        dialog.set_payload(self.payload)
        self.assertFalse(dialog.mode_selector.isEnabled())
        wait_for_idle(self.app)
        self.assertEqual(dialog.mode_selector.currentData(), 1)
        self.payload["mapping"]["rows"].pop()
        self.payload["controllers"].pop()
        dialog.set_payload(self.payload)
        wait_for_idle(self.app)
        self.assertEqual(dialog.mode_selector.currentData(), 0)
        dialog.mode_selector.setCurrentIndex(0)
        self.payload["controllers"][0]["ctrl_name"] = "UpdatedA"
        dialog.set_payload(self.payload)
        wait_for_idle(self.app)
        self.assertEqual(dialog.mode_selector.currentData(), 0)
        self.assertEqual(dialog.mode_selector.count(), 1)
        self.assertIn("linkStyle", dialog.preview.toPlainText())


if __name__ == "__main__":
    unittest.main()
