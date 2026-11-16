// Combinational 2:1 clock mux. Change s only while affected owners are in reset.
// No latch, synchronizer or glitch-free switching is provided here.
// Replace this wrapper's implementation with the library clock mux, or define
// PIPE_USE_CLKMUX_CELL and PIPE_CLKMUX_CELL (adapt A/B/S/Q to the library ports).
module pipe_lane_clk_mux (
    input  logic clk0,
    input  logic clk1,
    input  logic s,
    output logic clk_out
);

`ifdef PIPE_USE_CLKMUX_CELL
  `PIPE_CLKMUX_CELL u_clkmux (
      .A(clk0),
      .B(clk1),
      .S(s),
      .Q(clk_out)
  );
`else
  assign clk_out = s ? clk1 : clk0;
`endif

endmodule
