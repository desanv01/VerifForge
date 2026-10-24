# Bounded AXI4-Lite register peripheral — VF-055A

Status: contract/oracle groundwork only. No HDL, driver, simulator, registry, API or planner integration is implemented by this package.

## Versioned boundary (published before implementation)

Import from `veriforge.axi_lite`: `AxiLiteExperiment`, `AxiLiteCycle`, `AxiLiteObservation`, `AxiLiteTrace`, `AxiLiteContract`, `Parameters`, `CheckOutcome`, `check`, `reviewed_contract`.

Experiment schema `axi_lite.v1`, adapter `rtl.axi_lite.v1`, contract ID `axil-registers-v1`. Curated IDs reserved for later trusted fixtures: `axil_registers_correct`, `axil_aw_w_order_bug`, `axil_strobe_bug`, `axil_response_stability_bug`. None is installed/executable yet. Shared FIFO `Experiment` stays unchanged.

Exact cycle stimulus has active-low `reset_n`, AWVALID/AWADDR/AWPROT, WVALID/WDATA/WSTRB, BREADY, ARVALID/ARADDR/ARPROT and RREADY (snake_case fields). Observation has cycle index, AWREADY/WREADY/BVALID/BRESP/ARREADY/RVALID/RDATA/RRESP. `AxiLiteTrace` wraps observations under schema `axi_lite.trace.v1`. Both cycle/observation lists are bounded to 4096 records. Values are sampled **immediately before each rising edge**, after inputs and combinational outputs settle. Handshakes use that row's pre-edge values; state changes become visible in subsequent rows. Store the actually driven input sequence, including any driver-generated hold cycles, unchanged for replay.

Static schema validation bounds signal widths, fixed parameters, fixture IDs, cycles/budgets, first-edge reset and reset-time manager VALID. Dynamic manager legality (VALID/payload held until handshake, at most one outstanding write and one read) needs observed READY and is independently checked by the oracle. A typed cycle list alone cannot prove legality against an unknown DUT. Illegal stimulus is a harness error, not a design finding. A later adaptive driver must hold requests against actual READY and record those applied cycles; this package is not that driver.

## Reviewed product semantics

- 32-bit data, 8-bit byte address, four read/write 32-bit registers at 0,4,8,12; all reset to zero. AWPROT/ARPROT are accepted and ignored. No bursts, IDs, exclusives or side effects.
- AW and W may arrive in either order or together. One slot each is paired by acceptance order; no second write transaction may be accepted until B handshake/reset. One read may proceed independently until its R handshake/reset. A new request can be offered while the previous response stalls, but the peripheral must backpressure it; same-edge response consumption and new request acceptance are allowed.
- Write commits when the second channel is accepted. WSTRB bit i selects byte i; zero strobe is a successful no-op. Only aligned mapped addresses succeed (OKAY=0). Unaligned/unmapped access responds SLVERR=2; invalid writes leave registers unchanged, invalid reads return zero. This address/error policy is this peripheral's choice, not a universal AXI requirement.
- AR snapshots the register at its acceptance edge. An AR and write commit on the same edge read the **old** value. Read data/response and write response remain stable while VALID and !READY. Responses cannot precede the required request handshakes and cannot be duplicated.
- At a sampled reset edge, manager VALID must be low; response VALID must be low and registers/pending halves/responses clear. Reset may interrupt pending transactions or stalled responses. Every transition from reset-low to reset-high includes one sampled release edge with manager VALID low; requests may begin on the following row. The driver must assert active-low reset before sampling a reset row and deassert synchronously; no asynchronous reset timing behavior is claimed by this cycle model.
- Product-specific `request_ready_bound=4` edges: with its slot free and manager VALID held, each offered AW/W/AR must handshake within that many sampled edges, independently of the other request channel. Product-specific `response_bound=4` edges: BVALID appears within four edges after the second write handshake, RVALID within four after AR. Earliest response row is the next row. These bounds concern response assertion and do not depend on BREADY/RREADY. READY timing within the bound is otherwise unconstrained.
- Finite environment assumptions: manager eventually supplies both write halves (`write_pair_bound=8` sampled edges after first accepted half); response backpressure lasts at most `max_response_stall=8` consecutive offered-response rows. Exceeding those environmental assumptions is inconclusive, not a generic AXI protocol defect. A finite trace ending with requests/responses outstanding is inconclusive; reset can explicitly cancel them. Wall-clock timeout remains execution status/verdict responsibility of the runner, not this oracle.

`check(experiment, observations) -> CheckOutcome` independently scores actual handshakes and a mathematical byte-array register map, tolerating any response/READY schedule within the product bounds. It does not predict exact DUT READY waveforms or copy pipeline state. Generic handshake violations and functional mismatches are separate from product-specific progress failures. CheckOutcome is **not** AttemptResult and cannot manufacture execution/tool evidence. Later runner maps its verdict/failure into shared AttemptResult with true source/checker/contract/experiment/environment identity, observed tool versions, exact cycle stimulus and `axi_lite.trace.v1` artifact; common FIFO trace stays empty.

## Hand-derived example and later runner obligations

All unlisted request VALID signals below are low; response READY is high. Values are pre-edge samples. READY outputs not relevant to a row are unspecified by this example.

| Cycle | Driven input | Observed handshake/response | Independent consequence |
| --- | --- | --- | --- |
| 0 | reset_n=0 | BVALID=RVALID=0 | all registers zero, no pending work |
| 1 | reset_n=1, all request VALID=0 | no response | quiescent release edge |
| 2 | AWVALID=1, AWADDR=0 | AWREADY=1 | save address independently |
| 3 | WVALID=1, WDATA=0x11223344, WSTRB=15 | WREADY=1 | register 0 becomes 0x11223344 |
| 4 | no request | BVALID=1, BRESP=0 | consume successful write response |
| 5 | ARVALID=1, ARADDR=0 | ARREADY=1 | snapshot register 0 |
| 6 | no request | RVALID=1, RRESP=0, RDATA=0x11223344 | consume read, no pending tail |

Swapping the AW and W rows preserves the result. Starting from 0x11223344, writing 0xAABBCCDD with WSTRB=5 yields 0x11BB33DD (byte 0 and byte 2 replaced); all 16 masks have hand-written expected examples in the pure tests. A simultaneous AR/write-commit sees the old value; later reads see the masked new value. No observed readback means stored-register behavior was not externally checked; a scoped pass does not imply achieved strobe/read coverage.

Later driver/runner owner must:

1. Select only installed trusted fixture files; reject these reserved IDs as unsupported until implemented. No planner command/path/Python import is an execution option.
2. Drive inputs away from the rising edge, sample settled response and READY **before** raising the clock, then allow sequential updates. Keep observation-only driver code separate from the oracle; never call this checker inside the simulator to manufacture observations.
3. Enforce request hold behavior and bounded work under actual READY. A fixed cycle experiment that violates the manager rules is rejected as a harness error. If expanding an adaptive intent, preserve both intent and the final exact applied cycle experiment as distinct records with distinct hashes; do not silently change a submitted exact experiment or claim they were identical replay inputs.
4. Record observation envelope and exact applied inputs; invoke `check` in the trusted independent parent. Malformed records/illegal manager behavior map to harness_error, not a design failure. Missing tools/wall timeout/cancellation/resource exhaustion remain separate runner outcomes.
5. Build shared evidence identity using `canonical_hash(reviewed_contract(parameters))`, actual source fixture identity, oracle SHA-256, actual applied experiment SHA-256 and observed environment/tool identity. Transform a checker failure into shared FailureSignature only with real checked execution evidence; do not invent tool versions or execution success from pure examples.
6. Test the correct fixture plus actual distinct negative variants, READY variation, both AW/W orders, every strobe, stalls, reset interruption, malformed harness records and replay identity. Registry/API/planner/evidence dispatch require an explicit versioned extension owned by those components.

Edge-sampled records do not prove absence of combinational input/output paths, analog timing or asynchronous reset assertion behavior. This is a bounded peripheral contract checker, not a complete AXI conformance checker. There is no runner/replay/export/reducer implementation or measured coverage in this package. Pure verification currently passes 79 cases; real HDL/driver and independent review gates remain open.

## Protocol references

Official [Arm IHI0022 latest landing page](https://developer.arm.com/documentation/ihi0022/latest/) was checked on 24 October 2026; it redirects to an inaccessible support-rendered page in this environment. The official E PDF initially loaded but later section fetches timed out; normative rules were then checked in the accessible official [IHI0022H PDF](https://developer.arm.com/-/media/Arm%20Developer%20Community/PDF/IHI0022H_amba_axi_protocol_spec.pdf), A3.1–A3.3 and B1.1 (pages 40–46 and 120–121). This is an explicitly pinned AXI4-Lite baseline, not a claim that H is the latest AXI specification. Generic rules used: independent five channels, VALID held until READY, stable stalled payload, response dependencies, and reset VALID behavior. Full byte-strobe support, the one-outstanding capacity and bounded independent request acceptance are this product's stricter choices; AXI4-Lite permits other subordinate strobe/capacity/READY policies. Product bounds/map/collision/reset-register policy above are independently chosen and versioned here. No protocol text or diagrams are copied into the implementation.
