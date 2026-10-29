"""Local experimental RISC-V wire types; no shared API schema is changed."""
from typing import Annotated, Literal
from pydantic import Field, StrictBool, StrictInt, model_validator
from veriforge.contracts import WireModel

U32 = Annotated[StrictInt, Field(ge=0, lt=2**32)]
Register = Annotated[StrictInt, Field(ge=0, le=31)]
Order = Annotated[StrictInt, Field(ge=0, lt=2**64)]


class RiscvExperiment(WireModel):
    schema_version: Literal["riscv.v1"] = "riscv.v1"
    experiment_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    fixture_id: Literal["picorv32_rv32i", "picorv32_reg_write_bug"] = "picorv32_rv32i"
    program_id: Literal["rv32i-smoke-v1"] = "rv32i-smoke-v1"
    profile_id: Literal["picorv32-rv32i-v1"] = "picorv32-rv32i-v1"
    max_retired: Annotated[StrictInt, Field(ge=32, le=10000)] = 256
    max_cycles: Annotated[StrictInt, Field(ge=100, le=1000000)] = 100000
    wall_time_seconds: Annotated[float, Field(gt=0, le=300, strict=True)] = 120.0


class MemoryEffect(WireModel):
    kind: Literal["read", "write"]
    address: U32
    width: Annotated[StrictInt, Field(ge=1, le=4)]
    byte_mask: Annotated[StrictInt, Field(ge=0, le=15)]
    value: U32

    @model_validator(mode="after")
    def width_value(self):
        if self.width not in (1, 2, 4) or self.value >= 2**(8*self.width):
            raise ValueError("memory value exceeds access width")
        return self


class Retirement(WireModel):
    schema_version: Literal["riscv.retirement.v1"] = "riscv.retirement.v1"
    order: Order
    hart: Annotated[StrictInt, Field(ge=0, le=0)] = 0
    pc: U32
    instruction: U32
    rd: Register = 0
    rd_value: U32 = 0
    trap: StrictBool = False
    interrupt: StrictBool = False
    memory: MemoryEffect | None = None
    source_line: Annotated[StrictInt, Field(ge=1)] | None = None
    source_cycle: Annotated[StrictInt, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def fixed_invariants(self):
        if self.rd == 0 and self.rd_value != 0:
            raise ValueError("x0 has no architectural write")
        return self


class RVFIEvent(WireModel):
    cycle: Annotated[StrictInt, Field(ge=0)] | None = None
    rvfi_order: Order
    rvfi_insn: U32
    rvfi_pc_rdata: U32
    rvfi_rd_addr: Register
    rvfi_rd_wdata: U32
    rvfi_rs1_rdata: U32
    rvfi_mem_addr: U32
    rvfi_mem_rmask: Annotated[StrictInt, Field(ge=0, le=15)]
    rvfi_mem_wmask: Annotated[StrictInt, Field(ge=0, le=15)]
    rvfi_mem_rdata: U32
    rvfi_mem_wdata: U32
    rvfi_trap: StrictBool = False
    rvfi_intr: StrictBool = False


class SimulationOutput(WireModel):
    """Strict simulator result envelope; job-specific bounds are checked by runner."""
    termination: Literal["completion_store_retired", "trap", "cycle_limit", "retirement_limit"]
    cycles: Annotated[StrictInt, Field(ge=1, le=1000000)]
    events: Annotated[list[RVFIEvent], Field(strict=True, max_length=10000)]


class FirstDivergence(WireModel):
    order: Order
    field: Literal["pc", "instruction", "rd", "rd_value", "trap", "interrupt", "memory"]
    expected: str
    observed: str
    requirement_id: Literal["RISCV_ARCH_RETIREMENT"] = "RISCV_ARCH_RETIREMENT"


class Comparison(WireModel):
    verdict: Literal["pass_within_scope", "design_mismatch", "inconclusive"]
    compared: Annotated[StrictInt, Field(ge=0)]
    first_divergence: FirstDivergence | None = None
    reason: str

    @model_validator(mode="after")
    def consistent_verdict(self):
        if (self.verdict == "design_mismatch") != (self.first_divergence is not None):
            raise ValueError("only architectural mismatch carries a first divergence")
        if self.verdict == "pass_within_scope" and self.compared == 0:
            raise ValueError("empty traces cannot pass")
        return self
