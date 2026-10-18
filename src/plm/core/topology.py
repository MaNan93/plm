"""Generate Mermaid summaries of clock, reset, and bidirectional data routing.

This is a logical per-lane view, not an RTL instance/netlist export: clock
gates shared by several lanes in the RTL are drawn beside each lane.
"""

from collections import Counter
from collections.abc import Mapping
import json

from plm.core.model import _decimal_int


# Same palette order as the controller swatches in the lane-mapping editor.
# Keep the exporter independent of Qt so it also works in CLI-only installs.
_COLORS = ("#4F46E5", "#0D9488", "#7C3AED", "#D97706", "#059669", "#E11D48")
INACTIVE_ROUTE_COLOR = "#CBD5E1"
CONDITIONAL_ROUTE_COLOR = "#D97706"
_MODE_COLORS = ("#4F46E5", "#0D9488", "#7C3AED", "#2563EB", "#059669", "#E11D48")


def topology_mode_color(mode, modes):
    """One stable highlight color per mode, shared by the export and GUI key."""
    return _MODE_COLORS[sorted(modes).index(mode) % len(_MODE_COLORS)]


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _label(value, safe=""):
    """Escape user text using Mermaid's decimal character entities."""
    return "".join(
        char if char.isalnum() or char in " _-:;,.=/" + safe else f"#{ord(char)};"
        for char in str(value).replace("\r", " ").replace("\n", " ")
    )


def _diagram_label(value, separator, safe=""):
    """Wrap long generated labels at field boundaries, without losing text."""
    parts = value.split(separator) if len(value) > 40 else [value]
    return "<br/>".join(_label(part, safe) for part in parts)


def _lane_bundles(routes, lanes):
    """Bundle at the minimum active width, refining at routing boundaries.

    Lanes must be contiguous, have identical ownership in every mode and
    consecutive controller ports. Refine controller-port cuts across all
    candidates so a P2M bus never combines ports with different fan-ins.
    """
    widths = Counter(
        (mode, cid) for per_lane in routes.values()
        for (cid, _cl), modes in per_lane.items() for mode in modes
    )
    granularity = min(widths.values(), default=1)

    def signature(lane):
        return tuple(sorted((cid, cl - lane, tuple(sorted(modes)))
                            for (cid, cl), modes in routes.get(lane, {}).items()))

    groups = []
    for lane in lanes:
        if (groups and lane == groups[-1][-1] + 1
                and lane // granularity == groups[-1][0] // granularity
                and signature(lane) == signature(groups[-1][0])):
            groups[-1].append(lane)
        else:
            groups.append([lane])
    while True:
        cuts = {}
        for group in groups:
            for cid, cl in routes.get(group[0], {}):
                cuts.setdefault(cid, set()).update((cl, cl + len(group)))
        refined = []
        for group in groups:
            positions = {0, len(group)}
            for cid, cl in routes.get(group[0], {}):
                positions.update(cut - cl for cut in cuts[cid] if cl < cut < cl + len(group))
            positions = sorted(positions)
            refined.extend(group[left:right] for left, right in zip(positions, positions[1:]))
        if len(refined) == len(groups):
            return {group[0]: tuple(group) for group in groups}
        groups = refined


def generate_mermaid_topology(model, *, phy_lanes=None, path="all", compact=False, mode=None) -> str:
    """Return a standalone ``graph LR`` string, without Markdown fences.

    Accepts Config/GenerationPlan or a dict/object with:
      controllers: list of {id, name, max_width}, Controller objects, or
                   an ID -> Controller dict (ctrl_id/ctrl_name also work).
      lane_mapping: {mode: {phy_lane: (controller_id, controller_lane)}};
                    Config's ``mapping`` spelling is also accepted.
      lane_count: optional; inferred from the mapping when omitted.
      pclk_mode: optional; defaults to pclk_as_phy_output, as in PLM.
      dpr: optional bool; adds a DPR branch selected by v_lane_mask.

    GenerationPlan supplies pclk_mode/dpr through its ``options`` field.
    Solid lines represent MAC->PHY data; dotted lines represent PHY-input
    PCLK or PCLK feedback in the original combined view.
    Repeated routes across modes are merged and labelled with those modes.
    Single-candidate routes bypass the mux/gates when DPR is disabled.
    Optional phy_lanes selects physical lane numbers without renumbering;
    path may be 'clock', 'reset', 'm2p', or 'p2m'. 'data' aliases 'm2p';
    'all' preserves the original M2P + forward-clock combined view.
    The clock-only view also shows return PCLK/recovered clocks. The reset
    view follows raw dec_tgt and includes independent perst_n for SerDes.
    A lane-filtered diagram includes only that selection's controllers, with
    their original palette positions preserved.
    compact=True bundles equivalent contiguous lanes up to the minimum
    active controller width; bus labels retain PHY/controller port ranges.
    Replicated data muxes and independent clock gates are marked xN.
    mode selects a mapping mode to highlight: its complete routes use one
    color, other candidate routes fade to gray, and runtime-dependent DPR
    paths are amber. None keeps the unhighlighted all-mode view.
    """
    raw_controllers = _field(model, "controllers", [])
    entries = raw_controllers.values() if isinstance(raw_controllers, Mapping) else raw_controllers
    controllers = {}
    for entry in entries:
        cid = _decimal_int(_field(entry, "id", _field(entry, "ctrl_id")))
        name = _field(entry, "name", _field(entry, "ctrl_name", str(cid)))
        width = _field(entry, "max_width")
        width = _decimal_int(width) if width is not None else None
        if cid < 0 or cid in controllers or (width is not None and width < 1):
            raise ValueError(f"Invalid or duplicate controller: {cid}")
        controllers[cid] = (str(name), width)

    raw_mapping = _field(model, "lane_mapping")
    if raw_mapping is None:
        raw_mapping = _field(model, "mapping", {})
    routes = {}
    modes_seen = set()
    for raw_mode, per_lane in raw_mapping.items():
        mapping_mode = _decimal_int(raw_mode)
        if mapping_mode in modes_seen:
            raise ValueError(f"Duplicate mode: {mapping_mode}")
        modes_seen.add(mapping_mode)
        lanes_seen = set()
        for raw_lane, owner in per_lane.items():
            lane = _decimal_int(raw_lane)
            cid, ctrl_lane = map(_decimal_int, owner)
            if lane < 0 or lane in lanes_seen or cid not in controllers or ctrl_lane < 0:
                raise ValueError(f"Invalid mapping at mode {mapping_mode}, PHY lane {lane}: {owner}")
            lanes_seen.add(lane)
            width = controllers[cid][1]
            if width is not None and ctrl_lane >= width:
                raise ValueError(f"Controller {cid} lane {ctrl_lane} exceeds max_width={width}")
            routes.setdefault(lane, {}).setdefault((cid, ctrl_lane), []).append(mapping_mode)

    inferred_count = max(routes, default=-1) + 1
    lane_count = _decimal_int(_field(model, "lane_count", inferred_count))
    if lane_count < inferred_count or lane_count < 0:
        raise ValueError("lane_count does not cover all mapped PHY lanes")
    if path not in ("data", "clock", "all", "reset", "m2p", "p2m"):
        raise ValueError(f"Unsupported topology path: {path}")
    if mode is not None:
        mode = _decimal_int(mode)
        if mode not in modes_seen:
            raise ValueError(f"Undefined topology mode: {mode}")
    lanes = list(range(lane_count)) if phy_lanes is None else sorted(set(map(_decimal_int, phy_lanes)))
    if phy_lanes is not None and (not lanes or any(lane < 0 or lane >= lane_count for lane in lanes)):
        raise ValueError("phy_lanes must select existing PHY lane numbers")
    visible_controllers = (
        set(controllers) if phy_lanes is None else
        {cid for lane in lanes for cid, _ in routes.get(lane, {})}
    )
    show_data, show_clock = path in ("data", "m2p", "all"), path in ("clock", "all")
    bundles = _lane_bundles(routes, lanes) if compact else {lane: (lane,) for lane in lanes}
    lanes = list(bundles)
    sizes = {lane: len(group) for lane, group in bundles.items()}

    def span(start, size):
        return str(start) if size == 1 else f"{start}..{start + size - 1}"

    def port(name, start, size):
        return f"{name}[{start}]" if size == 1 else f"{name}[{start + size - 1}:{start}]"

    def copies(lane):
        return "" if sizes[lane] == 1 else f" / x{sizes[lane]} independent"

    options = _field(model, "options", model)
    pclk_mode = _field(options, "pclk_mode", "pclk_as_phy_output")
    if pclk_mode not in ("pclk_as_phy_output", "pclk_as_phy_input"):
        raise ValueError(f"Unsupported pclk_mode: {pclk_mode}")
    dpr = _field(options, "dpr", False)
    if not isinstance(dpr, bool):
        raise ValueError("dpr must be a bool")
    clock_port = "phy_pclk" if pclk_mode == "pclk_as_phy_input" else "phy_pclk_in"

    controller_nodes, logic_nodes, phy_nodes, edges, styles = [], [], [], [], []
    clock_sources, source_gates, tie_nodes = [], [], []
    edge_states = []

    def node(target, node_id, text):
        label = _diagram_label(text, " / ")
        target.append(f'    {node_id}["{label}"]')
        if target is phy_nodes:
            fill, stroke = "#ECFDF5", "#059669"
        elif target is clock_sources:
            fill, stroke = "#F0F9FF", "#0284C7"
        elif target is tie_nodes:
            fill, stroke = "#F8FAFC", "#94A3B8"
        elif target is source_gates or node_id.startswith(("clk_", "rxclk_")):
            fill, stroke = "#EFF6FF", "#3B82F6"
        elif node_id.startswith("reset_"):
            fill, stroke = "#FFFBEB", "#D97706"
        elif target is logic_nodes:
            fill, stroke = "#F5F3FF", "#8B5CF6"
        elif node_id == "dpr":
            fill, stroke = "#FFF7ED", "#F97316"
        else:
            return
        styles.append(f"  style {node_id} fill:{fill},stroke:{stroke},stroke-width:1.5px,color:#111827")

    def edge(source, target, text, clock=False, *, modes=None, conditional=False):
        arrow = "-.->" if clock else "-->"
        # Quoted edge labels accept these characters literally. Avoid entity
        # escapes here: the bundled ELK renderer otherwise escapes them twice.
        label = _diagram_label(text, "; ", safe="[]>")
        edges.append(f'  {source} {arrow}|"{label}"| {target}')
        edge_states.append((source, target, modes, conditional, clock))

    for index, (cid, (name, width)) in enumerate(controllers.items()):
        if cid not in visible_controllers:
            continue
        node(controller_nodes, f"ctrl_{cid}", f"{cid}: {name}")
        color = _COLORS[index % len(_COLORS)]
        styles.append(f"  style ctrl_{cid} fill:#FFFFFF,stroke:{color},stroke-width:3px,color:#111827")
    if dpr:
        node(controller_nodes, "dpr", "DPR")

    for lane in lanes:
        phy = f"phy_{lane}"
        candidates = routes.get(lane, {})
        phy_label = (f"PHY Lane {lane}" if sizes[lane] == 1 else
                     f"PHY Lanes {span(lane, sizes[lane])} / x{sizes[lane]}")
        node(phy_nodes, phy, phy_label + (" (unmapped)" if not candidates else ""))
        if path in ("reset", "p2m"):
            continue
        branches = [
            (f"ctrl_{cid}", ctrl_lane, "mode=" + ",".join(map(str, sorted(modes))), modes)
            for (cid, ctrl_lane), modes in sorted(candidates.items())
        ]
        if dpr:
            zero = "0" if sizes[lane] == 1 else "all 0"
            one = "1" if sizes[lane] == 1 else "all 1"
            branches = [(source, cl, label + f"; {port('v_lane_mask', lane, sizes[lane])}={zero}", active_modes)
                        for source, cl, label, active_modes in branches]
            branches.append(("dpr", lane, f"{port('v_lane_mask', lane, sizes[lane])}={one}", None))
        if not branches:
            continue
        if len(branches) == 1 and not dpr:
            source, cl, label, active_modes = branches[0]
            if show_data:
                edge(source, phy, f"lane {span(cl, sizes[lane])}; {label}; data", modes=active_modes)
            clock_source = port("pclk", cl, sizes[lane]) if pclk_mode == "pclk_as_phy_input" else "ctrl_pclk"
            if show_clock:
                edge(source, phy, f"{clock_source} -> {port(clock_port, lane, sizes[lane])}", clock=True, modes=active_modes)
            continue

        mux, clock_or = f"mux_{lane}", f"clk_or_{lane}"
        if show_data:
            node(logic_nodes, mux, f"pipe_lane_data_mux / PHY {span(lane, sizes[lane])}" + copies(lane))
        if show_clock:
            node(logic_nodes, clock_or, f"pipe_lane_clk_or / PHY {span(lane, sizes[lane])}"
                 + (copies(lane) if pclk_mode == "pclk_as_phy_input" else ""))
        for branch, (source, cl, label, active_modes) in enumerate(branches):
            gate = f"clk_gate_{lane}_{branch}"
            if show_data:
                edge(source, mux, f"lane {span(cl, sizes[lane])}; {label}",
                     modes=active_modes, conditional=source == "dpr")
            clock_source = port("pclk", cl, sizes[lane]) if pclk_mode == "pclk_as_phy_input" else "ctrl_pclk"
            if source == "dpr":
                clock_source = "dpr_pclk"
            if show_clock:
                node(logic_nodes, gate, f"pipe_lane_clk_gate / PHY {span(lane, sizes[lane])} / branch {branch}"
                     + (copies(lane) if pclk_mode == "pclk_as_phy_input" else ""))
                edge(source, gate, f"{clock_source}; {label}", clock=True,
                     modes=active_modes, conditional=source == "dpr")
                edge(gate, clock_or, "gated clock", clock=True,
                     modes=active_modes, conditional=source == "dpr")
        if show_data:
            edge(mux, phy, "MAC -> PHY data")
        if show_clock:
            edge(clock_or, phy, port(clock_port, lane, sizes[lane]), clock=True)

    if path == "reset":
        resets = ("rst_n", "perst_n") if pclk_mode == "pclk_as_phy_input" else ("rst_n",)
        for lane in lanes:
            owners = {}
            for (cid, _cl), modes in routes.get(lane, {}).items():
                owners.setdefault(cid, set()).update(modes)
            for reset in resets:
                branches = [
                    (f"ctrl_{cid}", f"ctrl_{reset}[{cid}]", "mode=" + ",".join(map(str, sorted(modes))), modes)
                    for cid, modes in sorted(owners.items())
                ]
                if dpr:
                    branches.append(("dpr", f"dpr_{reset}", f"DPR owns PHY {lane}", None))
                mux = f"reset_{reset}_{lane}"
                if len(branches) > 1 or dpr:
                    node(logic_nodes, mux, f"pipe_lane_data_mux / {reset} / dec_tgt / safe=0")
                    for source, reset_source, label, active_modes in branches:
                        edge(source, mux, f"{reset_source}; {label}; dec_tgt",
                             modes=active_modes, conditional=source == "dpr")
                    edge(mux, f"phy_{lane}", port(f"phy_{reset}", lane, sizes[lane]))
                elif branches:
                    source, reset_source, label, active_modes = branches[0]
                    edge(source, f"phy_{lane}", f"{reset_source} -> {port('phy_' + reset, lane, sizes[lane])}; {label}",
                         modes=active_modes)

    if path == "p2m":
        inputs = {}
        for lane in lanes:
            for (cid, cl), modes in routes.get(lane, {}).items():
                inputs.setdefault((cid, cl), {})[lane] = modes
        select = ("rxclk_en" if dpr else "seltgt") if pclk_mode == "pclk_as_phy_input" else "sel_tgt"
        for cid in sorted(visible_controllers):
            width = controllers[cid][1]
            ports = sorted(cl for owner, cl in inputs if owner == cid)
            port_widths = {cl: sizes[next(iter(inputs[cid, cl]))] for cl in ports}
            covered = {index for cl in ports for index in range(cl, cl + port_widths[cl])}
            unused = sorted(set(range(width or 0)) - covered) if phy_lanes is None else []
            tied_ports = [cl for cl in ports if any(len(routes[lane]) > 1 for lane in inputs[cid, cl])]
            tie = f"p2m_tie_{cid}"
            if unused or tied_ports:
                node(tie_nodes, tie, f"Controller {cid} p2m_tie / configured tie_off")
            if unused:
                edge(tie, f"ctrl_{cid}", "never mapped lanes=" + ",".join(map(str, unused)))
            for cl in ports:
                sources = inputs[cid, cl]
                direct = len(sources) == 1 and len(routes[next(iter(sources))]) == 1 and not dpr
                mux = f"p2m_mux_{cid}_{cl}"
                if not direct:
                    count = port_widths[cl]
                    node(logic_nodes, mux, f"pipe_lane_data_mux / Ctrl {cid} lane {span(cl, count)} / SAFE_P2M"
                         + (f" / x{count} independent" if count > 1 else ""))
                    if cl in tied_ports:
                        port_modes = {active for modes in sources.values() for active in modes}
                        edge(tie, mux, "another real owner active", modes=modes_seen - port_modes)
                for lane, modes in sorted(sources.items()):
                    label = "mode=" + ",".join(map(str, sorted(modes)))
                    target = f"ctrl_{cid}" if direct else mux
                    edge(f"phy_{lane}", target,
                         f"{port('phy_phy2mac', lane, sizes[lane])} -> ctrl lane {span(cl, port_widths[cl])}; {label}"
                         + ("" if direct else f"; {select}"), modes=modes)
                if not direct:
                    edge(mux, f"ctrl_{cid}", f"ctrl lane {span(cl, port_widths[cl])}; PHY -> MAC data")
        if dpr:
            for lane in lanes:
                mux = f"p2m_dpr_{lane}"
                node(logic_nodes, mux, "pipe_lane_data_mux / DPR tap / N=1 / SAFE_P2M" + copies(lane))
                edge(f"phy_{lane}", mux, f"{port('phy_phy2mac', lane, sizes[lane])}; synchronized {select} DPR bit", conditional=True)
                edge(mux, "dpr", port("dpr_phy2mac", lane, sizes[lane]), conditional=True)

    if path == "clock" and pclk_mode == "pclk_as_phy_input":
        # RX recovered clocks run in the reverse direction, independently of
        # the controller-generated PCLK that feeds each PHY lane above.
        for lane in lanes:
            candidates = routes.get(lane, {})
            rx_source = f"phy_rx_source_{lane}"
            node(clock_sources, rx_source, f"PHY rx_clk / lane {span(lane, sizes[lane])}")
            for (cid, cl), modes in sorted(candidates.items()):
                label = "mode=" + ",".join(map(str, sorted(modes)))
                if len(candidates) == 1 and not dpr:
                    edge(rx_source, f"ctrl_{cid}",
                         f"{port('rx_clk', lane, sizes[lane])} -> {port('rx_clk', cl, sizes[lane])}; {label}", clock=True, modes=modes)
                else:
                    gate = f"rxclk_gate_{lane}_{cid}_{cl}"
                    node(source_gates, gate, f"pipe_lane_clk_gate / RX PHY {span(lane, sizes[lane])}" + copies(lane))
                    edge(rx_source, gate, f"{port('rx_clk', lane, sizes[lane])}; rxclk_en; {label}", clock=True, modes=modes)
                    edge(gate, f"ctrl_{cid}", port("rx_clk", cl, sizes[lane]), clock=True, modes=modes)
            if dpr:
                gate = f"rxclk_gate_{lane}_dpr"
                node(source_gates, gate, f"pipe_lane_clk_gate / DPR RX PHY {span(lane, sizes[lane])}" + copies(lane))
                edge(rx_source, gate, f"{port('rx_clk', lane, sizes[lane])}; synchronized DPR rxclk_en", clock=True, conditional=True)
                edge(gate, "dpr", port("dpr_rx_clk", lane, sizes[lane]), clock=True, conditional=True)
        if phy_lanes is None:
            used_ports = {(cid, cl) for per_lane in routes.values() for cid, cl in per_lane}
            for cid in sorted(visible_controllers):
                unused = [cl for cl in range(controllers[cid][1] or 0) if (cid, cl) not in used_ports]
                if unused:
                    tie = f"rxclk_tie_{cid}"
                    node(clock_sources, tie, "constant 0")
                    edge(tie, f"ctrl_{cid}", "unused rx_clk lanes=" + ",".join(map(str, unused)), clock=True)

    if path == "clock" and pclk_mode == "pclk_as_phy_output":
        # A validated PLM controller has one fixed lowest PHY clock source.
        # Derive it from the mapping, rather than drawing a fictitious MUX.
        for lane in lanes:
            node(clock_sources, f"phy_pclk_source_{lane}", f"PHY pclk_out / lane {span(lane, sizes[lane])}")
        for cid in sorted(visible_controllers):
            lowest_by_mode = {}
            for lane, candidates in routes.items():
                for (owner, _cl), modes in candidates.items():
                    if owner == cid:
                        for mapped_mode in modes:
                            lowest_by_mode[mapped_mode] = min(lane, lowest_by_mode.get(mapped_mode, lane))
            for lane in sorted(set(lowest_by_mode.values()) & set(lanes)):
                edge(f"phy_pclk_source_{lane}", f"ctrl_{cid}", f"phy_pclk_out[{lane}] -> ctrl_pclk", clock=True)
        if dpr:
            node(source_gates, "dpr_pclk_or", "pipe_lane_clk_or / DPR working clock")
            for lane in lanes:
                gate = f"dpr_pclk_gate_{lane}"
                node(source_gates, gate, f"pipe_lane_clk_gate / DPR clock source PHY {span(lane, sizes[lane])}" + copies(lane))
                edge(f"phy_pclk_source_{lane}", gate, f"{port('phy_pclk_out', lane, sizes[lane])}; synchronized lowest mask bit / idle default", clock=True, conditional=True)
                edge(gate, "dpr_pclk_or", "gated clock", clock=True, conditional=True)
            edge("dpr_pclk_or", "dpr", "dpr_pclk", clock=True, conditional=True)

    logic_title = {"reset": "Reset Logic", "p2m": "PHY to MAC Data", "m2p": "MAC to PHY Data",
                   "clock": "Clock Logic"}.get(path, "Mux / Clock Logic")
    lines = ["graph LR", '  %%{init: {"flowchart": {"defaultRenderer": "elk"}}}%%',
             "  %% Logical routing; dotted edges are clocks."]
    groups = [
        ("clock_sources", "PHY Clock Source Ports", clock_sources),
        ("source_gates", "Clock Source Gates", source_gates),
        ("controllers", "Controllers", controller_nodes),
        ("logic", logic_title, logic_nodes),
        ("phys", "PHY Lanes", phy_nodes),
    ]
    if path == "p2m":
        groups = [("phys", "PHY Lanes", phy_nodes), ("ties", "P2M tie-off", tie_nodes),
                  ("logic", logic_title, logic_nodes), ("controllers", "Controllers", controller_nodes)]
    for group, title, nodes in groups:
        if nodes:
            # A flat graph lets ELK route around every component. Nested
            # containers make dense clock fan-outs expensive to lay out.
            lines.extend([f"  %% {title}", *nodes])
    # Mermaid 10's ELK renderer omits edge stroke styles. Supply matching CSS
    # as well as linkStyle so the offline viewer and exported diagram agree.
    edge_css = []
    active_color = topology_mode_color(mode, modes_seen) if mode is not None else None
    for index, (source, target, active_modes, conditional, clock) in enumerate(edge_states):
        declarations = ["stroke-dasharray:3 3" if clock else "stroke-dasharray:none"]
        if mode is not None:
            active = active_modes is None or mode in active_modes
            color = CONDITIONAL_ROUTE_COLOR if conditional else active_color if active else INACTIVE_ROUTE_COLOR
            width = "2.5px" if active or conditional else "1px"
            opacity = "1" if active or conditional else "0.45"
            styles.append(f"  linkStyle {index} stroke:{color},stroke-width:{width},color:{color},opacity:{opacity}")
            declarations.extend((f"stroke:{color}", f"stroke-width:{width}", f"opacity:{opacity}"))
        selector = f".flowchart-link.LS-{source}.LE-{target}:nth-of-type({index + 1})"
        edge_css.append(selector + "{" + ";".join(declarations) + "}")
    config = {"flowchart": {"defaultRenderer": "elk"}, "themeCSS": "".join(edge_css)}
    lines[1] = "  %%{init: " + json.dumps(config, separators=(",", ":")) + "}%%"
    return "\n".join([*lines, *edges, *styles]) + "\n"
