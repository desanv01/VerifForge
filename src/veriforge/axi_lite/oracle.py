"""Handshake ledger and integer register map, independent of future HDL/driver."""
from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from veriforge.contracts import CheckerIdentity, Requirement
from .contracts import (
    AxiLiteContract, AxiLiteExperiment, AxiLiteTrace, CheckFailure, CheckOutcome, Parameters,
)


def reviewed_contract(parameters: Parameters | None = None) -> AxiLiteContract:
    return AxiLiteContract(parameters=parameters or Parameters(),
        checker=CheckerIdentity(checker_id="axil-handshake-register-ledger", version="1",
                                sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),
        requirements=tuple(Requirement(requirement_id=key, description=description) for key, description in (
            ("AXIL_HANDSHAKE", "VALID and response payload persist until transfer or reset."),
            ("AXIL_ORDER", "Independent AW/W pairing, bounded outstanding work, no unsolicited/duplicate responses."),
            ("AXIL_STROBE", "Per-byte masked writes at mapped aligned addresses; invalid writes have no effect."),
            ("AXIL_READ", "AR snapshots register before same-edge write; invalid reads return SLVERR and zero."),
            ("AXIL_RESET", "Reset clears registers and responses, cancels partial and paired requests."),
            ("AXIL_PROGRESS", "Product-specific bounded READY and response assertion under documented assumptions."),
        )))


def apply_strobes(old: int, data: int, strobe: int) -> int:
    """Replace selected little-endian byte lanes, with no HDL width arithmetic."""
    return sum(((data if strobe & (1 << lane) else old) >> (8 * lane) & 255) << (8 * lane)
               for lane in range(4))


def _mapped(address):
    return address % 4 == 0 and 0 <= address < 16


def check(experiment: AxiLiteExperiment, observations) -> CheckOutcome:
    """Score exact applied inputs against observed pre-edge signals.

    Malformed complete envelopes fail before functional scoring. Actual executor
    status, timeout, tool provenance and process limits remain runner duties.
    """
    experiment = AxiLiteExperiment.model_validate(experiment)
    try:
        trace = (AxiLiteTrace.model_validate(observations) if isinstance(observations, (dict, AxiLiteTrace))
                 else AxiLiteTrace(observations=observations))
        observed = trace.observations
    except (ValidationError, TypeError, ValueError):
        return CheckOutcome(verdict="harness_error", checked_cycles=0, uncertainties=("Malformed observation envelope",))
    if len(observed) != experiment.cycle_count or any(row.cycle != i for i, row in enumerate(observed)):
        return CheckOutcome(verdict="harness_error", checked_cycles=0, uncertainties=("Incomplete or misordered observation envelope",))
    p = experiment.parameters
    registers = [0] * 4
    aw = wd = write = read = None
    offered = {}
    stalls = {"b": 0, "r": 0}
    previous = None

    def outcome(verdict, cycle, requirement=None, name=None, want=None, got=None, uncertainty=None):
        failure = (CheckFailure(cycle=cycle, requirement_id=requirement, failure_class=name,
                                expected=str(want), observed=str(got)) if requirement else None)
        return CheckOutcome(verdict=verdict, failure=failure, checked_cycles=cycle + 1,
                            registers=tuple(registers), uncertainties=(uncertainty,) if uncertainty else ())

    for i, (drive, got) in enumerate(zip(experiment.cycles, observed)):
        if not drive.reset_n:
            if got.b_valid or got.r_valid:
                return outcome("design_mismatch", i, "AXIL_RESET", "axil.reset_valid", False, True)
            registers = [0] * 4
            aw = wd = write = read = None
            offered.clear()
            stalls = {"b": 0, "r": 0}
            previous = None
            continue
        if previous is not None:
            before, seen = previous
            for channel, payload in (("aw", ("addr", "prot")), ("w", ("data", "strb")), ("ar", ("addr", "prot"))):
                if getattr(before, channel + "_valid") and not getattr(seen, channel + "_ready"):
                    if not getattr(drive, channel + "_valid") or any(getattr(drive, channel + "_" + field) != getattr(before, channel + "_" + field) for field in payload):
                        return outcome("harness_error", i, uncertainty="Manager changed stalled " + channel + " VALID/payload")
            for channel, payload in (("b", ("resp",)), ("r", ("resp", "data"))):
                if getattr(seen, channel + "_valid") and not getattr(before, channel + "_ready"):
                    if not getattr(got, channel + "_valid") or any(getattr(got, channel + "_" + field) != getattr(seen, channel + "_" + field) for field in payload):
                        return outcome("property_failure", i, "AXIL_HANDSHAKE", "axil.stalled_response", "stable VALID/payload", channel)

        # Responses refer only to requests accepted on prior edges.
        if got.b_valid:
            if write is None:
                return outcome("property_failure", i, "AXIL_ORDER", "axil.unsolicited_b", "prior AW and W", "BVALID")
            if got.b_resp != write[1]:
                return outcome("design_mismatch", i, "AXIL_STROBE", "axil.bresp", write[1], got.b_resp)
        if got.r_valid:
            if read is None:
                return outcome("property_failure", i, "AXIL_ORDER", "axil.unsolicited_r", "prior AR", "RVALID")
            if (got.r_resp, got.r_data) != read[1:]:
                return outcome("design_mismatch", i, "AXIL_READ", "axil.read_value", read[1:], (got.r_resp, got.r_data))

        for channel, pending, valid in (("b", write, got.b_valid), ("r", read, got.r_valid)):
            if pending is not None and not valid and i - pending[0] >= p.response_bound:
                return outcome("property_failure", i, "AXIL_PROGRESS", "axil.response_bound", f"{channel}VALID within {p.response_bound} edges", i - pending[0])
            stalls[channel] = stalls[channel] + 1 if valid and not getattr(drive, channel + "_ready") else 0
            if stalls[channel] > p.max_response_stall:
                return outcome("inconclusive", i, uncertainty="External response backpressure exceeded finite environment assumption")

        if got.b_valid and drive.b_ready:
            aw = wd = write = None
        if got.r_valid and drive.r_ready:
            read = None

        # Read snapshots old storage before any same-edge paired write commit.
        if drive.ar_valid and got.ar_ready:
            if read is not None:
                return outcome("property_failure", i, "AXIL_ORDER", "axil.read_overflow", "one outstanding read", "second AR accepted")
            read = (i, 0, registers[drive.ar_addr // 4]) if _mapped(drive.ar_addr) else (i, 2, 0)
        if drive.aw_valid and got.aw_ready:
            if aw is not None or write is not None:
                return outcome("property_failure", i, "AXIL_ORDER", "axil.write_overflow", "one AW slot", "second AW accepted")
            aw = (i, drive.aw_addr)
        if drive.w_valid and got.w_ready:
            if wd is not None or write is not None:
                return outcome("property_failure", i, "AXIL_ORDER", "axil.write_overflow", "one W slot", "second W accepted")
            wd = (i, drive.w_data, drive.w_strb)
        if aw is not None and wd is not None and write is None:
            valid_address = _mapped(aw[1])
            if valid_address:
                index = aw[1] // 4
                registers[index] = apply_strobes(registers[index], wd[1], wd[2])
            write = (i, 0 if valid_address else 2)

        # READY progress is measured only when the corresponding slot was free;
        # other request-channel VALID is not a prerequisite in this product.
        for channel, slot, accepted in (
            ("aw", aw, drive.aw_valid and got.aw_ready),
            ("w", wd, drive.w_valid and got.w_ready),
            ("ar", read, drive.ar_valid and got.ar_ready),
        ):
            valid = getattr(drive, channel + "_valid")
            if accepted or not valid:
                offered.pop(channel, None)
            elif slot is None and not (channel in ("aw", "w") and write is not None):
                offered.setdefault(channel, i)
                if i - offered[channel] + 1 >= p.request_ready_bound:
                    return outcome("property_failure", i, "AXIL_PROGRESS", "axil.ready_bound", f"{channel}READY within {p.request_ready_bound} offered edges", i - offered[channel] + 1)
        if write is None and (aw is not None or wd is not None):
            first = aw[0] if aw is not None else wd[0]
            if i - first >= p.write_pair_bound:
                return outcome("inconclusive", i, uncertainty="Manager did not complete write halves within finite environment assumption")
        previous = (drive, got)

    if any(item is not None for item in (aw, wd, write, read)) or offered:
        return outcome("inconclusive", len(observed) - 1, uncertainty="Trace ended with unobserved or unconsumed requests/responses")
    return outcome("pass_within_scope", len(observed) - 1)
