"""Local Mermaid rendering in Qt WebEngine, with versioned async results."""

import json
from pathlib import Path

from PyQt5 import QtCore, QtWidgets, sip

try:
    # Imported through ConfigPage before QApplication is constructed. This
    # avoids late WebEngine initialization on platforms requiring shared GL.
    from PyQt5 import QtWebEngineWidgets
except ImportError:
    QtWebEngineWidgets = None


VIEWER_PATH = Path(__file__).resolve().parents[1] / "assets" / "mermaid" / "viewer.html"


if QtWebEngineWidgets is not None:
    class MermaidPage(QtWebEngineWidgets.QWebEnginePage):
        resultReady = QtCore.pyqtSignal(int, str)

        def javaScriptConsoleMessage(self, level, message, line, source):
            if message.startswith("PLM_TOPOLOGY:"):
                result = json.loads(message[len("PLM_TOPOLOGY:"):])
                self.resultReady.emit(result["version"], result["error"])

        def acceptNavigationRequest(self, url, kind, main_frame):
            return url.isLocalFile() and Path(url.toLocalFile()).resolve() == VIEWER_PATH.resolve()


class MermaidView(QtWidgets.QWidget):
    renderFinished = QtCore.pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._revision = 0
        self._source = None
        self._loaded = False
        self._page_error = ""
        self.web = None
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        controls = QtWidgets.QHBoxLayout()
        for title, script in (("−", "zoomTopology(0.8)"), ("+", "zoomTopology(1.25)"),
                              ("100%", "resetTopology()"),
                              ("适应窗口", "fitTopology()")):
            button = QtWidgets.QPushButton(title)
            button.clicked.connect(lambda checked=False, code=script: self._run(code))
            controls.addWidget(button)
        controls.addStretch(1)
        root.addLayout(controls)
        self.stack = QtWidgets.QStackedWidget()
        root.addWidget(self.stack, 1)
        self.message = QtWidgets.QLabel("正在准备拓扑图…")
        self.message.setTextFormat(QtCore.Qt.PlainText)
        self.message.setAlignment(QtCore.Qt.AlignCenter)
        self.message.setWordWrap(True)
        self.stack.addWidget(self.message)
        if QtWebEngineWidgets is None:
            self.message.setText("图形预览需要 PyQtWebEngine。请执行 uv sync 后重启 PLM；仍可查看和导出源文本。")
            return
        self.web = QtWebEngineWidgets.QWebEngineView()
        self.web.setContextMenuPolicy(QtCore.Qt.NoContextMenu)
        self.web.setAccessibleName("实时 Mermaid 拓扑图")
        self.page = MermaidPage(self.web)
        self.web.setPage(self.page)
        settings = self.page.settings()
        settings.setAttribute(settings.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(settings.JavascriptCanOpenWindows, False)
        self.page.resultReady.connect(self._render_result)
        self.web.loadFinished.connect(self._page_loaded)
        self.stack.addWidget(self.web)
        self.web.load(QtCore.QUrl.fromLocalFile(str(VIEWER_PATH)))

    def _run(self, code):
        if not sip.isdeleted(self) and self.web is not None and self._loaded and not sip.isdeleted(self.page):
            self.page.runJavaScript("window." + code)

    def invalidate(self, message="配置已修改，正在更新拓扑…"):
        self._revision += 1
        self._source = None
        self._run(f"invalidateTopology({self._revision})")
        if self.web is not None:
            self.message.setText(message)
        self.stack.setCurrentWidget(self.message)

    def set_source(self, source):
        self.invalidate("正在渲染拓扑图…")
        self._source = source
        if self.web is None:
            self.renderFinished.emit(False, self.message.text())
        elif self._page_error:
            self.message.setText(self._page_error)
            self.renderFinished.emit(False, self._page_error)
        elif self._loaded:
            self._render()

    def _page_loaded(self, ok):
        if not ok:
            self._renderer_ready(False)
            return
        self.page.runJavaScript(
            "Boolean(window.renderTopology && window.mermaid && window.structuredClone)",
            self._renderer_ready,
        )

    def _renderer_ready(self, ok):
        # WebEngine may invoke an outstanding JS callback while destroying
        # the page, e.g. if the user closes a preview immediately after opening.
        if sip.isdeleted(self) or sip.isdeleted(self.message):
            return
        self._loaded = bool(ok)
        if not ok:
            self._page_error = "无法初始化本地 Mermaid 渲染组件。"
            self.message.setText(self._page_error)
            self.renderFinished.emit(False, self._page_error)
        elif self._source is not None:
            self._render()

    def _render(self):
        # ELK schedules layout work asynchronously. Keep the browser visible
        # while it works so Chromium does not throttle its background timers.
        self.stack.setCurrentWidget(self.web)
        self._run(f"renderTopology({json.dumps(self._source)}, {self._revision})")

    def _render_result(self, revision, error):
        if revision != self._revision:
            return
        self.message.setText(error)
        self.stack.setCurrentWidget(self.message if error else self.web)
        if not error:
            # Reframe after Qt gives the browser its visible viewport size.
            QtCore.QTimer.singleShot(0, lambda: self._run("readTopology()"))
        self.renderFinished.emit(not bool(error), error)
