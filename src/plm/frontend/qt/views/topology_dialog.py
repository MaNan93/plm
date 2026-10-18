"""Live Mermaid diagram and source export from unsaved editor snapshots."""

from copy import deepcopy
from pathlib import Path

from PyQt5 import QtCore, QtWidgets

from plm.core.topology import generate_mermaid_topology
from plm.frontend.qt.services.tasks import TaskRunner
from plm.frontend.qt.services.workspaces import WorkspaceService
from plm.frontend.qt.widgets.code_preview import CodePreview
from plm.frontend.qt.widgets.mermaid_view import MermaidView


class TopologyDialog(QtWidgets.QDialog):
    def __init__(self, payload, parent=None, payload_provider=None):
        super().__init__(parent)
        self.setObjectName("TopologyDialog")
        self.setWindowTitle("Mermaid 拓扑")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowModality(QtCore.Qt.NonModal)
        self.resize(1100, 720)
        self.tasks = TaskRunner(self)
        self.payload_provider = payload_provider
        self._payload = None
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self._build)
        self._text = ""
        root = QtWidgets.QVBoxLayout(self)
        description = QtWidgets.QLabel(
            "修改 Controllers、LaneMapping 或 Generation 配置后，拓扑图会自动更新，"
            "包含未保存的修改。可缩放或拖动图形，也可查看和导出 Mermaid 源文本。"
        )
        description.setWordWrap(True)
        root.addWidget(description)
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
            return generate_mermaid_topology(plan)

        self.tasks.submit_latest("topology", build, self._show_text, self._show_error)

    def _show_text(self, text):
        self._text = text
        self.preview.setPlainText(text)
        self.status.setText("正在渲染拓扑图…")
        self.copy_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self.graph.set_source(text)

    def _render_finished(self, ok, error):
        self.status.setText(
            "拓扑已更新。实线为数据路径，虚线为时钟；此图展示正向逻辑连接。"
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
            self, "保存 Mermaid 拓扑", "topology.mmd", "Mermaid (*.mmd);;所有文件 (*)",
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
