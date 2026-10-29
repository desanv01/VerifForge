// Curated fixed map: 4 x 32-bit registers, one independent AW/W slot.
// MUTANT=1 loses separated write pairing; 2 ignores strobes; 3 drops stalled B.
module axil_registers #(
    parameter integer MUTANT = 0
) (
    input logic clk, reset_n,
    input logic aw_valid, input logic [7:0] aw_addr, input logic [2:0] aw_prot,
    output logic aw_ready,
    input logic w_valid, input logic [31:0] w_data, input logic [3:0] w_strb,
    output logic w_ready,
    output logic b_valid, output logic [1:0] b_resp, input logic b_ready,
    input logic ar_valid, input logic [7:0] ar_addr, input logic [2:0] ar_prot,
    output logic ar_ready,
    output logic r_valid, output logic [31:0] r_data,
    output logic [1:0] r_resp, input logic r_ready
);
    logic [31:0] regs [0:3];
    logic aw_saved, w_saved, b_pending, r_pending;
    logic [7:0] saved_addr;
    logic [31:0] saved_data, read_data;
    logic [3:0] saved_strb;
    logic [1:0] write_resp, read_resp;
    wire aw_fire = aw_valid && aw_ready;
    wire w_fire = w_valid && w_ready;
    wire [7:0] commit_addr = aw_saved ? saved_addr : aw_addr;
    wire [31:0] commit_data = w_saved ? saved_data : w_data;
    wire [3:0] commit_strb = w_saved ? saved_strb : w_strb;
    wire pair_ready = (aw_saved || aw_fire) && (w_saved || w_fire);
    wire commit_write = !b_pending && pair_ready &&
                        ((MUTANT != 1) || (aw_fire && w_fire));
    function automatic logic mapped(input logic [7:0] addr);
        mapped = (addr < 16) && (addr[1:0] == 0);
    endfunction
    assign aw_ready = reset_n && !aw_saved && !b_pending;
    assign w_ready = reset_n && !w_saved && !b_pending;
    assign ar_ready = reset_n && !r_pending;
    // Gate reset before the sampled reset edge; storage is cleared at reset.
    assign b_valid = reset_n && b_pending;
    assign b_resp = write_resp;
    assign r_valid = reset_n && r_pending;
    assign r_resp = read_resp;
    assign r_data = read_data;

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            for (integer i = 0; i < 4; i = i + 1) regs[i] <= 0;
            aw_saved <= 0; w_saved <= 0; b_pending <= 0; r_pending <= 0;
            saved_addr <= 0; saved_data <= 0; saved_strb <= 0;
            write_resp <= 0; read_resp <= 0; read_data <= 0;
        end else begin
            if (b_pending && (b_ready || MUTANT == 3)) begin
                b_pending <= 0; aw_saved <= 0; w_saved <= 0;
            end
            if (r_pending && r_ready) r_pending <= 0;
            if (aw_fire) begin aw_saved <= 1; saved_addr <= aw_addr; end
            if (w_fire) begin w_saved <= 1; saved_data <= w_data; saved_strb <= w_strb; end
            if (commit_write) begin
                b_pending <= 1;
                write_resp <= mapped(commit_addr) ? 2'b00 : 2'b10;
                if (mapped(commit_addr)) begin
                    for (integer lane = 0; lane < 4; lane = lane + 1)
                        if (MUTANT == 2 || commit_strb[lane])
                            regs[commit_addr[3:2]][8*lane +: 8] <= commit_data[8*lane +: 8];
                end
            end
            // Nonblocking assignments preserve read-before-write collisions.
            if (ar_valid && ar_ready) begin
                r_pending <= 1;
                read_resp <= mapped(ar_addr) ? 2'b00 : 2'b10;
                read_data <= mapped(ar_addr) ? regs[ar_addr[3:2]] : 32'b0;
            end
        end
    end
endmodule
