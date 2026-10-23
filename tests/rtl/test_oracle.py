from veriforge.rtl.oracle import expected_trace, compare


def cycle(**kwargs):
    return dict(operation_index=0, reset=False, write=False, read=False, value=0, **kwargs)


def test_hand_derived_boundary_and_reset():
    stimulus = [dict(cycle(), reset=True), dict(cycle(), read=True, write=True, value=17),
                dict(cycle(), write=True, value=42), dict(cycle(), read=True, write=True, value=99),
                dict(cycle(), read=True), dict(cycle(), read=True), dict(cycle(), write=True, value=63),
                dict(cycle(), reset=True, write=True, read=True)]
    rows = expected_trace(stimulus, 2)
    assert [(r["data"], r["empty"], r["full"]) for r in rows] == [
        (0, True, False), (0, False, False), (0, False, True), (17, False, False),
        (42, True, False), (42, True, False), (42, False, False), (0, True, False)]
    assert not rows[1]["accepted_read"]
    assert not rows[3]["accepted_write"]
    assert not rows[-1]["accepted_write"] and not rows[-1]["accepted_read"]


def test_mismatch_is_observation_derived():
    rows = expected_trace([dict(cycle(), reset=True)], 4)
    assert compare(rows, [{"cycle": 0, "data": 0, "empty": True, "full": False}]) is None
    assert compare(rows, [{"cycle": 0, "data": 0, "empty": False, "full": False}])["signal"] == "empty"
