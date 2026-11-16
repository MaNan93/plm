"""Exercise combinational clock routing without waiting for clock edges."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from plm.core.generator import rtl
from plm.core.paths import COMMON_DIR
import test_dpr_phy_input


class CombClockMuxTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("PLM_RUN_RTL_SIM") == "1",
                         "set PLM_RUN_RTL_SIM=1 to run Verilator")
    def test_all_clock_values_and_owner_selections(self):
        simulator = os.environ.get("PLM_VERILATOR") or shutil.which("verilator")
        self.assertTrue(simulator)
        for phy_input in (False, True):
            with self.subTest(phy_input=phy_input), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                cfg = test_dpr_phy_input.DprClockTests._simulation_config(root)
                mode = "pclk_as_phy_input" if phy_input else "pclk_as_phy_output"
                (root / "pkg.sv").write_text(rtl.gen_pkg(cfg), encoding="utf-8")
                (root / "clk.sv").write_text(rtl.gen_clk_mux(cfg, mode), encoding="utf-8")
                shutil.copy2(COMMON_DIR / "pipe_lane_clk_mux.sv", root / "mux2.sv")
                ports = (
                    ".rx_clk(rx), .ctrla_pclk(pa), .ctrlb_pclk(pb), "
                    ".seltgt_g1_l1(sel), .rxclk_en_g1_l1(sel), "
                    ".phy_pclk(pout), .ctrla_rx_clk(ra), .ctrlb_rx_clk(rb)"
                    if phy_input else
                    ".phy_pclk_out(rx), .sel_tgt(owners), .ctrl_pclk(ra), .phy_pclk_in(pout)"
                )
                wrapper = '''import pipe_lane_signal_pkg::*;
module clock_probe(input logic [1:0] rx, pa, sel, input logic pb,
                   output logic [1:0] pout, ra, output logic rb);
  lane_sel_t owners;
  assign owners.g1 = sel;
  REPLACE_UNUSED
  pipe_lane_clk_router dut (.ctrl_rst_n(2'b00), REPLACE_PORTS);
endmodule
'''.replace("REPLACE_PORTS", ports).replace(
                    "REPLACE_UNUSED", "" if phy_input else "assign rb = 1'b0;")
                (root / "probe.sv").write_text(wrapper, encoding="utf-8")
                driver = '''#include "Vclock_probe.h"
#include "verilated.h"
#include <iostream>
double sc_time_stamp() { return 0; }
int main() {
  VerilatedContext context; context.threads(1);
  Vclock_probe dut{&context};
  for (unsigned bits = 0; bits < 32; ++bits) {
    dut.rx = bits & 3; dut.pa = (bits >> 2) & 3; dut.pb = bits >> 4;
    // Selects change while source levels stay fixed, including high levels.
    for (unsigned sel : {1U, 2U, 0U, 2U, 1U}) {
      dut.sel = sel; dut.eval();
      unsigned out, ra, rb = 0;
      if (PHY_INPUT) {
        out = (dut.pa & 1) | ((sel == 1 ? (dut.pa >> 1) : sel == 2 ? dut.pb : 0) << 1);
        ra = (dut.rx & 1) | (sel == 1 ? (dut.rx & 2) : 0);
        rb = sel == 2 ? (dut.rx >> 1) : 0;
      } else {
        out = (dut.rx & 1) | ((sel == 1 ? (dut.rx & 1) : sel == 2 ? (dut.rx >> 1) : 0) << 1);
        ra = dut.rx;
      }
      if (dut.pout != out || dut.ra != ra || dut.rb != rb) {
        std::cerr << "clock routing mismatch " << bits << " " << sel << std::endl;
        return 1;
      }
    }
  }
  std::cout << "COMB_CLOCK_RESULT: PASS" << std::endl;
}
'''.replace("PHY_INPUT", "true" if phy_input else "false")
                (root / "driver.cpp").write_text(driver, encoding="utf-8")
                (root / "obj_dir").mkdir()
                args = [simulator, "--cc", "--exe", "--build", "-j", "2", "-Wno-fatal",
                        "--top-module", "clock_probe", "pkg.sv", "mux2.sv", "clk.sv", "probe.sv", "driver.cpp"]
                if os.name == "nt":
                    args += ["-MAKEFLAGS", f"PYTHON3={Path(sys.executable).as_posix()}", "-LDFLAGS", "-static"]
                build = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=180)
                self.assertEqual(build.returncode, 0, build.stdout[-4000:] + build.stderr[-4000:])
                binary = root / "obj_dir" / ("Vclock_probe.exe" if os.name == "nt" else "Vclock_probe")
                run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn("COMB_CLOCK_RESULT: PASS", run.stdout)
