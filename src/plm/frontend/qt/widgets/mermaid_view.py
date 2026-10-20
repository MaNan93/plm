"""Local Mermaid rendering in Qt WebEngine, with versioned async results."""

import json
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets, sip

from plm.frontend.qt.styles.tokens import SPACING_XS

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
        imageReady = QtCore.pyqtSignal(int, str, str)

        def javaScriptConsoleMessage(self, level, message, line, source):
            if message.startswith("PLM_TOPOLOGY_PNG:"):
                result = json.loads(message[len("PLM_TOPOLOGY_PNG:"):])
                self.imageReady.emit(result["request"], result["data"], result["error"])
                return
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
        self._ready = False
        self._rendered = False
        self._active_mode = None
        self._highlight_revision = 0
        self._image_request = 0
        self._image_callbacks = {}
        self.image_export_error = ""
        self.web = None
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.zoom_bar = QtWidgets.QFrame(self)
        self.zoom_bar.setObjectName("TopologyZoomBar")
        zoom_layout = QtWidgets.QHBoxLayout(self.zoom_bar)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        zoom_layout.setSpacing(SPACING_XS)
        for title, script in (("−", "zoomTopology(0.8)"), ("+", "zoomTopology(1.25)"),
                              ("100%", "resetTopology()"),
                              ("适应窗口", "fitTopology()")):
            button = QtWidgets.QPushButton(title)
            button.clicked.connect(lambda checked=False, code=script: self._run(code))
            zoom_layout.addWidget(button)
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
        self.page.imageReady.connect(self._png_result)
        self.web.loadFinished.connect(self._page_loaded)
        self.stack.addWidget(self.web)
        self.web.load(QtCore.QUrl.fromLocalFile(str(VIEWER_PATH)))

    def _run(self, code):
        if not sip.isdeleted(self) and self.web is not None and self._loaded and not sip.isdeleted(self.page):
            self.page.runJavaScript("window." + code)

    def invalidate(self, message="配置已修改，正在更新拓扑…"):
        self._revision += 1
        self._ready = self._rendered = False
        self._active_mode = None
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
        self._ready = self._rendered = not bool(error)
        if not error:
            # Reframe after Qt gives the browser its visible viewport size.
            QtCore.QTimer.singleShot(0, lambda: self._run("readTopology()"))
            if self._active_mode is not None:
                self._apply_highlight()
                return
        self.renderFinished.emit(not bool(error), error)

    @property
    def can_export_image(self):
        return self.web is not None and self._ready

    def highlight_mode(self, mode):
        """Remember mode changes during layout; otherwise update only SVG styles."""
        if self._source is None:
            return False
        self._active_mode = mode
        self._highlight_revision += 1
        if self.web is None:
            self.renderFinished.emit(False, self.message.text())
        elif self._rendered:
            self._apply_highlight()
        return True

    def _apply_highlight(self):
        revision, highlight = self._revision, self._highlight_revision
        self._ready = False

        def finished(ok):
            if sip.isdeleted(self) or (revision, highlight) != (self._revision, self._highlight_revision):
                return
            self._ready = bool(ok)
            self.renderFinished.emit(bool(ok), "" if ok else "无法更新 Mode 高亮。")

        self.page.runJavaScript(f"window.highlightTopologyMode({json.dumps(self._active_mode)})", finished)

    def export_svg(self, callback):
        """Asynchronously return a self-contained SVG, or empty text if stale."""
        if not self.can_export_image:
            callback("")
            return
        revision, highlight = self._revision, self._highlight_revision

        def finished(source):
            if sip.isdeleted(self):
                return
            current = (revision, highlight) == (self._revision, self._highlight_revision)
            callback(source if current and self._ready and isinstance(source, str) else "")

        self.page.runJavaScript("window.getTopologySvg()", finished)

    def export_png(self, scale=2.0, callback=None, background="white"):
        """Rasterize the complete SVG at scale, independent of viewport/zoom.

        Use callback(QImage) for nonblocking UI export. Without a callback this
        returns a QImage after a bounded Qt event loop. background=None keeps
        transparency; the default is white. Failures yield a null QImage.
        """
        if callback is None:
            loop, images = QtCore.QEventLoop(), []
            timeout = QtCore.QTimer()
            timeout.setSingleShot(True)
            timeout.timeout.connect(loop.quit)

            def finished(image):
                images.append(image)
                loop.quit()

            timeout.start(15000)
            self.export_png(scale, finished, background)
            if not images:
                loop.exec_()
            timeout.stop()
            return images[0] if images else QtGui.QImage()
        self.image_export_error = ""
        if not self.can_export_image:
            self.image_export_error = "原理图尚未渲染完成。"
            callback(QtGui.QImage())
            return
        self._image_request += 1
        request = self._image_request
        timeout = QtCore.QTimer(self)
        timeout.setSingleShot(True)
        timeout.timeout.connect(lambda: self._png_result(request, "", "PNG 导出超时。"))
        self._image_callbacks[request] = (self._revision, self._highlight_revision, callback, timeout)
        timeout.start(15000)
        self.page.runJavaScript(
            f"void window.exportTopologyPng({request}, {json.dumps(scale)}, {json.dumps(background)})"
        )

    def _png_result(self, request, data, error):
        pending = self._image_callbacks.pop(request, None)
        if pending is None:
            return
        revision, highlight, callback, timeout = pending
        timeout.stop()
        timeout.deleteLater()
        image = QtGui.QImage()
        if (revision, highlight) != (self._revision, self._highlight_revision):
            error = "原理图已更新，请重新导出。"
        if not error and data.startswith("data:image/png;base64,"):
            image.loadFromData(QtCore.QByteArray.fromBase64(data.split(",", 1)[1].encode("ascii")), "PNG")
        self.image_export_error = error or ("无法生成 PNG 图像。" if image.isNull() else "")
        callback(image)
