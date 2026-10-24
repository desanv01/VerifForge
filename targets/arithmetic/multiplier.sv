module multiplier #(parameter WIDTH=8, parameter LATENCY=2, parameter MUTANT=0)(
    input logic clk, reset, in_valid,
    input logic [WIDTH-1:0] a, b,
    output logic out_valid,
    output logic [2*WIDTH-1:0] product
);
    logic [LATENCY:0] valid_pipe;
    logic [2*WIDTH-1:0] data_pipe [0:LATENCY];
    integer i;
    always_ff @(posedge clk) begin
        if (reset) begin
            valid_pipe <= '0;
            for (i=0; i<=LATENCY; i=i+1) data_pipe[i] <= '0;
        end else begin
            valid_pipe[0] <= in_valid;
            if (MUTANT == 2) data_pipe[0] <= (a*b) & ((64'b1 << WIDTH)-1);
            else data_pipe[0] <= a*b;
            for (i=1; i<=LATENCY; i=i+1) begin
                valid_pipe[i] <= valid_pipe[i-1];
                data_pipe[i] <= data_pipe[i-1];
            end
        end
    end
    assign out_valid = (MUTANT == 1) ? valid_pipe[LATENCY-1] : valid_pipe[LATENCY];
    assign product = (MUTANT == 1) ? data_pipe[LATENCY-1] : data_pipe[LATENCY];
endmodule
