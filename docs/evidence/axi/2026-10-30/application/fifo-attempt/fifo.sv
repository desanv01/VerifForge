module fifo #(parameter WIDTH=8, DEPTH=4, RESET_BUG=0)(
 input logic clk, reset, wr_en, rd_en,
 input logic [WIDTH-1:0] wr_data,
 output logic [WIDTH-1:0] rd_data,
 output logic empty, full
);
 logic [WIDTH-1:0] mem [0:DEPTH-1];
 integer head=0, tail=0, count=0;
 wire take_read = rd_en && !empty;
 wire take_write = wr_en && !full;
 assign empty = count == 0;
 assign full = count == DEPTH;
 always @(posedge clk) begin
   if (reset) begin
     head <= 0; tail <= 0; rd_data <= 0;
     if (RESET_BUG == 0) count <= 0;
   end else begin
     if (take_read) begin
       rd_data <= mem[head];
       head <= (head == DEPTH-1) ? 0 : head+1;
     end
     if (take_write) begin
       mem[tail] <= wr_data;
       tail <= (tail == DEPTH-1) ? 0 : tail+1;
     end
     case ({take_write, take_read})
       2'b10: count <= count+1;
       2'b01: count <= count-1;
       default: count <= count;
     endcase
   end
 end
endmodule
