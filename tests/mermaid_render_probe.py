"""Native WebEngine integration probe; never shows a desktop window."""

import json
import math
from pathlib import Path
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

from PyQt5 import QtCore, QtGui, QtWidgets

from plm.core.model import Config
from plm.core.topology import generate_mermaid_topology, topology_mode_color
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


def wait_callback(invoke):
    loop, results = QtCore.QEventLoop(), []

    def finished(*result):
        results.append(result)
        loop.quit()

    timer = QtCore.QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(15000)
    invoke(finished)
    if not results:
        loop.exec_()
    timer.stop()
    assert results, 'Export/highlight callback timed out'
    return results[0]


def highlight(mode):
    def invoke(finished):
        def result(ok, error):
            view.renderFinished.disconnect(result)
            finished(ok, error)
        view.renderFinished.connect(result)
        assert view.highlight_mode(mode)
    ok, error = wait_callback(invoke)
    assert ok, error


def assert_image_exports():
    source, = wait_callback(view.export_svg)
    root = ET.fromstring(source)
    assert root.tag == '{http://www.w3.org/2000/svg}svg'
    assert root.get('viewBox')
    assert 'topology-port' in source and 'topology-label-background' in source
    assert '<marker' in source and '<style' in source
    width, height = float(root.get('width')), float(root.get('height'))
    # Full graph dimensions remain constant while zoomed/scrolled/cropped.
    evaluate('window.zoomTopology(2); viewport.scrollLeft = 100; viewport.scrollTop = 80')
    again, = wait_callback(view.export_svg)
    assert again == source, 'Viewport zoom leaked into exported SVG'
    image = view.export_png(scale=2.0)
    assert not image.isNull(), view.image_export_error
    assert (image.width(), image.height()) == (math.ceil(width * 2), math.ceil(height * 2))
    assert image.pixelColor(0, 0) == QtGui.QColor('white')
    # Decode actual file bytes and check for colored route/node pixels.
    with tempfile.TemporaryDirectory() as directory:
        filename = str(Path(directory) / 'topology.png')
        assert image.save(filename, 'PNG')
        decoded = QtGui.QImage(filename)
        assert decoded.size() == image.size()
    colors = set()
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            color = image.pixelColor(x, y)
            colors.add(color.name().upper())
    assert '#0D9488' in colors and '#D97706' in colors, colors
    transparent, = wait_callback(lambda cb: view.export_png(scale=1.0, callback=cb, background=None))
    assert not transparent.isNull(), view.image_export_error
    assert transparent.pixelColor(0, 0).alpha() == 0
    assert view.export_png(scale=0).isNull()
    assert view.image_export_error


def assert_routes_clear():
    geometry = evaluate("""(() => {
        const svg = document.querySelector('#canvas svg');
        const inverse = svg.getCTM().inverse();
        const boxes = Array.from(svg.querySelectorAll('.node > rect.label-container'), rect => {
            const b = rect.getBBox(), m = inverse.multiply(rect.getCTM());
            return {id: rect.parentNode.getAttribute('data-id'), x: b.x + m.e, y: b.y + m.f,
                    width: b.width, height: b.height};
        });
        for (const prefix of ['ctrl_', 'phy_', 'phy_pclk_source_', 'phy_rx_source_']) {
            const numbered = boxes.filter(box => new RegExp('^' + prefix + '[0-9]+$').test(box.id))
                .sort((a, b) => +a.id.slice(prefix.length) - +b.id.slice(prefix.length));
            for (let i = 1; i < numbered.length; i++) {
                if (numbered[i - 1].y + numbered[i - 1].height > numbered[i].y + 0.5)
                    return {error: 'numbered nodes out of vertical order', prefix, nodes: numbered};
            }
        }
        const paths = Array.from(svg.querySelectorAll('.flowchart-link'));
        if (Array.from(svg.querySelectorAll('.edgeLabel')).some(label => /mode=[0-9]/.test(label.textContent)))
            return {error: 'redundant mode annotation'};
        const ports = Array.from(svg.querySelectorAll('.topology-port'));
        if (ports.length !== paths.length * 2) return {error: 'missing port markers'};
        for (const port of ports) {
            const b = boxes.find(box => box.id === port.getAttribute('data-node'));
            const x = +port.getAttribute('cx'), y = +port.getAttribute('cy');
            if (!b || Math.min(Math.abs(x - b.x), Math.abs(x - b.x - b.width),
                    Math.abs(y - b.y), Math.abs(y - b.y - b.height)) > 1)
                return {error: 'port marker outside boundary'};
        }
        if (Array.from(svg.querySelectorAll('.topology-label-background')).some(
                background => getComputedStyle(background).fill !== 'rgb(255, 255, 255)'))
            return {error: 'label background not white'};
        for (const path of paths) {
            if (/[CQSA]/i.test(path.getAttribute('d'))) return {error: 'curved edge'};
            const m = inverse.multiply(path.getCTM()), length = path.getTotalLength();
            const ports = Array.from(path.classList).filter(c => /^(LS|LE)-/.test(c));
            for (const port of ports) {
                const start = port.startsWith('LS-'), b = boxes.find(b => b.id === port.slice(3));
                const p = path.getPointAtLength(start ? 0 : length), x = p.x + m.e, y = p.y + m.f;
                if (!b || Math.max(b.x - x, x - b.x - b.width, b.y - y, y - b.y - b.height, 0)
                        > (start ? 1 : 8)) return {error: 'disconnected port', port};
            }
            for (let distance = 0; distance <= length; distance += 2) {
                const p = path.getPointAtLength(distance), x = p.x + m.e, y = p.y + m.f;
                const hit = boxes.find(b => x > b.x + 1 && x < b.x + b.width - 1
                    && y > b.y + 1 && y < b.y + b.height - 1);
                if (hit) return {error: 'edge enters component', node: hit.id,
                                 edge: path.getAttribute('class'), x, y};
            }
        }
        return {nodes: boxes.length, edges: paths.length};
    })()""")
    assert "error" not in geometry, geometry
    assert geometry["nodes"] and geometry["edges"], geometry


def assert_aligned_feedback_is_straight():
    routes = evaluate("""(() => {
        const paths = Array.from(document.querySelectorAll('#canvas svg .flowchart-link'))
            .filter(path => Array.from(path.classList).some(c => /^LS-phy_pclk_source_[0-9]+$/.test(c)));
        return paths.map(path => {
            const start = path.getPointAtLength(0), end = path.getPointAtLength(path.getTotalLength());
            const bounds = path.getBBox();
            return {aligned: Math.abs(start.y - end.y) < 0.01, height: bounds.height};
        });
    })()""")
    assert routes and any(route["aligned"] for route in routes), routes
    assert all(not route["aligned"] or route["height"] < 0.01 for route in routes), routes


# Independent, aligned PHY feedback sources have no obstacle to route around.
feedback_model = {
    'controllers': [{'id': cid, 'name': f'Ctrl{cid}', 'max_width': 4} for cid in range(4)],
    'lane_mapping': {0: {lane: (lane // 4, lane % 4) for lane in range(16)}},
    'lane_count': 16, 'pclk_mode': 'pclk_as_phy_output', 'dpr': False,
}
assert wait_render(generate_mermaid_topology(feedback_model, path='clock', compact=True))[0]
assert_routes_clear()
assert_aligned_feedback_is_straight()


# Scrambled model IDs, reverse lane ownership and double-digit IDs must not
# turn insertion order or edge crossing reduction into the displayed order.
ordered_model = {
    'controllers': [{'id': cid, 'name': f'Ctrl{cid}', 'max_width': 3} for cid in (2, 10, 0, 1)],
    'lane_mapping': {0: {lane: ((10, 2, 1, 0)[lane // 3], lane % 3) for lane in range(12)}},
    'lane_count': 12, 'pclk_mode': 'pclk_as_phy_input', 'dpr': True,
}
for route in ('clock', 'reset', 'm2p', 'p2m'):
    for compact in (False, True):
        assert wait_render(generate_mermaid_topology(ordered_model, path=route, compact=compact))[0]
        assert_routes_clear()

count, category_count, compact_count = 0, 0, 0
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
            assert_routes_clear()
            count += 1
            for route in ("clock", "reset", "m2p", "p2m"):
                source = generate_mermaid_topology(model, path=route)
                ok, error = wait_render(source)
                assert ok, (directory, mode, dpr, route, error)
                assert_routes_clear()
                labels = evaluate("document.querySelector('#canvas svg').textContent")
                assert "PHY Lane 0" in labels and f"PHY Lane {config.lane_count - 1}" in labels
                if route == "clock":
                    assert "pipe_lane_data_mux" not in labels
                else:
                    assert "Clock switch" not in labels
                assert "pipe_lane_clk_gate" not in labels and "pipe_lane_clk_or" not in labels
                if route == "reset":
                    assert ("perst_n" in labels) == (mode == "pclk_as_phy_input")
                category_count += 1
                # PHY feedback sources can move to different ELK layers even
                # with DPR disabled; include the reported compact clock case.
                if dpr or route == "clock":
                    original_nodes = evaluate("document.querySelectorAll('#canvas svg .node').length")
                    source = generate_mermaid_topology(model, path=route, compact=True)
                    ok, error = wait_render(source)
                    assert ok, (directory, mode, route, "compact", error)
                    assert_routes_clear()
                    node_labels = evaluate("Array.from(document.querySelectorAll('#canvas svg .node'), node => node.textContent)")
                    covered = []
                    for label in node_labels:
                        match = re.match(r"PHY Lanes? (\d+)(?:\.\.(\d+))?", label.strip())
                        if match:
                            first, last = int(match[1]), int(match[2] or match[1])
                            covered.extend(range(first, last + 1))
                    assert sorted(covered) == list(range(config.lane_count)), (directory, route, covered)
                    assert len(node_labels) <= original_nodes
                    compact_count += 1

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

colored_model = {
    "controllers": [{"id": 0, "name": "A", "max_width": 2}, {"id": 1, "name": "B", "max_width": 1}],
    "lane_mapping": {0: {0: (0, 0), 1: (0, 1)}, 2: {0: (0, 0), 1: (1, 0)}},
    "pclk_mode": "pclk_as_phy_input", "dpr": True,
}
color_count = 0
highlight_times = []
for route in ("clock", "reset", "m2p", "p2m"):
    for selected_mode in (0, 2):
        source = generate_mermaid_topology(colored_model, path=route, compact=True, mode=selected_mode)
        assert wait_render(source)[0], (route, selected_mode)
        assert_routes_clear()
        colors = evaluate("""Array.from(document.querySelectorAll('#canvas svg path[marker-end]'), path => {
            const match = /#([^)]*)/.exec(path.getAttribute('marker-end'));
            const marker = document.querySelector('[id="' + match[1] + '"] path');
            return {stroke: getComputedStyle(path).stroke, fill: getComputedStyle(marker).fill};
        })""")
        expected_hex = topology_mode_color(selected_mode, (0, 2)).lstrip('#')
        expected = 'rgb(' + ', '.join(str(int(expected_hex[i:i+2], 16)) for i in (0, 2, 4)) + ')'
        strokes = {entry['stroke'] for entry in colors}
        assert {expected, 'rgb(203, 213, 225)', 'rgb(217, 119, 6)'} <= strokes, (route, strokes)
        assert all(entry['stroke'] == entry['fill'] for entry in colors), (route, colors)
        color_count += 1
        # All mode changes must retain the actual DOM nodes and geometry. A
        # renderer call here is a regression even if its result looks identical.
        evaluate("""window.beforeSvg = canvas.querySelector('svg');
            window.beforeGeometry = Array.from(beforeSvg.querySelectorAll('.flowchart-link'), p => p.getAttribute('d'));
            window.originalRender = mermaid.render;
            mermaid.render = () => { throw new Error('Mode switch triggered layout'); };
            window.beforeMarkerCount = beforeSvg.querySelectorAll('marker').length;
        """)
        for target_mode in (2, 0, 2):
            highlight(target_mode)
            state = evaluate("""(() => {
                const start = performance.now();
                for (let i = 0; i < 10; i++) highlightTopologyMode(modeMetadata.mode);
                const duration = (performance.now() - start) / 10;
                const svg = canvas.querySelector('svg');
                return {duration, same: svg === window.beforeSvg,
                    geometry: Array.from(svg.querySelectorAll('.flowchart-link'), p => p.getAttribute('d')),
                    markers: svg.querySelectorAll('marker').length,
                    routes: modeRoutes.map(route => ({
                        active: route.modes === null || route.modes.includes(modeMetadata.mode),
                        conditional: route.conditional, color: getComputedStyle(route.path).stroke,
                        opacity: route.path.style.opacity, width: route.path.style.strokeWidth,
                        markers: route.markers.every(p => p.style.fill === route.path.style.stroke),
                        labels: route.labels.every(p => getComputedStyle(p).fill === getComputedStyle(route.path).stroke),
                        ports: route.ports.every(p => getComputedStyle(p).fill === getComputedStyle(route.path).stroke)
                    }))};
            })()""")
            assert state['same']
            assert state['geometry'] == evaluate('window.beforeGeometry')
            assert state['markers'] == evaluate('window.beforeMarkerCount'), 'Marker leak'
            for edge in state['routes']:
                expected = ('#D97706' if edge['conditional'] else
                            topology_mode_color(target_mode, (0, 2)) if edge['active'] else '#CBD5E1')
                expected = QtGui.QColor(expected)
                assert edge['color'] == f'rgb({expected.red()}, {expected.green()}, {expected.blue()})', edge
                assert edge['markers'] and edge['ports'] and edge['labels'], edge
                assert edge['opacity'] == ('1' if edge['active'] or edge['conditional'] else '0.45')
                assert edge['width'] == ('2.5px' if edge['active'] or edge['conditional'] else '1px')
            highlight_times.append(state['duration'])
        evaluate('mermaid.render = window.originalRender')
        if route == 'clock' and selected_mode == 2:
            assert_image_exports()
assert max(highlight_times) < 20, highlight_times

# Rapid changes while the first layout is pending must apply the latest mode.
loop = QtCore.QEventLoop()
result = []
def pending_finished(ok, error):
    result.append((ok, error))
    loop.quit()
view.renderFinished.connect(pending_finished)
view.set_source(generate_mermaid_topology(colored_model, path='clock', mode=0))
for target_mode in (2, 0, 2):
    assert view.highlight_mode(target_mode)
QtCore.QTimer.singleShot(15000, loop.quit)
loop.exec_()
view.renderFinished.disconnect(pending_finished)
assert result and result[-1][0], result
assert evaluate('modeMetadata.mode') == 2
# Both in-flight export types reject a superseded diagram.
stale_svg = []
view.export_svg(stale_svg.append)
view.invalidate()
QtCore.QTimer.singleShot(100, loop.quit)
loop.exec_()
assert stale_svg == [''], stale_svg
assert view.export_png().isNull()
assert evaluate("performance.getEntriesByType('resource').every(item => item.name.startsWith('file:'))")
assert wait_render(generate_mermaid_topology({
    "controllers": [{"id": 0, "name": "A", "max_width": 1}],
    "lane_mapping": {0: {0: (0, 0)}},
}, mode=0))[0]
assert_routes_clear()
dash = evaluate("Array.from(document.querySelectorAll('#canvas svg .flowchart-link'), p => getComputedStyle(p).strokeDasharray)")
assert dash[0] == 'none' and dash[1] != 'none', dash
# Readable framing and explicit overview/100% controls remain distinct.
assert wait_render('graph LR\n' + '\n'.join(f'n{i}["Component {i}"] --> n{i+1}' for i in range(20)))[0]
evaluate('window.readTopology()')
readable = evaluate('scale')
evaluate('window.fitTopology()')
assert evaluate('scale') < readable and readable >= 0.75
evaluate('window.resetTopology()')
assert evaluate('scale') == 1
print("OFFLINE_RENDER_PASS " + json.dumps({"configurations": count, "category_views": category_count,
                                        "compact_views": compact_count, "colored_views": color_count,
                                        "highlight_ms_max": max(highlight_times),
                                        "version": evaluate("navigator.userAgent")}))
view.close()
view.deleteLater()
app.processEvents()
