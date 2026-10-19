"""Native WebEngine integration probe; never shows a desktop window."""

import json
from pathlib import Path
import re
import sys

from PyQt5 import QtCore, QtWidgets

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
                                        "version": evaluate("navigator.userAgent")}))
view.close()
view.deleteLater()
app.processEvents()
