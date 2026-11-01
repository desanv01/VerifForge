// Firmware-only native-bus wrapper. No AXI bridge or Spike MMIO reference.
module firmware_top (
    input wire clk, resetn,
    input wire ram_ready,
    input wire [31:0] ram_rdata,
    output wire trap, mem_valid, mem_instr, mem_ready, mmio_window,
    output wire [31:0] mem_addr, mem_wdata, mem_rdata,
    output wire [3:0] mem_wstrb,
    output wire rvfi_valid, rvfi_trap, rvfi_intr,
    output wire [63:0] rvfi_order,
    output wire [31:0] rvfi_insn, rvfi_pc_rdata,
    output wire [4:0] rvfi_rd_addr,
    output wire [31:0] rvfi_rd_wdata, rvfi_rs1_rdata,
    output wire [31:0] rvfi_mem_addr, rvfi_mem_rdata, rvfi_mem_wdata,
    output wire [3:0] rvfi_mem_rmask, rvfi_mem_wmask
);
    wire peripheral_ready, peripheral_selected;
    wire [31:0] peripheral_rdata;
    wire ram_window = mem_addr >= 32'h80000000 && mem_addr < 32'h80010000;
    assign mmio_window = mem_addr >= 32'h40000000 && mem_addr < 32'h40000010;
    // Entire MMIO window is reserved: invalid addresses never fall through to RAM.
    assign mem_ready = resetn && mem_valid &&
        !(mem_instr && |mem_wstrb) &&
        (mmio_window ? peripheral_ready : (ram_window && mem_addr[1:0] == 0 && ram_ready));
    assign mem_rdata = mmio_window ? peripheral_rdata : (ram_window ? ram_rdata : 32'b0);
    firmware_native_registers peripheral (
        .clk(clk), .resetn(resetn), .mem_valid(mem_valid && mmio_window),
        .mem_instr(mem_instr), .mem_addr(mem_addr), .mem_wdata(mem_wdata),
        .mem_wstrb(mem_wstrb), .mem_ready(peripheral_ready),
        .mem_rdata(peripheral_rdata), .selected(peripheral_selected)
    );
    picorv32_top core (
        .clk(clk),
        .resetn(resetn),
        .trap(trap),
        .mem_valid(mem_valid),
        .mem_instr(mem_instr),
        .mem_ready(mem_ready),
        .mem_addr(mem_addr),
        .mem_wdata(mem_wdata),
        .mem_wstrb(mem_wstrb),
        .mem_rdata(mem_rdata),
        .rvfi_valid(rvfi_valid),
        .rvfi_trap(rvfi_trap),
        .rvfi_intr(rvfi_intr),
        .rvfi_order(rvfi_order),
        .rvfi_insn(rvfi_insn),
        .rvfi_pc_rdata(rvfi_pc_rdata),
        .rvfi_rd_addr(rvfi_rd_addr),
        .rvfi_rd_wdata(rvfi_rd_wdata),
        .rvfi_rs1_rdata(rvfi_rs1_rdata),
        .rvfi_mem_addr(rvfi_mem_addr),
        .rvfi_mem_rdata(rvfi_mem_rdata),
        .rvfi_mem_wdata(rvfi_mem_wdata),
        .rvfi_mem_rmask(rvfi_mem_rmask),
        .rvfi_mem_wmask(rvfi_mem_wmask)
    );
endmodule
