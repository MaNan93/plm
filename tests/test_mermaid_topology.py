import copy
import re
from types import SimpleNamespace
import unittest

from plm.core.gen_flags import GenerationOptions
from plm.core.model import Config
from plm.core.topology import (
    CONDITIONAL_ROUTE_COLOR, INACTIVE_ROUTE_COLOR,
    generate_mermaid_topology, topology_mode_color,
)
from sample_data import SAMPLES_DIR


class MermaidTopologyTests(unittest.TestCase):
    def test_data_mux_module_shows_ports_and_safe_normalization(self):
        graph = generate_mermaid_topology(self.model(), path="data_mux")
        self.assertIn('subgraph data_mux_module["pipe_lane_data_mux / parameters N, WIDTH"]', graph)
        for port in ('din', 'sel', 'safe', 'dout'):
            self.assertRegex(graph, rf'(?m)^  {port}\[')
        self.assertIn('din[i] ^ safe', graph)
        self.assertIn('normalized[i] & sel[i]', graph)
        self.assertIn('OR reduction / acc', graph)
        self.assertIn('acc ^ safe', graph)
        self.assertIn('sel=0: dout=safe', graph)
        self.assertIn('one-hot or all 0', graph)
        self.assertNotIn('ctrl_', graph)
        self.assertNotIn('-.->', graph)
        self.assertEqual(graph, generate_mermaid_topology(self.wide_model(), path="data_mux", mode=1, compact=True))

    def test_numeric_node_order_preserves_controller_palette(self):
        model = {
            'controllers': [{'id': cid, 'name': f'Ctrl{cid}', 'max_width': 1}
                            for cid in (2, 10, 0, 1)],
            'lane_count': 12,
            'lane_mapping': {0: {10: (2, 0), 2: (10, 0), 1: (0, 0), 0: (1, 0)}},
        }
        for path in ('all', 'clock', 'reset', 'm2p', 'p2m'):
            with self.subTest(path=path):
                graph = generate_mermaid_topology(model, path=path)
                ids = list(map(int, re.findall(r'^\s+ctrl_(\d+)\[', graph, re.MULTILINE)))
                lanes = list(map(int, re.findall(r'^\s+phy_(\d+)\[', graph, re.MULTILINE)))
                self.assertEqual(ids, [0, 1, 2, 10])
                self.assertEqual(lanes, list(range(12)))
                self.assertIn('style ctrl_2 fill:#FFFFFF,stroke:#4F46E5', graph)
                self.assertIn('"forceNodeModelOrder":true', graph)

    def test_mode_palette_reserves_gray_and_amber_for_other_states(self):
        for mode in range(12):
            self.assertNotIn(topology_mode_color(mode, range(12)),
                             (INACTIVE_ROUTE_COLOR, CONDITIONAL_ROUTE_COLOR))

    def colored_edges(self, graph):
        edges = [line.strip() for line in graph.splitlines()
                 if re.match(r"\s+\w+ (?:-->|-\.->)\|", line)]
        styles = {int(index): color for index, color in re.findall(r"linkStyle (\d+) stroke:(#[0-9A-Fa-f]+),", graph)}
        self.assertEqual(set(styles), set(range(len(edges))))
        return {line: styles[index] for index, line in enumerate(edges)}

    def assert_edge_color(self, edges, start, color):
        matches = [actual for line, actual in edges.items() if line.startswith(start)]
        self.assertTrue(matches, start)
        self.assertTrue(all(actual == color for actual in matches), (start, matches, color))
    def model(self):
        return {
            "controllers": [
                {"id": 7, "name": "CtrlA", "max_width": 2},
                {"id": 12, "name": "CtrlB", "max_width": 1},
            ],
            "lane_mapping": {
                0: {0: (7, 0), 1: (7, 1)},
                2: {0: (7, 0), 1: (12, 0)},
            },
        }

    def test_direct_and_switched_paths_preserve_ports_and_modes(self):
        graph = generate_mermaid_topology(self.model())
        self.assertTrue(graph.startswith("graph LR\n"))
        self.assertIn('ctrl_7["7: CtrlA"]', graph)
        self.assertIn('ctrl_7 -->|"lane 0; data"| phy_0', graph)
        self.assertNotIn("mux_0", graph)
        self.assertNotIn("clk_switch_0", graph)
        self.assertIn('ctrl_7 -->|"lane 1"| mux_1', graph)
        self.assertIn('ctrl_12 -->|"lane 0"| mux_1', graph)
        self.assertIn('ctrl_7 -.->|"ctrl_pclk"| clk_switch_1', graph)
        self.assertIn('clk_switch_1["Clock switch / 2:1 / PHY 1"]', graph)
        self.assertIn('clk_switch_1 -.->|"phy_pclk_in[1]"| phy_1', graph)
        self.assertNotIn("clk_gate_", graph)
        self.assertNotIn("clk_or_", graph)

    def test_phy_input_clock_and_dpr_on_plan_shaped_object(self):
        model = self.model()
        plan = SimpleNamespace(
            controllers=model["controllers"], mapping=model["lane_mapping"],
            options=GenerationOptions(arch="serdes", pclk_mode="pclk_as_phy_input", dpr=True),
        )
        graph = generate_mermaid_topology(plan)
        self.assertIn('dpr["DPR"]', graph)
        self.assertIn('dpr -->|"lane 0; v_lane_mask[0]=1"| mux_0', graph)
        self.assertIn('ctrl_7 -.->|"pclk[1]; v_lane_mask[1]=0"| clk_switch_1', graph)
        self.assertIn('clk_switch_1["Clock switch / 3:1 / PHY 1"]', graph)
        self.assertIn('clk_switch_1 -.->|"phy_pclk[1]"| phy_1', graph)

    def test_names_cannot_inject_nodes_or_html(self):
        model = self.model()
        model["controllers"][0]["name"] = 'end"]\nmalicious["<script>|&`'
        graph = generate_mermaid_topology(model)
        self.assertIn("#34;#93; malicious#91;#34;#60;script#62;#124;#38;#96;", graph)
        self.assertNotIn('\nmalicious[', graph)
        self.assertNotIn("<script>", graph)

    def test_mutations_are_reflected_without_changing_input(self):
        model = self.model()
        before = copy.deepcopy(model)
        graph = generate_mermaid_topology(model)
        self.assertEqual(model, before)
        self.assertEqual(graph, generate_mermaid_topology(model))
        model["lane_mapping"][2][1] = (7, 1)
        self.assertNotIn("mux_1", generate_mermaid_topology(model))

    def test_declared_unmapped_lanes_and_empty_model(self):
        model = self.model()
        model["lane_count"] = 3
        self.assertIn('phy_2["PHY Lane 2 #40;unmapped#41;"]', generate_mermaid_topology(model))
        self.assertTrue(generate_mermaid_topology({}).startswith("graph LR\n"))

    def test_invalid_references_and_options_raise(self):
        for owner in ((99, 0), (7, 2), (7, -1)):
            with self.subTest(owner=owner):
                model = self.model()
                model["lane_mapping"][0][0] = owner
                with self.assertRaises(ValueError):
                    generate_mermaid_topology(model)
        for field, value in (("lane_count", 1), ("pclk_mode", "invalid"), ("dpr", "false")):
            with self.subTest(field=field):
                model = self.model()
                model[field] = value
                with self.assertRaises(ValueError):
                    generate_mermaid_topology(model)

    def test_all_bundled_configs_work_with_controller_dicts(self):
        for path in sorted(SAMPLES_DIR.glob("*/config")):
            with self.subTest(sample=path.parent.name):
                cfg = Config(path)
                graph = generate_mermaid_topology(cfg)
                self.assertEqual(graph.count('["PHY Lane '), cfg.lane_count)
                for cid, controller in cfg.controllers.items():
                    self.assertIn(f'ctrl_{cid}["{cid}: {controller.name}"]', graph)

    def test_csv_controller_field_names_and_string_indices(self):
        model = {
            "controllers": [{"ctrl_id": "07", "ctrl_name": "控制器甲", "max_width": "1"}],
            "lane_mapping": {"02": {"0": ("07", "0")}},
        }
        graph = generate_mermaid_topology(model)
        self.assertIn('ctrl_7["7: 控制器甲"]', graph)
        self.assertIn('ctrl_7 -->|"lane 0; data"| phy_0', graph)

    def test_lane_selection_keeps_physical_numbers_and_original_colors(self):
        graph = generate_mermaid_topology(self.model(), phy_lanes=[1], path="data")
        self.assertEqual(graph.count('["PHY Lane '), 1)
        self.assertIn('phy_1["PHY Lane 1"]', graph)
        self.assertNotIn("phy_0", graph)
        self.assertIn("stroke:#0D9488", graph)
        graph = generate_mermaid_topology(self.model(), phy_lanes=[0])
        self.assertNotIn("ctrl_12", graph)
        self.assertIn("stroke:#4F46E5", graph)

    def test_data_and_clock_diagrams_have_separate_routes(self):
        model = self.model()
        model["dpr"] = True
        data = generate_mermaid_topology(model, phy_lanes=[1], path="data")
        clock = generate_mermaid_topology(model, phy_lanes=[1], path="clock")
        self.assertIn("pipe_lane_data_mux", data)
        self.assertNotIn("clk_gate_", data)
        self.assertNotIn("clk_or_", data)
        self.assertNotIn("-.->", data)
        self.assertIn("Clock switch", clock)
        self.assertNotIn("pipe_lane_clk_gate", clock)
        self.assertNotIn("pipe_lane_clk_or", clock)
        self.assertNotIn("mux_", clock)
        self.assertNotIn(" -->", clock)
        for graph in (data, clock):
            self.assertIn('dpr["DPR"]', graph)
            self.assertNotIn("mode=", graph)

    def test_direct_routes_can_be_split_and_invalid_filters_are_rejected(self):
        data = generate_mermaid_topology(self.model(), phy_lanes=[0], path="data")
        clock = generate_mermaid_topology(self.model(), phy_lanes=[0], path="clock")
        self.assertIn(" -->", data)
        self.assertNotIn("-.->", data)
        self.assertIn("-.->", clock)
        self.assertNotIn(" -->", clock)
        for selected in ([], [-1], [2]):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                generate_mermaid_topology(self.model(), phy_lanes=selected)
        with self.assertRaises(ValueError):
            generate_mermaid_topology(self.model(), path="invalid")

    def test_reset_uses_raw_decoder_and_separate_serdes_perst(self):
        model = self.model()
        graph = generate_mermaid_topology(model, path="reset")
        self.assertIn('ctrl_7 -->|"ctrl_rst_n[7] -> phy_rst_n[0]"| phy_0', graph)
        self.assertIn("reset_rst_n_1", graph)
        self.assertIn("dec_tgt<br/>safe=0", graph)
        self.assertNotIn("perst_n", graph)
        self.assertNotIn("clk_gate", graph)
        model.update(pclk_mode="pclk_as_phy_input", dpr=True)
        graph = generate_mermaid_topology(model, path="reset")
        self.assertIn("reset_perst_n_0", graph)
        self.assertIn("dpr_perst_n", graph)
        self.assertIn("ctrl_perst_n", graph)
        self.assertNotIn("sel_tgt", graph)

    def test_p2m_uses_reverse_edges_and_converges_by_controller_port(self):
        model = self.model()
        model["lane_mapping"] = {
            0: {0: (7, 0), 1: (12, 0)},
            2: {0: (12, 0), 1: (7, 0)},
        }
        graph = generate_mermaid_topology(model, path="p2m")
        self.assertIn('phy_0 -->|"phy_phy2mac[0] -> ctrl lane 0; sel_tgt"| p2m_mux_7_0', graph)
        self.assertIn('phy_1 -->|"phy_phy2mac[1] -> ctrl lane 0; sel_tgt"| p2m_mux_7_0', graph)
        self.assertIn('p2m_mux_7_0 -->|"ctrl lane 0; PHY -> MAC data"| ctrl_7', graph)
        self.assertIn("never mapped lanes=1", graph)
        self.assertIn("another real owner active", graph)
        self.assertNotIn("ctrl_7 -->", graph)
        self.assertNotIn("clk_gate", graph)
        self.assertNotIn("reset_", graph)

    def test_p2m_dpr_is_a_separate_synchronized_read_tap(self):
        model = self.model()
        model.update(pclk_mode="pclk_as_phy_input", dpr=True)
        graph = generate_mermaid_topology(model, path="p2m")
        self.assertIn("p2m_dpr_0", graph)
        self.assertIn("synchronized rxclk_en DPR bit", graph)
        self.assertIn('p2m_dpr_0 -->|"dpr_phy2mac[0]"| dpr', graph)
        self.assertNotIn("dpr -->", graph)
        self.assertNotIn("v_lane_mask", graph)

    def test_clock_view_includes_phy_output_pclk_or_recovered_rx_clock(self):
        model = self.model()
        graph = generate_mermaid_topology(model, path="clock")
        self.assertIn('phy_pclk_source_0 -.->|"phy_pclk_out[0] -> ctrl_pclk"| ctrl_7', graph)
        self.assertNotIn("rx_clk", graph)
        model.update(pclk_mode="pclk_as_phy_input", dpr=True)
        graph = generate_mermaid_topology(model, path="clock")
        self.assertIn("rxclk_switch_1_12_0", graph)
        self.assertIn("Clock switch / 2:1 / RX PHY 1 / else=0", graph.replace("<br/>", " / "))
        self.assertIn("dpr_rx_clk", graph)
        self.assertNotIn("phy_pclk_out", graph)
        model["pclk_mode"] = "pclk_as_phy_output"
        graph = generate_mermaid_topology(model, path="clock")
        self.assertIn('phy_pclk_source_1 -.->', graph)
        self.assertIn('dpr_pclk_switch -.->|"dpr_pclk"| dpr', graph)

    def test_all_categories_include_all_phy_lanes_and_legacy_data_alias(self):
        for path in ("clock", "reset", "m2p", "p2m"):
            with self.subTest(path=path):
                graph = generate_mermaid_topology(self.model(), path=path)
                self.assertEqual(graph.count('["PHY Lane '), 2)
                self.assertNotIn("mode=", graph)
        graph = generate_mermaid_topology(self.model(), path="m2p")
        self.assertIn("mux_1", graph)
        self.assertNotIn("clk_gate", graph)
        self.assertNotIn("p2m_mux", graph)

    def wide_model(self):
        return {
            "controllers": [{"id": 0, "name": "A", "max_width": 8},
                            {"id": 1, "name": "B", "max_width": 4}],
            "lane_mapping": {
                0: {lane: (0, lane) for lane in range(8)},
                1: {lane: (0, lane) if lane < 4 else (1, lane - 4) for lane in range(8)},
            },
        }

    def test_compact_categories_preserve_bus_ranges_and_replication(self):
        model = self.wide_model()
        model["dpr"] = True
        for path in ("clock", "reset", "m2p", "p2m"):
            with self.subTest(path=path):
                graph = generate_mermaid_topology(model, path=path, compact=True)
                self.assertIn('phy_0["PHY Lanes 0..3 / x4"]', graph)
                self.assertIn('phy_4["PHY Lanes 4..7 / x4"]', graph)
                self.assertNotIn('phy_1[', graph)
                if path in ("m2p", "p2m"):
                    self.assertIn("x4 independent", graph)
                elif path == "clock":
                    self.assertNotIn("x4 independent", graph)
        p2m = generate_mermaid_topology(model, path="p2m", compact=True)
        self.assertIn("ctrl lane 4..7", p2m)
        self.assertNotIn("never mapped", p2m)
        m2p = generate_mermaid_topology(model, path="m2p", compact=True)
        self.assertIn("v_lane_mask[7:4]=all 1", m2p)
        reset = generate_mermaid_topology(model, path="reset", compact=True)
        self.assertNotIn("x4 independent", reset)
        self.assertIn("phy_rst_n[7:4]", reset)
        model["pclk_mode"] = "pclk_as_phy_input"
        clock = generate_mermaid_topology(model, path="clock", compact=True)
        self.assertIn("pclk[7:4]", clock)
        self.assertIn("rx_clk[7:4]", clock)
        self.assertIn("x4 independent", clock)

    def test_compact_does_not_merge_different_ports_or_fine_grained_owners(self):
        model = self.wide_model()
        model["lane_mapping"][1].update({4: (1, 0), 5: (1, 2), 6: (1, 1), 7: (1, 3)})
        graph = generate_mermaid_topology(model, path="m2p", compact=True)
        self.assertIn('phy_0["PHY Lanes 0..3 / x4"]', graph)
        for lane in range(4, 8):
            self.assertIn(f'phy_{lane}["PHY Lane {lane}"]', graph)
        self.assertEqual(generate_mermaid_topology(self.model(), compact=True),
                         generate_mermaid_topology(self.model()))

    def test_clock_switch_counts_inputs_and_keeps_selection_conditions(self):
        model = self.wide_model()
        model['dpr'] = True
        graph = generate_mermaid_topology(model, path='clock', compact=True, mode=1)
        # Two PHY bus blocks, but DPR selects one of eight physical clocks.
        self.assertIn('clk_switch_0["Clock switch / 2:1 / PHY 0..3"]', graph)
        self.assertIn('clk_switch_4["Clock switch / 3:1 / PHY 4..7"]', graph)
        self.assertIn('dpr_pclk_switch["Clock switch / 8:1 / DPR working clock"]', graph)
        self.assertEqual(len(re.findall(r'^\s+\w+\["Clock switch', graph, re.MULTILINE)), 3)
        self.assertIn('v_lane_mask[7:4]=all 0', graph)
        self.assertIn('v_lane_mask[7:4]=all 1', graph)
        self.assertIn('phy_pclk_out[7:4]', graph)
        self.assertIn('synchronized lowest mask bit / idle default', graph)
        self.assertNotIn('pipe_lane_clk_gate', graph)
        self.assertNotIn('pipe_lane_clk_or', graph)

    def test_compact_refines_buses_when_p2m_candidate_slices_differ(self):
        model = {
            "controllers": [{"id": cid, "name": str(cid), "max_width": 6} for cid in (0, 1)],
            "lane_mapping": {
                0: {lane: (lane // 6, lane % 6) for lane in range(12)},
                1: {lane: (1, lane) if lane < 6 else (0, (lane - 3) % 6) for lane in range(12)},
            },
        }
        graph = generate_mermaid_topology(model, path="p2m", compact=True)
        for lane in (0, 3, 6, 9):
            self.assertIn(f'phy_{lane}["PHY Lanes {lane}..{lane+2} / x3"]', graph)
        self.assertIn("ctrl lane 0..2", graph)
        self.assertIn("ctrl lane 3..5", graph)
        self.assertNotIn("ctrl lane 0..5", graph)
        self.assertNotIn("never mapped", graph)

    def test_selected_mode_colors_m2p_and_entire_clock_branches(self):
        active = topology_mode_color(2, (0, 2))
        edges = self.colored_edges(generate_mermaid_topology(self.model(), path="m2p", mode=2))
        self.assert_edge_color(edges, 'ctrl_7 -->|"lane 1"', INACTIVE_ROUTE_COLOR)
        self.assert_edge_color(edges, 'ctrl_12 -->', active)
        self.assert_edge_color(edges, 'mux_1 -->', active)
        model = self.model()
        model["pclk_mode"] = "pclk_as_phy_input"
        edges = self.colored_edges(generate_mermaid_topology(model, path="clock", mode=2))
        self.assert_edge_color(edges, 'ctrl_7 -.->|"pclk[1]"', INACTIVE_ROUTE_COLOR)
        self.assert_edge_color(edges, 'ctrl_12 -.->|"pclk[0]"', active)
        self.assert_edge_color(edges, "clk_switch_1 -.->", active)
        self.assert_edge_color(edges, "rxclk_switch_1_7_1 -.->", INACTIVE_ROUTE_COLOR)
        self.assert_edge_color(edges, "rxclk_switch_1_12_0 -.->", active)

    def test_mode_highlights_reset_and_p2m_tie_fallback(self):
        active = topology_mode_color(2, (0, 2))
        edges = self.colored_edges(generate_mermaid_topology(self.model(), path="reset", mode=2))
        self.assert_edge_color(edges, 'ctrl_7 -->|"ctrl_rst_n[7]; dec_tgt', INACTIVE_ROUTE_COLOR)
        self.assert_edge_color(edges, 'ctrl_12 -->', active)
        self.assert_edge_color(edges, 'reset_rst_n_1 -->', active)
        edges = self.colored_edges(generate_mermaid_topology(self.model(), path="p2m", mode=2))
        self.assert_edge_color(edges, 'phy_1 -->|"phy_phy2mac[1] -> ctrl lane 1; sel_tgt', INACTIVE_ROUTE_COLOR)
        self.assert_edge_color(edges, 'p2m_tie_7 -->', active)
        self.assert_edge_color(edges, 'p2m_tie_12 -->', INACTIVE_ROUTE_COLOR)

    def test_dpr_routes_remain_conditional_and_invalid_mode_is_rejected(self):
        model = self.wide_model()
        model.update(dpr=True, pclk_mode="pclk_as_phy_input")
        for path in ("clock", "reset", "m2p", "p2m"):
            with self.subTest(path=path):
                graph = generate_mermaid_topology(model, path=path, compact=True, mode=1)
                edges = self.colored_edges(graph)
                self.assertIn(CONDITIONAL_ROUTE_COLOR, edges.values())
                self.assertIn(INACTIVE_ROUTE_COLOR, edges.values())
                self.assertIn(topology_mode_color(1, (0, 1)), edges.values())
                for line, color in edges.items():
                    if line.startswith("dpr ") or line.endswith("| dpr"):
                        self.assertEqual(color, CONDITIONAL_ROUTE_COLOR)
        self.assertNotIn("linkStyle", generate_mermaid_topology(self.model()))
        with self.assertRaisesRegex(ValueError, "Undefined topology mode"):
            generate_mermaid_topology(self.model(), mode=99)
        self.assertEqual(generate_mermaid_topology(self.model(), mode=2),
                         generate_mermaid_topology(self.model(), mode="02"))


if __name__ == "__main__":
    unittest.main()
