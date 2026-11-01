// Proposed actual native-bus peripheral RTL; not compiled or connected yet.
module firmware_native_registers (
    input logic clk, resetn, mem_valid, mem_instr,
    input logic [31:0] mem_addr, mem_wdata,
    input logic [3:0] mem_wstrb,
    output logic mem_ready,
    output logic [31:0] mem_rdata,
    output logic selected
);
    logic [31:0] registers [0:3];
    integer register_index;
    always_comb begin
        selected = 0;
        register_index = 0;
        mem_rdata = 0;
        case (mem_addr)
            32'h40000000: begin register_index = 0; mem_rdata = registers[0]; selected = 1; end
            32'h40000004: begin register_index = 1; mem_rdata = registers[1]; selected = 1; end
            32'h40000008: begin register_index = 2; mem_rdata = registers[2]; selected = 1; end
            32'h4000000c: begin register_index = 3; mem_rdata = registers[3]; selected = 1; end
            default: begin end
        endcase
    end
    assign mem_ready = resetn && mem_valid && !mem_instr && selected;
    always_ff @(posedge clk) begin
        if (!resetn) begin
            for (integer i = 0; i < 4; i = i + 1) registers[i] <= 0;
        end else if (mem_ready) begin
            for (integer lane = 0; lane < 4; lane = lane + 1)
                if (mem_wstrb[lane])
                    registers[register_index][8*lane +: 8] <= mem_wdata[8*lane +: 8];
        end
    end
endmodule
