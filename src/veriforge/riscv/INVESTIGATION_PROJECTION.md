# Read-only investigation projections

These builders consume **already admitted typed cases** and a host-supplied `resolve(EvidenceReference) -> bytes | None`. They neither import/admit an investigation nor read files, execute tools, or write evidence. Main owns version authorization and storage access. Supplied bytes are rehashed; missing bytes produce gaps. No host paths, command arguments or raw source text are returned.

Callables:

```python
veriforge.riscv.investigation.build_riscv_investigation(
    case, resolve, *, window=5, original=None, saved_program_ref=None)
veriforge.firmware.investigation.build_firmware_investigation(
    case, resolve, *, report=None, window=5)
```

RISC-V v1/v2 cases are supported. V2 `case.inputs.saved_program` is used automatically; `original` must match `case.replay_of`. Original control `inputs.original_run` binds saved-origin bytes when present. A separately supplied saved ref must equal the typed saved ref. Firmware `report` is the already-admitted `FirmwareRecordedReport`, enabling its exact candidate/replay associations. No names or displayed titles select a candidate.

Each result is a JSON object. All numbers below are JSON integers, except nullable fields; booleans remain booleans. `Ref = {artifact_id: str, revision_id: str, attempt_id: str|null, sha256: 64hex}`. UI links only a matching admitted artifact ref/hash, never a path. Common fields:

```text
schema_version: "riscv.investigation.v1" | "firmware.investigation.v1"
domain: "riscv" | "firmware"
case_label: admitted label (including control-saved-replay)
locator: exact admitted locator, including attempt/case and hosted origin
identity: {source_hash, checker_hash, contract_hash, experiment_hash, environment_hash}
checker: exact admitted CheckerIdentity
execution: {new_execution:"not_run", producer_status:str, termination:str,
            design_verdict:str, checker_status:str}
projection_status: "complete" | "partial"  # byte/projection availability, never acceptance
evidence: [{role:str, reference:Ref, size_bytes:int|null, status:"available"|"missing"}]
gaps: [{code:str, role:str, reference:Ref|null}]
```

RISC-V:

```text
first_divergence: null | {order:int, cycle:int, field:str, expected:typed JSON,
                         observed:typed JSON, requirement:"RISCV_ARCH_RETIREMENT"}
retirements: {start_order:int, end_order:int, total_reference:int, total_observed:int,
 rows:[{order:int, reference:Retirement|null, observed:Retirement|null}]}
Retirement = {schema_version,order,hart,pc,instruction,rd,rd_value,trap,interrupt,
              memory:null|{kind,address,width,byte_mask,value},source_line:int|null,source_cycle:int|null}
registers: {at_order:int, timing:"after_selected_retirement",
 reference:[{register:int,value:int|null,last_order:int|null}], observed:[same]}
program: {object:{reference:Ref,available:bool}, binary:{same},
 elf:{reference:Ref,available:bool,facts?:ElfFacts,retirement_links?:[{order,segments:[int]}]}}
lineage: {mode:"saved_program"|"recompiled"|"fresh_compilation"|"unknown",status:str,
          original:Locator|null,program_equal:bool|null,proof:Ref|null}
assumptions: {platform:fixed RV32I platform|null,profile_reference:Ref,hart:0,privilege:"machine",compressed:false,
 mmio:false,initial_registers:"unknown_except_x0",cycle_relation:str,scope:str}
```

Retirement rows are at most33 (window0..16), centered at the first architectural divergence, or order0. Register arrays have32 entries; unwritten registers remain unknown, never assumed reset-zero. `end_order` is exclusive. DUT cycles are diagnostic, not compared against reference cycles. Equal executable bytes without original/proof do not claim saved replay. `saved_program_bound` requires program/proof/environment/original-result relationships; missing original bytes remain explicit.

Firmware:

```text
first_conflict: null | admitted MmioConflict (mmio_index,bus_sequence,cycle,requirement,
 operation,expected_address,observed_address,write_strobes,active_lane_value,
 expected_probe,observed_probe)
elf: {reference:Ref,facts:ElfFacts|null,layout:LayoutCheck|null}
disassembly: {reference:Ref,corroborated:bool,total:int,rows:[InstructionFact],truncated:bool}
registers: [{name,expected_address,width_bits,access,reset_value,byte_strobes,side_effects,
 header:{reference:Ref,value:int|null,location:str|null},rtl:{same},
 mmio_indices:[int],address_status:"missing"|"agrees"|"mismatch"}]
mmio: [{index,bus_sequence,cycle,operation,address,data,write_strobes,expected_address:int|null,
 expected_register:str|null,observed_register:str|null,retirement_order:int|null,pc:int|null,
 instruction:InstructionFact|null,instruction_link:"corroborated"|"unavailable",reference:Ref}]
retirements: {start_order,end_order,total_observed,rows:[{order,pc,instruction,cycle,rd,rd_value}]}
candidate: null | {origin:"curated_header_correction",outcome,validation_status,
 original:Locator,repeated:Locator,source_hash,checker_hash,contract_hash,profile_hash,
 bad_verdict,candidate_verdict,covered_mmio_indices:[int],
 diff:{reference:Ref,text:str|null,truncated:bool},base_header:{reference,value,location},
 candidate_header:{same},source_bytes_available:bool,limitations:[str]}
lineage: {original_execution:Ref|null,status:str}
assumptions: {profile:PlatformProfile|null,profile_hash,register_contract_hash:str|null,
 reset:str,reference_support,scope}
```

Instruction facts retain address/encoding/width_bits/mnemonic/operands/provenance. Disassembly rows are capped at64; ELF-byte corroboration applies to the complete bounded parsed listing. MMIO records derive only from accepted data transfers, and PC links require checked ordered retirement association. Header/RTL/map assumptions are distinct from observed bus behavior. Firmware Spike-MMIO remains unsupported. Candidate diff text is at most16KiB, otherwise only its ref and truncation flag remain. Candidate validation is recorded scoped evidence, not a new run or model repair.

Budgets:64 resolved artifacts,16MiB per artifact,32MiB aggregate,512KiB serialized result; typed native parsers keep their existing narrower limits. Unknown/missing artifacts do not become empty success evidence. Bad byte identities, duplicate/nonfinite JSON, or disagreement with supplied admitted/native checker facts fail rather than project misleading data. Existing immutable checker/contract/target files are not modified.
