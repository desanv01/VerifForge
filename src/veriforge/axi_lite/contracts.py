"""Bounded AXI4-Lite cycle records; no execution capability crosses the wire."""
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from veriforge.contracts import CheckerIdentity, ExecutionBudget, Identifier, Requirement, WireModel

U32 = Annotated[StrictInt, Field(ge=0, lt=2**32)]
Address = Annotated[StrictInt, Field(ge=0, lt=256)]
Protection = Annotated[StrictInt, Field(ge=0, le=7)]
Response = Annotated[StrictInt, Field(ge=0, le=3)]
RequirementId = Literal["AXIL_HANDSHAKE", "AXIL_ORDER", "AXIL_STROBE", "AXIL_READ", "AXIL_RESET", "AXIL_PROGRESS"]
REQUIREMENTS = ("AXIL_HANDSHAKE", "AXIL_ORDER", "AXIL_STROBE", "AXIL_READ", "AXIL_RESET", "AXIL_PROGRESS")
FixtureId = Literal["axil_registers_correct", "axil_aw_w_order_bug", "axil_strobe_bug", "axil_response_stability_bug"]


class Parameters(WireModel):
    data_width: Annotated[StrictInt, Field(ge=32, le=32)] = 32
    address_width: Annotated[StrictInt, Field(ge=8, le=8)] = 8
    register_count: Annotated[StrictInt, Field(ge=4, le=4)] = 4
    max_outstanding_writes: Annotated[StrictInt, Field(ge=1, le=1)] = 1
    max_outstanding_reads: Annotated[StrictInt, Field(ge=1, le=1)] = 1
    request_ready_bound: Annotated[StrictInt, Field(ge=1, le=16)] = 4
    response_bound: Annotated[StrictInt, Field(ge=1, le=16)] = 4
    write_pair_bound: Annotated[StrictInt, Field(ge=1, le=32)] = 8
    max_response_stall: Annotated[StrictInt, Field(ge=0, le=32)] = 8


class AxiLiteCycle(WireModel):
    reset_n: StrictBool = True
    aw_valid: StrictBool = False
    aw_addr: Address = 0
    aw_prot: Protection = 0
    w_valid: StrictBool = False
    w_data: U32 = 0
    w_strb: Annotated[StrictInt, Field(ge=0, le=15)] = 15
    b_ready: StrictBool = True
    ar_valid: StrictBool = False
    ar_addr: Address = 0
    ar_prot: Protection = 0
    r_ready: StrictBool = True

    @model_validator(mode="after")
    def reset_inputs(self):
        if not self.reset_n and (self.aw_valid or self.w_valid or self.ar_valid):
            raise ValueError("manager VALID must be low during reset")
        return self


class AxiLiteObservation(WireModel):
    cycle: Annotated[StrictInt, Field(ge=0, le=4095)]
    aw_ready: StrictBool
    w_ready: StrictBool
    b_valid: StrictBool
    b_resp: Response = 0
    ar_ready: StrictBool
    r_valid: StrictBool
    r_data: U32 = 0
    r_resp: Response = 0


class AxiLiteTrace(WireModel):
    schema_version: Literal["axi_lite.trace.v1"] = "axi_lite.trace.v1"
    observations: Annotated[tuple[AxiLiteObservation, ...], Field(min_length=1, max_length=4096)]


class AxiLiteExperiment(WireModel):
    schema_version: Literal["axi_lite.v1"] = "axi_lite.v1"
    adapter: Literal["rtl.axi_lite.v1"] = "rtl.axi_lite.v1"
    contract_id: Literal["axil-registers-v1"] = "axil-registers-v1"
    target: Literal["axi_lite"] = "axi_lite"
    experiment_id: Identifier
    fixture_id: FixtureId = "axil_registers_correct"
    objective_ids: Annotated[tuple[RequirementId, ...], Field(min_length=1, max_length=6)] = REQUIREMENTS
    seed: Annotated[StrictInt, Field(ge=0, lt=2**32)] = 0
    parameters: Parameters = Field(default_factory=Parameters)
    cycles: Annotated[tuple[AxiLiteCycle, ...], Field(min_length=1, max_length=4096)]
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)

    @property
    def cycle_count(self):
        return len(self.cycles)

    @model_validator(mode="after")
    def legal_static_inputs(self):
        if self.cycles[0].reset_n:
            raise ValueError("first sampled edge must reset")
        if self.cycle_count > self.budget.max_cycles:
            raise ValueError("cycle stimulus exceeds max_cycles")
        for before, after in zip(self.cycles, self.cycles[1:]):
            if not before.reset_n and after.reset_n and (after.aw_valid or after.w_valid or after.ar_valid):
                raise ValueError("manager VALID must wait through the reset release edge")
        if len(set(self.objective_ids)) != len(self.objective_ids):
            raise ValueError("duplicate objective ID")
        return self


class AxiLiteContract(WireModel):
    schema_version: Literal["axi_lite.contract.v1"] = "axi_lite.contract.v1"
    contract_id: Literal["axil-registers-v1"] = "axil-registers-v1"
    target: Literal["axi_lite"] = "axi_lite"
    review_status: Literal["reviewed"] = "reviewed"
    parameters: Parameters
    checker: CheckerIdentity
    requirements: tuple[Requirement, ...]
    sampling: Literal["settled_pre_rising_edge"] = "settled_pre_rising_edge"
    read_write_collision: Literal["read_before_write"] = "read_before_write"
    invalid_access: Literal["slverr_no_write_read_zero"] = "slverr_no_write_read_zero"
    reset: Literal["clear_registers_cancel_all_pending"] = "clear_registers_cancel_all_pending"
    assumptions: tuple[str, ...] = (
        "Manager holds VALID and payload until actual handshake or reset.",
        "One accepted outstanding write and one read; AW and W independent.",
        "Finite write-half gap and response stalls as bounded in parameters.",
        "Reset asserted before sample, deasserted synchronously; no asynchronous timing claim.",
    )


class CheckFailure(WireModel):
    cycle: Annotated[StrictInt, Field(ge=0, le=4095)]
    requirement_id: RequirementId
    failure_class: Identifier
    expected: Annotated[str, Field(max_length=1000)]
    observed: Annotated[str, Field(max_length=1000)]


class CheckOutcome(WireModel):
    schema_version: Literal["axi_lite.check.v1"] = "axi_lite.check.v1"
    verdict: Literal["pass_within_scope", "design_mismatch", "property_failure", "harness_error", "inconclusive"]
    failure: CheckFailure | None = None
    checked_cycles: Annotated[StrictInt, Field(ge=0, le=4096)]
    registers: tuple[U32, U32, U32, U32] = (0, 0, 0, 0)
    uncertainties: tuple[Annotated[str, Field(max_length=1000)], ...] = ()

    @model_validator(mode="after")
    def failure_consistency(self):
        if (self.failure is not None) != (self.verdict in ("design_mismatch", "property_failure")):
            raise ValueError("only independent design/property failures carry a failure")
        return self
