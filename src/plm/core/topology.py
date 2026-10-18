"""Generate a Mermaid summary of forward lane data and clock routing.

This is a logical per-lane view, not an RTL instance/netlist export: clock
gates shared by several lanes in the RTL are drawn beside each lane.
"""

from collections.abc import Mapping

from plm.core.model import _decimal_int


# Same palette order as the controller swatches in the lane-mapping editor.
# Keep the exporter independent of Qt so it also works in CLI-only installs.
_COLORS = ("#4F46E5", "#0D9488", "#7C3AED", "#D97706", "#059669", "#E11D48")


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _label(value):
    """Escape user text using Mermaid's decimal character entities."""
    return "".join(
        char if char.isalnum() or char in " _-:;,.=/" else f"#{ord(char)};"
        for char in str(value).replace("\r", " ").replace("\n", " ")
    )


def generate_mermaid_topology(model) -> str:
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
    PCLK or PCLK feedback. PHY->MAC data and recovered clocks are omitted.
    Repeated routes across modes are merged and labelled with those modes.
    Single-candidate routes bypass the mux/gates when DPR is disabled.
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
        mode = _decimal_int(raw_mode)
        if mode in modes_seen:
            raise ValueError(f"Duplicate mode: {mode}")
        modes_seen.add(mode)
        lanes_seen = set()
        for raw_lane, owner in per_lane.items():
            lane = _decimal_int(raw_lane)
            cid, ctrl_lane = map(_decimal_int, owner)
            if lane < 0 or lane in lanes_seen or cid not in controllers or ctrl_lane < 0:
                raise ValueError(f"Invalid mapping at mode {mode}, PHY lane {lane}: {owner}")
            lanes_seen.add(lane)
            width = controllers[cid][1]
            if width is not None and ctrl_lane >= width:
                raise ValueError(f"Controller {cid} lane {ctrl_lane} exceeds max_width={width}")
            routes.setdefault(lane, {}).setdefault((cid, ctrl_lane), []).append(mode)

    inferred_count = max(routes, default=-1) + 1
    lane_count = _decimal_int(_field(model, "lane_count", inferred_count))
    if lane_count < inferred_count or lane_count < 0:
        raise ValueError("lane_count does not cover all mapped PHY lanes")

    options = _field(model, "options", model)
    pclk_mode = _field(options, "pclk_mode", "pclk_as_phy_output")
    if pclk_mode not in ("pclk_as_phy_output", "pclk_as_phy_input"):
        raise ValueError(f"Unsupported pclk_mode: {pclk_mode}")
    dpr = _field(options, "dpr", False)
    if not isinstance(dpr, bool):
        raise ValueError("dpr must be a bool")
    clock_port = "phy_pclk" if pclk_mode == "pclk_as_phy_input" else "phy_pclk_in"

    controller_nodes, logic_nodes, phy_nodes, edges, styles = [], [], [], [], []

    def node(target, node_id, text):
        target.append(f'    {node_id}["{_label(text)}"]')

    def edge(source, target, text, clock=False):
        arrow = "-.->" if clock else "-->"
        edges.append(f'  {source} {arrow}|"{_label(text)}"| {target}')

    for index, (cid, (name, width)) in enumerate(controllers.items()):
        node(controller_nodes, f"ctrl_{cid}", f"{cid}: {name}")
        color = _COLORS[index % len(_COLORS)]
        styles.append(f"  style ctrl_{cid} fill:#FFFFFF,stroke:{color},stroke-width:3px,color:#111827")
    if dpr:
        node(controller_nodes, "dpr", "DPR")

    for lane in range(lane_count):
        phy = f"phy_{lane}"
        candidates = routes.get(lane, {})
        node(phy_nodes, phy, f"PHY Lane {lane}" + (" (unmapped)" if not candidates else ""))
        branches = [
            (f"ctrl_{cid}", ctrl_lane, "mode=" + ",".join(map(str, sorted(modes))))
            for (cid, ctrl_lane), modes in sorted(candidates.items())
        ]
        if dpr:
            branches = [(source, cl, label + f"; v_lane_mask[{lane}]=0")
                        for source, cl, label in branches]
            branches.append(("dpr", lane, f"v_lane_mask[{lane}]=1"))
        if not branches:
            continue
        if len(branches) == 1 and not dpr:
            source, cl, label = branches[0]
            edge(source, phy, f"lane {cl}; {label}; data")
            port = f"pclk[{cl}]" if pclk_mode == "pclk_as_phy_input" else "ctrl_pclk"
            edge(source, phy, f"{port} -> {clock_port}[{lane}]", clock=True)
            continue

        mux, clock_or = f"mux_{lane}", f"clk_or_{lane}"
        node(logic_nodes, mux, f"pipe_lane_data_mux / PHY {lane}")
        node(logic_nodes, clock_or, f"pipe_lane_clk_or / PHY {lane}")
        for branch, (source, cl, label) in enumerate(branches):
            gate = f"clk_gate_{lane}_{branch}"
            node(logic_nodes, gate, f"pipe_lane_clk_gate / PHY {lane} / branch {branch}")
            edge(source, mux, f"lane {cl}; {label}")
            port = f"pclk[{cl}]" if pclk_mode == "pclk_as_phy_input" else "ctrl_pclk"
            if source == "dpr":
                port = "dpr_pclk"
            edge(source, gate, f"{port}; {label}", clock=True)
            edge(gate, clock_or, "gated clock", clock=True)
        edge(mux, phy, "MAC -> PHY data")
        edge(clock_or, phy, f"{clock_port}[{lane}]", clock=True)

    lines = ["graph LR", "  %% Logical forward routes; dotted edges are clocks."]
    for group, title, nodes in (
        ("controllers", "Controllers", controller_nodes),
        ("logic", "Mux / Clock Logic", logic_nodes),
        ("phys", "PHY Lanes", phy_nodes),
    ):
        if nodes:
            lines.extend([f'  subgraph {group}["{title}"]', *nodes, "  end"])
    return "\n".join([*lines, *edges, *styles]) + "\n"
