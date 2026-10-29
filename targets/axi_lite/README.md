# Trusted bounded AXI-Lite fixture

`registers.sv` is a project-authored curated register peripheral. `MUTANT=0` selects the intended correct implementation; 1 loses separate-channel write pairing, 2 ignores byte strobes, 3 drops stalled BVALID. The trusted runner alone selects these fixed variants by typed IDs. Fixed dimensions: 32-bit data, four registers, eight-bit addresses, one outstanding write and one outstanding read.

The JSON files in `witnesses/` are exact input experiments for the corresponding intended mutant. Their expected consequences are hand-derived in [AXI_LITE_EXECUTION.md](../../docs/project/AXI_LITE_EXECUTION.md), which now also records the completed seven-case real simulator gate (October24/29). The original source-only delivery did not claim live results. The separately owned [contract](../../docs/project/AXI_LITE.md) defines map, reset, pre-edge sampling, strobe, collision, progress and environment semantics.
