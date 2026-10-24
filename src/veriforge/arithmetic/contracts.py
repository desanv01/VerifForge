"""Arithmetic-specific wire format; shared FIFO Experiment v1 stays unchanged."""
from typing import Annotated, Literal
from pydantic import Field, StrictBool, StrictInt, model_validator
from veriforge.contracts import WireModel, ExecutionBudget, CheckerIdentity, Requirement


class Parameters(WireModel):
    width: Annotated[StrictInt, Field(ge=1, le=32)] = 8
    latency: Literal[2] = 2


class Reset(WireModel):
    op: Literal["reset"] = "reset"
    cycles: Annotated[StrictInt, Field(ge=1, le=10000)] = 1


class Idle(WireModel):
    op: Literal["idle"] = "idle"
    cycles: Annotated[StrictInt, Field(ge=1, le=10000)] = 1


class Multiply(WireModel):
    op: Literal["multiply"] = "multiply"
    a: Annotated[StrictInt, Field(ge=0, lt=2**32)]
    b: Annotated[StrictInt, Field(ge=0, lt=2**32)]


class Cycle(WireModel):
    op: Literal["cycle"] = "cycle"
    reset: StrictBool = False
    valid: StrictBool = False
    a: Annotated[StrictInt, Field(ge=0, lt=2**32)] = 0
    b: Annotated[StrictInt, Field(ge=0, lt=2**32)] = 0


Operation = Annotated[Reset | Idle | Multiply | Cycle, Field(discriminator="op")]
REQUIREMENTS = ("ARITH_PRODUCT", "ARITH_LATENCY", "ARITH_RESET")


class ArithmeticExperiment(WireModel):
    schema_version: Literal["arithmetic.v1"] = "arithmetic.v1"
    experiment_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    fixture_id: Literal["multiplier_correct", "multiplier_latency_bug", "multiplier_data_bug"] = "multiplier_correct"
    contract_id: Literal["unsigned-multiplier-v1"] = "unsigned-multiplier-v1"
    target: Literal["arithmetic"] = "arithmetic"
    adapter: Literal["rtl.arithmetic.v1"] = "rtl.arithmetic.v1"
    objective_ids: tuple[Literal["ARITH_PRODUCT", "ARITH_LATENCY", "ARITH_RESET"], ...] = REQUIREMENTS
    seed: Annotated[StrictInt, Field(ge=0, lt=2**32)] = 0
    parameters: Parameters = Field(default_factory=Parameters)
    operations: Annotated[tuple[Operation, ...], Field(min_length=1, max_length=10000)]
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)

    @property
    def cycle_count(self):
        return sum(getattr(op, "cycles", 1) for op in self.operations)

    @model_validator(mode="after")
    def legal(self):
        if not self.objective_ids or len(set(self.objective_ids)) != len(self.objective_ids):
            raise ValueError("objective IDs must be nonempty and unique")
        if not isinstance(self.operations[0], Reset) and not (isinstance(self.operations[0], Cycle) and self.operations[0].reset):
            raise ValueError("first cycle must reset")
        if self.cycle_count > self.budget.max_cycles:
            raise ValueError("stimulus exceeds max_cycles")
        for op in self.operations:
            if any(getattr(op, operand, 0) >= 2**self.parameters.width for operand in ("a", "b")):
                raise ValueError("operand exceeds unsigned width")
        # A complete experiment observes all accepted transactions unless reset cancels them.
        pending = 0
        for op in self.operations:
            if isinstance(op, Reset) or isinstance(op, Cycle) and op.reset:
                pending = 0
            elif isinstance(op, Multiply) or isinstance(op, Cycle) and op.valid:
                pending = self.parameters.latency
            else:
                pending = max(0, pending - getattr(op, "cycles", 1))
        if pending:
            raise ValueError("append latency idle cycles to drain in-flight products")
        return self


class ArithmeticContract(WireModel):
    schema_version: Literal["arithmetic.contract.v1"] = "arithmetic.contract.v1"
    contract_id: Literal["unsigned-multiplier-v1"] = "unsigned-multiplier-v1"
    target: Literal["arithmetic"] = "arithmetic"
    version: Literal["1"] = "1"
    review_status: Literal["reviewed"] = "reviewed"
    parameters: Parameters
    checker: CheckerIdentity
    requirements: tuple[Requirement, ...]
    timing: str = "Input accepted at rising edge n produces valid full-width unsigned a*b after edge n+2."
    reset: str = "Synchronous active-high reset overrides valid, cancels all in-flight products and clears output valid/data."
    bubbles: str = "No backpressure; accepts every valid non-reset edge. Output valid exactly tracks accepted inputs delayed two edges; invalid output data is unspecified."
    assumptions: tuple[str, ...] = ("First edge resets; inputs stable before rising edge; sample settled post-edge outputs.", "Operands are unsigned WIDTH bits; product is 2*WIDTH bits; drain or reset before experiment end.")
