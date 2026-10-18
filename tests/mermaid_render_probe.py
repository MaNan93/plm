"""Native WebEngine integration probe; never shows a desktop window."""

import json
from pathlib import Path
import sys

from PyQt5 import QtCore, QtWidgets

from plm.core.model import Config
from plm.core.topology import generate_mermaid_topology
from plm.frontend.qt.widgets.mermaid_view import MermaidView


QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts)
app = QtWidgets.QApplication([])
view = MermaidView()
assert view.web is not None
view.resize(1000, 700)


def wait_render(source, latest=None):
    loop = QtCore.QEventLoop()
    results = []

    def finished(ok, error):
        results.append((ok, error))
        loop.quit()

    view.renderFinished.connect(finished)
    view.set_source(source)
    if latest is not None:
        view.set_source(latest)
    timeout = QtCore.QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(15000)
    loop.exec_()
    timeout.stop()
    view.renderFinished.disconnect(finished)
    assert results, "Renderer timed out"
    return results[-1]


def evaluate(script):
    loop = QtCore.QEventLoop()
    results = []

    def finished(result):
        results.append(result)
        loop.quit()

    view.page.runJavaScript(script, finished)
    QtCore.QTimer.singleShot(5000, loop.quit)
    loop.exec_()
    assert results, "JavaScript callback timed out"
    return results[0]


count = 0
for directory in sorted((Path(__file__).parent / "fixtures" / "samples").glob("*/config")):
    config = Config(directory)
    for mode in ("pclk_as_phy_output", "pclk_as_phy_input"):
        for dpr in (False, True):
            model = {
                "controllers": config.controllers, "mapping": config.mapping,
                "lane_count": config.lane_count, "pclk_mode": mode, "dpr": dpr,
            }
            source = generate_mermaid_topology(model)
            ok, error = wait_render(source)
            assert ok, (directory, mode, dpr, error)
            details = evaluate("""(() => {
                const svg = document.querySelector('#canvas svg');
                return { nodes: svg.querySelectorAll('.node').length,
                         labels: svg.textContent,
                         width: svg.viewBox.baseVal.width,
                         height: svg.viewBox.baseVal.height };
            })()""")
            assert details["nodes"] >= config.lane_count + len(config.controllers)
            assert details["width"] > 0 and details["height"] > 0
            assert "PHY Lane 0" in details["labels"]
            count += 1

assert wait_render('graph LR\n old["Old"] --> lane', 'graph LR\n fresh["Latest"] --> lane')[0]
assert "Latest" in evaluate("document.querySelector('#canvas').textContent")
assert "Old" not in evaluate("document.querySelector('#canvas').textContent")
before = evaluate("document.querySelector('#canvas svg').style.width")
evaluate("window.zoomTopology(1.25)")
after = evaluate("document.querySelector('#canvas svg').style.width")
assert float(after[:-2]) > float(before[:-2]), (before, after)
ok, error = wait_render("graph LR\n invalid[[[")
assert not ok and error
assert evaluate("document.querySelector('#canvas svg') === null")
assert wait_render('graph LR\n restored["Recovered"] --> lane')[0]

# Escaped input remains label text and does not create HTML or script nodes.
source = generate_mermaid_topology({
    "controllers": [{"id": 0, "name": '<script>window.injected=true</script>"|&', "max_width": 1}],
    "lane_mapping": {0: {0: (0, 0)}},
})
assert wait_render(source)[0]
assert evaluate("window.injected === undefined")
assert evaluate("document.querySelector('#canvas script') === null")
assert evaluate("performance.getEntriesByType('resource').every(item => item.name.startsWith('file:'))")
print("OFFLINE_RENDER_PASS " + json.dumps({"configurations": count, "version": evaluate("navigator.userAgent")}))
view.close()
view.deleteLater()
app.processEvents()
