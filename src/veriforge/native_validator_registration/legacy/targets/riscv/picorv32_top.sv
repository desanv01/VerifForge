// Experimental source-only wrapper. Compile upstream + this file with RISCV_FORMAL.
module picorv32_top (
    input wire clk, resetn,
    output wire trap,
    output wire mem_valid, mem_instr,
    input wire mem_ready,
    output wire [31:0] mem_addr, mem_wdata,
    output wire [3:0] mem_wstrb,
    input wire [31:0] mem_rdata,
    output wire rvfi_valid, rvfi_trap, rvfi_intr,
    output wire [63:0] rvfi_order,
    output wire [31:0] rvfi_insn, rvfi_pc_rdata,
    output wire [4:0] rvfi_rd_addr,
    output wire [31:0] rvfi_rd_wdata, rvfi_rs1_rdata,
    output wire [31:0] rvfi_mem_addr, rvfi_mem_rdata, rvfi_mem_wdata,
    output wire [3:0] rvfi_mem_rmask, rvfi_mem_wmask
);
    picorv32 #(
        .ENABLE_COUNTERS(0), .ENABLE_COUNTERS64(0),
        .ENABLE_REGS_16_31(1), .REGS_INIT_ZERO(1), .BARREL_SHIFTER(1),
        .COMPRESSED_ISA(0), .ENABLE_MUL(0), .ENABLE_FAST_MUL(0), .ENABLE_DIV(0),
        .ENABLE_IRQ(0), .ENABLE_PCPI(0), .CATCH_MISALIGN(1), .CATCH_ILLINSN(1),
        .PROGADDR_RESET(32'h80000000), .STACKADDR(32'hffffffff)
    ) cpu (
        .clk(clk), .resetn(resetn), .trap(trap),
        .mem_valid(mem_valid), .mem_instr(mem_instr), .mem_ready(mem_ready),
        .mem_addr(mem_addr), .mem_wdata(mem_wdata), .mem_wstrb(mem_wstrb), .mem_rdata(mem_rdata),
        .irq(32'b0), .pcpi_wr(1'b0), .pcpi_rd(32'b0), .pcpi_wait(1'b0), .pcpi_ready(1'b0),
        .rvfi_valid(rvfi_valid), .rvfi_order(rvfi_order), .rvfi_insn(rvfi_insn),
        .rvfi_trap(rvfi_trap), .rvfi_intr(rvfi_intr), .rvfi_pc_rdata(rvfi_pc_rdata),
        .rvfi_rd_addr(rvfi_rd_addr), .rvfi_rd_wdata(rvfi_rd_wdata), .rvfi_rs1_rdata(rvfi_rs1_rdata),
        .rvfi_mem_addr(rvfi_mem_addr), .rvfi_mem_rmask(rvfi_mem_rmask), .rvfi_mem_wmask(rvfi_mem_wmask),
        .rvfi_mem_rdata(rvfi_mem_rdata), .rvfi_mem_wdata(rvfi_mem_wdata)
    );
endmodule
