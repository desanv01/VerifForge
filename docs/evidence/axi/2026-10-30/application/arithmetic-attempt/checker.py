"""Independent mathematical checker using scheduled products, not HDL stages."""
def expand(experiment):
    rows = []
    for index, op in enumerate(experiment.operations):
        for _ in range(getattr(op, "cycles", 1)):
            rows.append({"operation_index": index, "reset": op.op == "reset" or getattr(op, "reset", False),
                         "valid": op.op == "multiply" or getattr(op, "valid", False),
                         "a": getattr(op, "a", 0), "b": getattr(op, "b", 0)})
    return rows


def expected_trace(stimulus, latency=2):
    pending, rows = {}, []
    for cycle, item in enumerate(stimulus):
        if item["reset"]:
            pending.clear()
            valid, product = False, 0
        else:
            valid = cycle in pending
            product = pending.pop(cycle, None)
            if item["valid"]:
                pending[cycle + latency] = item["a"] * item["b"]
        rows.append({"cycle": cycle, "operation_index": item["operation_index"],
                     "valid": valid, "product": product, "reset": item["reset"]})
    return rows


def compare(expected, observed):
    if len(expected) != len(observed):
        raise ValueError("incomplete simulator observations")
    # Validate the whole observation envelope before interpreting any design mismatch.
    for cycle, got in enumerate(observed):
        if set(got) != {"cycle", "valid", "product"} or type(got["cycle"]) is not int or got["cycle"] != cycle or type(got["valid"]) is not bool or type(got["product"]) is not int or got["product"] < 0:
            raise ValueError("malformed or misordered simulator observations")
    for want, got in zip(expected, observed):
        for signal in ("valid", "product"):
            if signal == "product" and not (want["valid"] or want["reset"]):
                continue
            if want[signal] != got[signal]:
                requirement = "ARITH_RESET" if want["reset"] else "ARITH_LATENCY" if signal == "valid" else "ARITH_PRODUCT"
                return {"cycle": want["cycle"], "signal": signal, "requirement": requirement,
                        "expected": want[signal], "observed": got[signal]}
    return None
