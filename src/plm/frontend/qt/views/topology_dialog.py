"""Live Mermaid diagram and source export from unsaved editor snapshots."""

from copy import deepcopy
from pathlib import Path

from PyQt5 import QtCore, QtWidgets

from plm.core.topology import (
    CONDITIONAL_ROUTE_COLOR, INACTIVE_ROUTE_COLOR,
    generate_mermaid_topology, topology_mode_color,
)
from plm.frontend.qt.services.tasks import TaskRunner
from plm.frontend.qt.services.workspaces import WorkspaceService
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
        self._mode_initialized = False
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self._build)
        self._text = ""
        root = QtWidgets.QVBoxLayout(self)
        selection = QtWidgets.QHBoxLayout()
        self.path_selector = StyledComboBox()
        self.path_selector.setAccessibleName("原理图类型")
        for label, value in (("时钟", "clock"), ("复位", "reset"),
                             ("数据 M2P", "m2p"), ("数据 P2M", "p2m")):
            self.path_selector.addItem(label, value)
        caption = QtWidgets.QLabel("类型")
        caption.setBuddy(self.path_selector)
        selection.addWidget(caption)
        selection.addWidget(self.path_selector)
        self.mode_selector = StyledComboBox()
        self.mode_selector.setAccessibleName("Mode")
        self.mode_selector.setEnabled(False)
        mode_caption = QtWidgets.QLabel("Mode")
        mode_caption.setBuddy(self.mode_selector)
        selection.addWidget(mode_caption)
        selection.addWidget(self.mode_selector)
        self.mode_selector.currentIndexChanged.connect(self._show_selected)
        self.compact_checkbox = QtWidgets.QCheckBox("最小粒度视图")
        self.compact_checkbox.setChecked(True)
        self.compact_checkbox.toggled.connect(self._show_selected)
        selection.addWidget(self.compact_checkbox)
        self.active_key = QtWidgets.QLabel("━ 当前")
        self.inactive_key = QtWidgets.QLabel("━ 其他")
        self.inactive_key.setStyleSheet(f"color: {INACTIVE_ROUTE_COLOR};")
        self.conditional_key = QtWidgets.QLabel("━ DPR 条件")
        self.conditional_key.setStyleSheet(f"color: {CONDITIONAL_ROUTE_COLOR};")
        self.conditional_key.setToolTip("DPR 分支由运行时 v_lane_mask 与同步选择控制，无法仅由 mode 确定。")
        self.conditional_key.hide()
        for key in (self.active_key, self.inactive_key, self.conditional_key):
            selection.addWidget(key)
        selection.addStretch(1)
        root.addLayout(selection)
        self.path_selector.currentIndexChanged.connect(self._show_selected)
        self.preview = CodePreview()
        self.preview.highlighter.set_enabled(False)
        self.preview.setAccessibleName("Mermaid 拓扑文本")
        self.preview.setPlaceholderText("正在检查配置并生成拓扑…")
        self.graph = MermaidView()
        self.graph.renderFinished.connect(self._render_finished)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self.graph, "拓扑图")
        self.tabs.addTab(self.preview, "Mermaid 源文本")
        root.addWidget(self.tabs, 1)
        self.status = QtWidgets.QLabel("正在生成…")
        self.status.setTextFormat(QtCore.Qt.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.button(QtWidgets.QDialogButtonBox.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        self.copy_button = buttons.addButton("复制 Mermaid", QtWidgets.QDialogButtonBox.ActionRole)
        self.save_button = buttons.addButton("保存 .mmd", QtWidgets.QDialogButtonBox.ActionRole)
        for button in (self.copy_button, self.save_button):
            button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_text)
        self.save_button.clicked.connect(self.save_text)
        root.addWidget(buttons)

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
        self.mode_selector.setEnabled(False)
        self.tasks.cancel_latest("topology")
        self._text = ""
        self.preview.clear()
        self.preview.setPlaceholderText("正在检查配置并生成拓扑…")
        self.graph.invalidate()
        self.status.setText("正在更新拓扑…")
        self.copy_button.setEnabled(False)
        self.save_button.setEnabled(False)
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
        self.mode_selector.addItem("全部 Mode", None)
        for mode in plan.modes:
            self.mode_selector.addItem(f"Mode {mode}", mode)
        index = self.mode_selector.findData(previous) if self._mode_initialized else 1
        self.mode_selector.setCurrentIndex(index if index >= 0 else 1)
        self._mode_initialized = True
        del blocker
        self.mode_selector.setEnabled(True)
        self.conditional_key.setVisible(plan.options.dpr)
        self._show_selected()

    def _show_selected(self, *_):
        if self._plan is None:
            return
        mode = self.mode_selector.currentData()
        self.active_key.setVisible(mode is not None)
        self.inactive_key.setVisible(mode is not None)
        self.conditional_key.setVisible(mode is not None and self._plan.options.dpr)
        if mode is not None:
            self.active_key.setStyleSheet(f"color: {topology_mode_color(mode, self._plan.modes)};")
        text = generate_mermaid_topology(
            self._plan, path=self.path_selector.currentData(),
            compact=self.compact_checkbox.isChecked(),
            mode=mode,
        )
        self._show_text(text)

    def _show_text(self, text):
        self._text = text
        self.preview.setPlainText(text)
        self.status.setText("正在渲染拓扑图…")
        self.copy_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self.graph.set_source(text)

    def _render_finished(self, ok, error):
        self.status.setText(
            f"{self.path_selector.currentText()} · {self.mode_selector.currentText()}"
            if ok else f"图形预览失败：{error} 可切换到 Mermaid 源文本。"
        )

    def _show_error(self, error):
        self.preview.setPlaceholderText("配置检查未通过。修正配置后会自动更新。")
        self.graph.invalidate("配置检查未通过。修正配置后会自动更新。")
        self.status.setText(f"无法生成拓扑：{error}")

    def copy_text(self):
        if self._text:
            QtWidgets.QApplication.clipboard().setText(self._text)
            self.status.setText("Mermaid 文本已复制。")

    def save_text(self):
        if not self._text:
            return
        dialog = QtWidgets.QFileDialog(
            self, "保存 Mermaid 拓扑",
            f"topology_{self.path_selector.currentData()}_mode_{self.mode_selector.currentData() if self.mode_selector.currentData() is not None else 'all'}.mmd",
            "Mermaid (*.mmd);;所有文件 (*)",
        )
        dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptSave)
        dialog.setDefaultSuffix("mmd")
        try:
            accepted = dialog.exec_() == QtWidgets.QDialog.Accepted
            filename = dialog.selectedFiles()[0] if accepted else None
        finally:
            dialog.deleteLater()
        if filename is None:
            return
        path = Path(filename)
        try:
            path.write_text(self._text, encoding="utf-8")
        except OSError as error:
            self.status.setText(f"保存失败：{error}")
        else:
            self.status.setText(f"已保存：{path}")

    def done(self, result):
        self.timer.stop()
        self.tasks.cancel_latest("topology")
        super().done(result)
