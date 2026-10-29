"""Independent event comparison: no DUT/reference execution or CPU emulation."""
import json
from .contracts import Comparison, FirstDivergence, Retirement
from .profile import PLATFORM
from .trace import TraceFormatError


def _validated(rows):
    events = tuple(Retirement.model_validate(row.model_dump() if isinstance(row, Retirement) else row) for row in rows)
    if any(event.order != index for index, event in enumerate(events)):
        raise TraceFormatError("non-contiguous architectural retirement order")
    return events


def _complete(events):
    if not events:
        return False
    last = events[-1]
    return bool(last.memory and last.memory.kind == "write" and last.memory.address == PLATFORM.completion
                and last.memory.width == 4 and last.memory.byte_mask == 15
                and last.memory.value == PLATFORM.completion_value and not last.trap)


def compare_retirements(reference, observed):
    expected, actual = _validated(reference), _validated(observed)
    if not _complete(expected) or expected[0].pc != PLATFORM.entry or any(event.trap or event.interrupt for event in expected):
        return Comparison(verdict="inconclusive", compared=0, reason="reference did not complete the supported control")
    fields = ("pc", "instruction", "rd", "rd_value", "trap", "interrupt", "memory")
    for index, (want, got) in enumerate(zip(expected, actual)):
        for field in fields:
            if getattr(want, field) != getattr(got, field):
                a, b = want.model_dump(mode="json")[field], got.model_dump(mode="json")[field]
                return Comparison(verdict="design_mismatch", compared=index+1, reason="first architectural divergence",
                    first_divergence=FirstDivergence(order=index, field=field,
                        expected=json.dumps(a, sort_keys=True), observed=json.dumps(b, sort_keys=True)))
    if len(expected) != len(actual) or not _complete(actual):
        return Comparison(verdict="inconclusive", compared=min(len(expected), len(actual)), reason="incomplete observed retirement trace")
    return Comparison(verdict="pass_within_scope", compared=len(expected), reason="architectural events agree through completion; no cycle comparison")
