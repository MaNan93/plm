"""Live Mermaid diagram and source export from unsaved editor snapshots."""

from copy import deepcopy
from pathlib import Path

from PyQt5 import QtCore, QtWidgets, sip

from plm.core.topology import (
    CONDITIONAL_ROUTE_COLOR, INACTIVE_ROUTE_COLOR,
    generate_mermaid_topology, highlight_mermaid_topology, topology_mode_color,
)
from plm.frontend.qt.services.tasks import TaskRunner
from plm.frontend.qt.services.workspaces import WorkspaceService
from plm.frontend.qt.styles.tokens import (
    SPACING_XS, SPACING_SM, SPACING_MD, SPACING_LG,
)
from plm.frontend.qt.widgets.code_preview import CodePreview
from plm.frontend.qt.widgets.common import StyledComboBox
from plm.frontend.qt.widgets.mermaid_view import MermaidView


class TopologyDialog(QtWidgets.QDialog):
    def __init__(self, payload, parent=None, payload_provider=None):
        super().__init__(parent)
        self.setObjectName("TopologyDialog")
        self.setWindowTitle("原理图")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowModality(QtCore.Qt.NonModal)
        self.resize(1100, 720)
        self.tasks = TaskRunner(self)
        self.payload_provider = payload_provider
        self._payload = None
        self._plan = None
        self._view_key = None
        self._mode_text = {}
        self._controller_lanes = {}
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self._build)
        self._text = ""
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(SPACING_LG, SPACING_LG, SPACING_LG, SPACING_LG)
        root.setSpacing(SPACING_MD)

        toolbar = QtWidgets.QFrame()
        toolbar.setObjectName("TopologyToolbar")
        selection = QtWidgets.QHBoxLayout(toolbar)
        selection.setContentsMargins(SPACING_MD, SPACING_SM, SPACING_MD, SPACING_SM)
        selection.setSpacing(SPACING_SM)

        self.path_selector = StyledComboBox()
        self.path_selector.setAccessibleName("原理图类型")
        for label, value in (("时钟", "clock"), ("复位", "reset"),
                             ("数据 M2P", "m2p"), ("数据 P2M", "p2m"),
                             ("子模块 data_mux", "data_mux")):
            self.path_selector.addItem(label, value)
        caption = QtWidgets.QLabel("类型")
        caption.setObjectName("TopologyToolbarLabel")
        caption.setBuddy(self.path_selector)
        selection.addWidget(caption)
        selection.addWidget(self.path_selector)

        self.mode_selector = StyledComboBox()
        self.mode_selector.setAccessibleName("Mode")
        self.mode_selector.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        self.mode_selector.setEnabled(False)
        mode_caption = QtWidgets.QLabel("Mode")
        mode_caption.setObjectName("TopologyToolbarLabel")
        mode_caption.setBuddy(self.mode_selector)
        selection.addWidget(mode_caption)
        selection.addWidget(self.mode_selector)
        self.mode_selector.currentIndexChanged.connect(self._show_selected)

        self.filter_selector = StyledComboBox()
        self.filter_selector.setAccessibleName("筛选")
        self.filter_selector.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.filter_selector.setMinimumContentsLength(12)
        self.filter_selector.addItem("全部 Lane", None)
        self.filter_selector.setEnabled(False)
        filter_caption = QtWidgets.QLabel("筛选")
        filter_caption.setObjectName("TopologyToolbarLabel")
        filter_caption.setBuddy(self.filter_selector)
        selection.addWidget(filter_caption)
        selection.addWidget(self.filter_selector)
        self.filter_selector.currentIndexChanged.connect(self._show_selected)

        selection.addSpacing(SPACING_XS)
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.VLine)
        sep.setObjectName("TopologyToolbarSep")
        selection.addWidget(sep)
        selection.addSpacing(SPACING_XS)

        self.compact_checkbox = QtWidgets.QCheckBox("最小粒度视图")
        self.compact_checkbox.setChecked(True)
        self.compact_checkbox.toggled.connect(self._show_selected)
        selection.addWidget(self.compact_checkbox)

        selection.addStretch(1)

        self.active_key = QtWidgets.QLabel("● 当前")
        self.active_key.setObjectName("TopologyLegendKey")
        self.inactive_key = QtWidgets.QLabel("● 其他")
        self.inactive_key.setObjectName("TopologyLegendKey")
        self.inactive_key.setStyleSheet(f"color: {INACTIVE_ROUTE_COLOR};")
        self.conditional_key = QtWidgets.QLabel("● DPR 条件")
        self.conditional_key.setObjectName("TopologyLegendKey")
        self.conditional_key.setStyleSheet(f"color: {CONDITIONAL_ROUTE_COLOR};")
        self.conditional_key.setToolTip("DPR 分支由运行时 v_lane_mask 与同步选择控制，无法仅由 mode 确定。")
        self.conditional_key.hide()
        for key in (self.active_key, self.inactive_key, self.conditional_key):
            selection.addWidget(key)

        root.addWidget(toolbar)
        self.path_selector.currentIndexChanged.connect(self._show_selected)

        self.preview = CodePreview()
        self.preview.highlighter.set_enabled(False)
        self.preview.setAccessibleName("Mermaid 拓扑文本")
        self.preview.setPlaceholderText("正在检查配置并生成拓扑…")
        self.graph = MermaidView()
        self.graph.renderFinished.connect(self._render_finished)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName("TopologyTabs")
        self.tabs.addTab(self.graph, "拓扑图")
        self.tabs.addTab(self.preview, "Mermaid 源文本")
        if hasattr(self.graph, "zoom_bar"):
            self.tabs.setCornerWidget(self.graph.zoom_bar, QtCore.Qt.TopRightCorner)
            self.tabs.currentChanged.connect(lambda idx: self.graph.zoom_bar.setVisible(idx == 0))
        root.addWidget(self.tabs, 1)

        footer = QtWidgets.QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(SPACING_MD)

        self.status = QtWidgets.QLabel("正在生成…")
        self.status.setObjectName("TopologyStatusLabel")
        self.status.setTextFormat(QtCore.Qt.PlainText)
        self.status.setWordWrap(False)
        footer.addWidget(self.status, 0, QtCore.Qt.AlignVCenter)
        footer.addStretch(1)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        self.close_button = buttons.button(QtWidgets.QDialogButtonBox.Close)
        self.close_button.setText("关闭")
        self.close_button.setObjectName("TopologySubtleButton")
        buttons.rejected.connect(self.reject)

        self.copy_button = buttons.addButton("复制 Mermaid", QtWidgets.QDialogButtonBox.ActionRole)
        self.copy_button.setObjectName("TopologySubtleButton")
        self.save_button = buttons.addButton("保存 .mmd", QtWidgets.QDialogButtonBox.ActionRole)
        self.save_button.setObjectName("TopologySubtleButton")
        self.save_svg_button = buttons.addButton("保存 SVG", QtWidgets.QDialogButtonBox.ActionRole)
        self.save_svg_button.setObjectName("TopologySecondaryAccentButton")
        self.save_png_button = buttons.addButton("保存 PNG", QtWidgets.QDialogButtonBox.ActionRole)
        self.save_png_button.setObjectName("PrimaryBtn")

        for button in (self.copy_button, self.save_button, self.save_svg_button, self.save_png_button):
            button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_text)
        self.save_button.clicked.connect(self.save_text)
        self.save_svg_button.clicked.connect(self.save_svg)
        self.save_png_button.clicked.connect(self.save_png)
        footer.addWidget(buttons, 0, QtCore.Qt.AlignVCenter)

        root.addLayout(footer)

        self.set_payload(payload)

    @QtCore.pyqtSlot()
    def refresh_from_editor(self):
        if self.payload_provider is not None:
            self.set_payload(self.payload_provider())

    def set_payload(self, payload):
        if payload == self._payload:
            return
        self._payload = deepcopy(payload)
        self._plan = None
        self._view_key = None
        self._mode_text.clear()
        self.mode_selector.setEnabled(False)
        self.filter_selector.setEnabled(False)
        self.tasks.cancel_latest("topology")
        self._text = ""
        self.preview.clear()
        self.preview.setPlaceholderText("正在检查配置并生成拓扑…")
        self.graph.invalidate()
        self.status.setText("正在更新拓扑…")
        self.copy_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self._enable_image_export(False)
        self.timer.start()

    def _build(self):
        snapshot = deepcopy(self._payload)

        def build(progress):
            progress.check_cancelled()
            plan = WorkspaceService().validate(snapshot)
            progress.check_cancelled()
            return plan

        self.tasks.submit_latest("topology", build, self._show_plan, self._show_error)

    def _show_plan(self, plan):
        self._plan = plan
        previous = self.mode_selector.currentData()
        blocker = QtCore.QSignalBlocker(self.mode_selector)
        self.mode_selector.clear()
        for mode in plan.modes:
            self.mode_selector.addItem(f"Mode {mode}", mode)
        self.mode_selector.setMinimumContentsLength(
            max((len(f"Mode {mode}") for mode in plan.modes), default=0) + 1
        )
        index = self.mode_selector.findData(previous)
        self.mode_selector.setCurrentIndex(index if index >= 0 else 0)
        del blocker
        previous_filter = self.filter_selector.currentData()
        blocker = QtCore.QSignalBlocker(self.filter_selector)
        self.filter_selector.clear()
        self.filter_selector.addItem("全部 Lane", None)
        self._controller_lanes = {
            cid: tuple(sorted({lane for mapping in plan.mapping.values()
                               for lane, (owner, _) in mapping.items() if owner == cid}))
            for cid in plan.controllers
        }
        for cid, controller in sorted(plan.controllers.items()):
            self.filter_selector.addItem(f"仅 {controller.name}", cid)
            item = self.filter_selector.model().item(self.filter_selector.count() - 1)
            lanes = self._controller_lanes[cid]
            item.setEnabled(bool(lanes))
            item.setToolTip("PHY Lane: " + ", ".join(map(str, lanes)) if lanes else "此控制器未占用 PHY Lane")
        index = self.filter_selector.findData(previous_filter)
        self.filter_selector.setCurrentIndex(
            index if index >= 0 and self.filter_selector.model().item(index).isEnabled() else 0
        )
        del blocker
        self.mode_selector.setEnabled(True)
        self.conditional_key.setVisible(plan.options.dpr)
        self._show_selected()

    def _show_selected(self, *_):
        if self._plan is None or self.mode_selector.currentIndex() < 0:
            return
        mode = self.mode_selector.currentData()
        module_view = self.path_selector.currentData() == "data_mux"
        self.mode_selector.setEnabled(not module_view)
        self.compact_checkbox.setEnabled(not module_view)
        self.filter_selector.setEnabled(not module_view)
        self.active_key.setVisible(not module_view)
        self.inactive_key.setVisible(not module_view)
        self.conditional_key.setVisible(not module_view and self._plan.options.dpr)
        self.active_key.setStyleSheet(f"color: {topology_mode_color(mode, self._plan.modes)};")
        key = (self.path_selector.currentData(), self.compact_checkbox.isChecked(),
               self.filter_selector.currentData())
        if key == self._view_key and not module_view:
            if mode not in self._mode_text:
                self._mode_text[mode] = highlight_mermaid_topology(self._text, mode)
            self._text = self._mode_text[mode]
            self._enable_image_export(False)
            # Send the small browser update before Qt lays out the source text.
            accepted = self.graph.highlight_mode(mode)
            self.preview.setPlainText(self._text)
            if accepted:
                return
        text = generate_mermaid_topology(
            self._plan, path=self.path_selector.currentData(),
            compact=self.compact_checkbox.isChecked(),
            mode=mode,
            phy_lanes=self._controller_lanes.get(self.filter_selector.currentData()),
        )
        self._view_key = key
        # Prepare export styles once while a new layout is needed, keeping
        # route generation and text restyling out of subsequent mode switches.
        self._mode_text = {
            value: text if value == mode or module_view else highlight_mermaid_topology(text, value)
            for value in self._plan.modes
        }
        self._show_text(text)

    def _show_text(self, text):
        self._text = text
        self.preview.setPlainText(text)
        self.status.setText("正在渲染拓扑图…")
        self.copy_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self._enable_image_export(False)
        self.graph.set_source(text)

    def _render_finished(self, ok, error):
        self._enable_image_export(ok and self.graph.can_export_image)
        if ok and self.path_selector.currentData() == "data_mux":
            self.status.setText("pipe_lane_data_mux · 参数 N、WIDTH · sel=0 时 dout=safe")
            return
        self.status.setText(
            f"{self.path_selector.currentText()} · {self.mode_selector.currentText()} · {self.filter_selector.currentText()}"
            if ok else f"图形预览失败：{error} 可切换到 Mermaid 源文本。"
        )

    def _show_error(self, error):
        self._enable_image_export(False)
        self.preview.setPlaceholderText("配置检查未通过。修正配置后会自动更新。")
        self.graph.invalidate("配置检查未通过。修正配置后会自动更新。")
        self.status.setText(f"无法生成拓扑：{error}")

    def copy_text(self):
        if self._text:
            QtWidgets.QApplication.clipboard().setText(self._text)
            self.status.setText("Mermaid 文本已复制。")

    def _enable_image_export(self, enabled):
        self.save_svg_button.setEnabled(enabled)
        self.save_png_button.setEnabled(enabled)

    def _choose_export_path(self, suffix):
        stem = ("pipe_lane_data_mux" if self.path_selector.currentData() == "data_mux" else
                f"topology_{self.path_selector.currentData()}_mode_{self.mode_selector.currentData()}")
        if self.path_selector.currentData() != "data_mux" and self.filter_selector.currentData() is not None:
            stem += f"_ctrl_{self.filter_selector.currentData()}"
        formats = {"mmd": "Mermaid", "svg": "SVG", "png": "PNG"}
        dialog = QtWidgets.QFileDialog(
            self, f"保存 {formats[suffix]} 拓扑", f"{stem}.{suffix}",
            f"{formats[suffix]} (*.{suffix});;所有文件 (*)",
        )
        dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptSave)
        dialog.setDefaultSuffix(suffix)
        try:
            accepted = dialog.exec_() == QtWidgets.QDialog.Accepted
            filename = dialog.selectedFiles()[0] if accepted else None
        finally:
            dialog.deleteLater()
        return Path(filename) if filename else None

    def save_text(self):
        if not self._text:
            return
        path = self._choose_export_path("mmd")
        if path is not None:
            self._write_text(path, self._text)

    def _write_text(self, path, text):
        try:
            path.write_text(text, encoding="utf-8")
        except (OSError, UnicodeError) as error:
            self.status.setText(f"保存失败：{error}")
        else:
            self.status.setText(f"已保存：{path}")

    def save_svg(self):
        if not self.graph.can_export_image:
            return
        path = self._choose_export_path("svg")
        if path is None:
            return

        def finished(source):
            if sip.isdeleted(self):
                return
            if source:
                self._write_text(path, source)
            else:
                self.status.setText("导出失败：原理图尚未就绪或已更新，请重试。")

        self.graph.export_svg(finished)

    def save_png(self):
        if not self.graph.can_export_image:
            return
        path = self._choose_export_path("png")
        if path is None:
            return
        self.status.setText("正在导出高清 PNG…")

        def finished(image):
            if sip.isdeleted(self):
                return
            if image.isNull():
                self.status.setText(f"导出失败：{self.graph.image_export_error}")
                return
            try:
                saved = image.save(str(path), "PNG")
            except OSError as error:
                self.status.setText(f"保存失败：{error}")
            else:
                self.status.setText(f"已保存：{path}" if saved else f"保存失败：无法写入 {path}")

        self.graph.export_png(scale=2.0, callback=finished)

    def done(self, result):
        self.timer.stop()
        self.tasks.cancel_latest("topology")
        super().done(result)
