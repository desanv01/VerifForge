// Synthetic decode text fixture: no build or actual peripheral behavior claimed.
module parser_fixture(input logic [7:0] addr, input logic [31:0] write_data, input logic clk);
logic [31:0] control, status, data, scratch;
always_ff @(posedge clk) begin
case (addr)
8'h00: control <= write_data;
8'h04: status <= write_data;
8'h08: data <= write_data;
8'h0c: scratch <= write_data;
default: ;
endcase
end
endmodule
