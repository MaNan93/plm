import copy
from types import SimpleNamespace
import unittest

from plm.core.gen_flags import GenerationOptions
from plm.core.model import Config
from plm.core.topology import generate_mermaid_topology
from sample_data import SAMPLES_DIR


class MermaidTopologyTests(unittest.TestCase):
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
        self.assertIn('ctrl_7 -->|"lane 0; mode=0,2; data"| phy_0', graph)
        self.assertNotIn("mux_0", graph)
        self.assertIn('ctrl_7 -->|"lane 1; mode=0"| mux_1', graph)
        self.assertIn('ctrl_12 -->|"lane 0; mode=2"| mux_1', graph)
        self.assertIn('clk_gate_1_0 -.->|"gated clock"| clk_or_1', graph)
        self.assertIn('clk_or_1 -.->|"phy_pclk_in#91;1#93;"| phy_1', graph)
        self.assertNotIn("mux_1 --> clk_gate", graph)

    def test_phy_input_clock_and_dpr_on_plan_shaped_object(self):
        model = self.model()
        plan = SimpleNamespace(
            controllers=model["controllers"], mapping=model["lane_mapping"],
            options=GenerationOptions(arch="serdes", pclk_mode="pclk_as_phy_input", dpr=True),
        )
        graph = generate_mermaid_topology(plan)
        self.assertIn('dpr["DPR"]', graph)
        self.assertIn('dpr -->|"lane 0; v_lane_mask#91;0#93;=1"| mux_0', graph)
        self.assertIn('ctrl_7 -.->|"pclk#91;1#93;; mode=0; v_lane_mask#91;1#93;=0"| clk_gate_1_0', graph)
        self.assertIn('clk_or_1 -.->|"phy_pclk#91;1#93;"| phy_1', graph)

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
        self.assertIn('ctrl_7 -->|"lane 0; mode=2; data"| phy_0', graph)


if __name__ == "__main__":
    unittest.main()
