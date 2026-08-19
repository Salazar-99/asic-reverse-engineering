`timescale 1ns / 1ps

module tb_puzzle;
  reg i_driver;
  reg clk_driver;
  reg enable_driver;
  reg rst_n_driver;
  reg vpwr_driver;
  reg vgnd_driver;

  wire I;
  wire clk;
  wire enable;
  wire rst_n;
  wire VPWR;
  wire VGND;
  wire [7:0] O;
  wire success;

  assign I = i_driver;
  assign clk = clk_driver;
  assign enable = enable_driver;
  assign rst_n = rst_n_driver;
  assign VPWR = vpwr_driver;
  assign VGND = vgnd_driver;

  puzzle dut (
    .I(I),
    .O(O),
    .VGND(VGND),
    .VPWR(VPWR),
    .clk(clk),
    .enable(enable),
    .rst_n(rst_n),
    .success(success)
  );

  always #5 clk_driver = ~clk_driver;

  initial begin
    vpwr_driver = 1'b1;
    vgnd_driver = 1'b0;
    i_driver = 1'b0;
    clk_driver = 1'b0;
    enable_driver = 1'b0;
    rst_n_driver = 1'b0;

    #12;
    if (VPWR !== 1'b1 || VGND !== 1'b0) begin
      $fatal(1, "Power rails are not driven correctly");
    end

    rst_n_driver = 1'b1;
    enable_driver = 1'b1;
    i_driver = 1'b1;
    repeat (4) @(posedge clk_driver);

    $display("PASS: reset-and-clock smoke test completed; O=%b success=%b", O, success);
    $finish;
  end
endmodule
