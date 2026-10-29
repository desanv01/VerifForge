"""Independent FIFO specification. No simulator, HDL, or planner dependency."""
from collections import deque

CONTRACT = {
    "id": "fifo-v1", "reset": "synchronous active high; clears queue and output",
    "acceptance": "pre-edge occupancy; no fallthrough or full replacement",
    "read": "registered oldest item; retains output when no accepted read",
    "flags": "post-edge occupancy", "priority": "reset overrides read/write",
}


def expand(experiment):
    cycles = []
    for index, operation in enumerate(experiment.operations):
        op = operation.model_dump()
        kind = op["op"]
        for _ in range(op.get("cycles", 1)):
            cycles.append({"operation_index": index, "reset": kind == "reset" or op.get("reset", False),
                           "write": kind == "write" or op.get("write", False),
                           "read": kind == "read" or op.get("read", False), "value": op.get("value", 0)})
    return cycles


def expected_trace(stimulus, depth):
    queue, output, rows = deque(), 0, []
    for cycle, inputs in enumerate(stimulus):
        read = not inputs["reset"] and inputs["read"] and bool(queue)
        write = not inputs["reset"] and inputs["write"] and len(queue) < depth
        if inputs["reset"]:
            queue.clear()
            output = 0
        else:
            if read:
                output = queue.popleft()
            if write:
                queue.append(inputs["value"])
        rows.append({"cycle": cycle, "operation_index": inputs["operation_index"],
                     "accepted_read": bool(read), "accepted_write": bool(write),
                     "data": output, "empty": not queue, "full": len(queue) == depth})
    return rows


def compare(expected, observed):
    if len(expected) != len(observed):
        raise ValueError("incomplete simulator observations")
    for want, got in zip(expected, observed):
        if got.get("cycle") != want["cycle"]:
            raise ValueError("misordered simulator observations")
        for signal in ("data", "empty", "full"):
            if got.get(signal) != want[signal]:
                return {"cycle": want["cycle"], "signal": signal,
                        "expected": want[signal], "observed": got.get(signal)}
    return None
