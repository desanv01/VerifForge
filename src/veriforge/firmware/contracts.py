"""Local firmware fact records; no shared manifest or execution schema change."""
from typing import Annotated, Literal
from pydantic import Field, StrictInt, model_validator
from veriforge.contracts import WireModel, Sha256

U32 = Annotated[StrictInt, Field(ge=0, lt=2**32)]
Name = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,95}$")]


class Provenance(WireModel):
    source: Annotated[str, Field(min_length=1, max_length=512)]
    source_sha256: Sha256
    location: Annotated[str, Field(min_length=1, max_length=128)]
    extractor: Literal["firmware.facts.v1"] = "firmware.facts.v1"
    origin: Literal["synthetic_fixture", "imported_artifact"]
    review_state: Literal["extracted_unreviewed"] = "extracted_unreviewed"
    conditions: tuple[str, ...] = ()


class AddressFact(WireModel):
    name: Name
    value: U32
    unit: Literal["byte_address"] = "byte_address"
    provenance: Provenance


class RegisterBinding(WireModel):
    logical_name: Name
    header_macro: Name
    rtl_signal: Name
    address: U32
    width_bits: Literal[32] = 32
    access: Literal["rw"] = "rw"
    reset_value: U32 = 0
    byte_strobes: Literal["per_byte"] = "per_byte"
    side_effects: Literal["none"] = "none"


class RegisterContract(WireModel):
    schema_version: Literal["firmware.registers.v1"] = "firmware.registers.v1"
    review_state: Literal["reviewed_fixture", "reviewed_user"]
    registers: Annotated[tuple[RegisterBinding, ...], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def unique(self):
        for field in ("logical_name", "header_macro", "rtl_signal", "address"):
            values = [getattr(row, field) for row in self.registers]
            if len(values) != len(set(values)):
                raise ValueError("duplicate register binding " + field)
        if any(row.address % 4 for row in self.registers):
            raise ValueError("fixture register addresses must be word aligned")
        return self


class LoadSegment(WireModel):
    offset: U32
    virtual_address: U32
    physical_address: U32
    file_size: U32
    memory_size: U32
    flags: Annotated[StrictInt, Field(ge=0, le=7)]
    file_sha256: Sha256


class ElfFacts(WireModel):
    schema_version: Literal["firmware.elf32.v1"] = "firmware.elf32.v1"
    machine: Literal[243] = 243
    elf_type: Literal[2] = 2
    byte_order: Literal["little"] = "little"
    entry: U32
    flags: U32
    load_segments: Annotated[tuple[LoadSegment, ...], Field(max_length=128)]
    provenance: Provenance
    limitations: tuple[str, ...] = ("ELF flags are not a complete ISA declaration; attributes/instruction validation and actual core execution remain required.",)


class InstructionFact(WireModel):
    address: U32
    encoding: U32
    width_bits: Literal[16, 32]
    mnemonic: Annotated[str, Field(min_length=1, max_length=32)]
    operands: Annotated[str, Field(max_length=256)]
    provenance: Provenance


class MmioTransaction(WireModel):
    sequence: Annotated[StrictInt, Field(ge=0, le=4095)]
    operation: Literal["read", "write"]
    address: U32
    data: U32
    byte_strobe: Annotated[StrictInt, Field(ge=0, le=15)] = 15
    pc: U32 | None = None


class MmioFacts(WireModel):
    schema_version: Literal["firmware.mmio.facts.v1"] = "firmware.mmio.facts.v1"
    transactions: Annotated[tuple[MmioTransaction, ...], Field(max_length=4096)]
    provenance: Provenance


class FactIssue(WireModel):
    register_name: Name
    artifact: Literal["header", "rtl", "mmio"]
    expected_address: U32
    observed_address: U32 | None
    provenance: Provenance | None = None


class FactComparison(WireModel):
    schema_version: Literal["firmware.comparison.v1"] = "firmware.comparison.v1"
    verdict: Literal["facts_agree", "mismatch", "inconclusive"]
    execution_status: Literal["not_run"] = "not_run"
    contract_hash: Sha256
    issues: tuple[FactIssue, ...] = ()
    limitations: tuple[str, ...] = ("Address comparison does not verify access policy, reset, byte-strobe behavior, side effects, ELF startup or firmware execution.",)


class PlatformProfile(WireModel):
    schema_version: Literal["firmware.platform.v1"] = "firmware.platform.v1"
    core_revision: Literal["ef203c2b0a3fb793280f5114941416c425c5b461"] = "ef203c2b0a3fb793280f5114941416c425c5b461"
    reference_revision: Literal["4c6a9bc79dab80616932949707e7c0471d8e9e54"] = "4c6a9bc79dab80616932949707e7c0471d8e9e54"
    isa: Literal["RV32I"] = "RV32I"
    abi: Literal["ilp32"] = "ilp32"
    byte_order: Literal["little"] = "little"
    ram_start: Literal[0x80000000] = 0x80000000
    ram_end_exclusive: Literal[0x80010000] = 0x80010000
    reset_vector: Literal[0x80000000] = 0x80000000
    completion_mailbox: Literal[0x8000FF00] = 0x8000FF00
    completion_value: Literal[1] = 1
    mmio_start: Literal[0x40000000] = 0x40000000
    mmio_end_exclusive: Literal[0x40000010] = 0x40000010
    memory_bus: Literal["picorv32_native_valid_ready_32bit_byte_wstrb"] = "picorv32_native_valid_ready_32bit_byte_wstrb"
    compiler_flags: tuple[str, ...] = ("-march=rv32i", "-mabi=ilp32", "-mno-relax", "-nostdlib", "-nostartfiles")
    execution_status: Literal["not_run"] = "not_run"


class LayoutCheck(WireModel):
    verdict: Literal["layout_agrees", "mismatch", "inconclusive"]
    execution_status: Literal["not_run"] = "not_run"
    profile_hash: Sha256
    elf_hash: Sha256
    issues: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ("Layout agreement does not prove complete ISA compatibility, stack initialization, mailbox behavior, boot, core or peripheral execution.",)
