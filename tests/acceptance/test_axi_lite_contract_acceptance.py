"""Independent hand-written bus histories; no driver/HDL execution or oracle clone."""
import pytest
from pydantic import ValidationError
from veriforge.axi_lite import AxiLiteExperiment, check


def history(inputs, outputs, **parameters):
    """Prefix only reset/release; every functional row is explicit in each test."""
    assert len(inputs) == len(outputs)
    experiment = AxiLiteExperiment(experiment_id="vf055c-independent", parameters=parameters,
        cycles=({"reset_n": False}, {}, *inputs))
    observations = [{"cycle": cycle, "aw_ready": False, "w_ready": False, "b_valid": False,
                     "ar_ready": False, "r_valid": False, **row}
                    for cycle, row in enumerate(({}, {}, *outputs))]
    return experiment, observations


@pytest.mark.parametrize("mask,readback", tuple(enumerate((
    0x11223344, 0x112233DD, 0x1122CC44, 0x1122CCDD,
    0x11BB3344, 0x11BB33DD, 0x11BBCC44, 0x11BBCCDD,
    0xAA223344, 0xAA2233DD, 0xAA22CC44, 0xAA22CCDD,
    0xAABB3344, 0xAABB33DD, 0xAABBCC44, 0xAABBCCDD))))
def test_all_masks_through_decoupled_stalled_channels_and_external_readback(mask, readback):
    # Bytes 44/33/22/11 become DD/CC/BB/AA only on selected lanes.
    # Unlike a direct apply_strobes unit test, correctness must be externally read.
    aw = {"aw_valid": True, "aw_addr": 12, "aw_prot": 7}
    w = {"w_valid": True, "w_data": 0xAABBCCDD, "w_strb": mask}
    first, second = (w, aw) if mask % 2 else (aw, w)
    first_ready, second_ready = ({"w_ready": True}, {"aw_ready": True}) if mask % 2 else ({"aw_ready": True}, {"w_ready": True})
    experiment, observed = history([
        {"aw_valid": True, "aw_addr": 12, "w_valid": True, "w_data": 0x11223344}, {},
        first, first, second, second, {"ar_valid": True, "ar_addr": 12, "ar_prot": 5}, {},
    ], [
        {"aw_ready": True, "w_ready": True}, {"b_valid": True},
        {}, first_ready, {}, second_ready, {"b_valid": True, "ar_ready": True},
        {"r_valid": True, "r_data": readback},
    ])
    assert check(experiment, observed).verdict == "pass_within_scope"
    observed[-1]["r_data"] = readback ^ 1
    failure = check(experiment, observed)
    assert failure.verdict == "design_mismatch" and failure.failure.requirement_id == "AXIL_READ"
    assert failure.failure.cycle == 9


@pytest.mark.parametrize("first", ["aw", "w"])
def test_bresponse_on_second_request_acceptance_edge_is_too_early(first):
    second = "w" if first == "aw" else "aw"
    experiment, observed = history([{first + "_valid": True}, {second + "_valid": True}],
        [{first + "_ready": True}, {second + "_ready": True, "b_valid": True}])
    outcome = check(experiment, observed)
    assert outcome.verdict == "property_failure"
    assert outcome.failure.failure_class == "axil.unsolicited_b" and outcome.failure.cycle == 3


def collision_history():
    # Three commits (7,9,11), and snapshots on the second/third commit edges.
    # Old B/R consumption frees slots before same-edge new AW/W/AR acceptance.
    return history([
        {"aw_valid": True, "w_valid": True, "w_data": 7},
        {"aw_valid": True, "w_valid": True, "w_data": 9, "ar_valid": True},
        {"aw_valid": True, "w_valid": True, "w_data": 11, "ar_valid": True},
        {}, {"ar_valid": True}, {},
    ], [
        {"aw_ready": True, "w_ready": True},
        {"b_valid": True, "aw_ready": True, "w_ready": True, "ar_ready": True},
        {"b_valid": True, "r_valid": True, "r_data": 7, "aw_ready": True, "w_ready": True, "ar_ready": True},
        {"b_valid": True, "r_valid": True, "r_data": 9}, {"ar_ready": True}, {"r_valid": True, "r_data": 11},
    ])


def test_old_value_snapshots_survive_same_edge_response_replacement():
    experiment, observed = collision_history()
    outcome = check(experiment, observed)
    assert outcome.verdict == "pass_within_scope" and outcome.registers == (11, 0, 0, 0)
    observed[4]["r_data"] = 9  # New commit value must not leak into old snapshot.
    outcome = check(experiment, observed)
    assert outcome.verdict == "design_mismatch" and outcome.failure.cycle == 4
    assert outcome.failure.requirement_id == "AXIL_READ"


@pytest.mark.parametrize("channel,payload", [("aw", "aw_prot"), ("w", "w_strb"), ("ar", "ar_prot")])
def test_non_data_manager_control_fields_are_held_until_actual_ready(channel, payload):
    experiment, observed = history([
        {channel + "_valid": True, payload: 1}, {channel + "_valid": True, payload: 2},
    ], [{}, {channel + "_ready": True}])
    outcome = check(experiment, observed)
    assert outcome.verdict == "harness_error" and outcome.failure is None
    assert "Manager changed stalled" in outcome.uncertainties[0]


@pytest.mark.parametrize("accept_too_early", [False, True])
def test_offered_second_write_waits_for_response_capacity_without_false_ready_deadline(accept_too_early):
    request = {"aw_valid": True, "aw_addr": 4, "w_valid": True, "w_data": 29}
    # Hold second write for five response-stall edges: beyond READY bound=4,
    # but no free capacity exists, so that request deadline has not started.
    inputs = [{"aw_valid": True, "w_valid": True, "w_data": 17}]
    inputs += [{**request, "b_ready": False} for _ in range(5)]
    inputs += [request, {}, {"ar_valid": True, "ar_addr": 4}, {}]
    outputs = [{"aw_ready": True, "w_ready": True}] + [{"b_valid": True} for _ in range(5)]
    outputs += [{"b_valid": True, "aw_ready": True, "w_ready": True}, {"b_valid": True},
                {"ar_ready": True}, {"r_valid": True, "r_data": 29}]
    if accept_too_early: outputs[1]["aw_ready"] = True
    experiment, observed = history(inputs, outputs)
    outcome = check(experiment, observed)
    if accept_too_early:
        assert outcome.verdict == "property_failure" and outcome.failure.requirement_id == "AXIL_ORDER"
    else:
        assert outcome.verdict == "pass_within_scope" and outcome.registers == (17, 29, 0, 0)


def test_reset_cancels_w_first_half_and_pending_read_then_new_address_pairs_only_new_data():
    experiment, observed = history([
        {"w_valid": True, "w_data": 0xDEADBEEF, "ar_valid": True},
        {"reset_n": False}, {}, {"aw_valid": True, "aw_addr": 8},
        {"w_valid": True, "w_data": 23}, {}, {"ar_valid": True, "ar_addr": 8}, {},
    ], [
        {"w_ready": True, "ar_ready": True}, {}, {}, {"aw_ready": True},
        {"w_ready": True}, {"b_valid": True}, {"ar_ready": True}, {"r_valid": True, "r_data": 23},
    ])
    outcome = check(experiment, observed)
    assert outcome.verdict == "pass_within_scope" and outcome.registers == (0, 0, 23, 0)
    # Cancelled old response must not escape on the release edge.
    observed[4]["r_valid"] = True
    outcome = check(experiment, observed)
    assert outcome.verdict == "property_failure" and outcome.failure.requirement_id == "AXIL_ORDER"


def test_every_reset_release_requires_a_quiescent_preedge_sample():
    with pytest.raises(ValidationError, match="reset release"):
        history([{"reset_n": False}, {"ar_valid": True}], [{}, {}])


@pytest.mark.parametrize("change", [None, {"r_data": 5}, {"r_valid": False}, {"r_resp": 2}])
def test_stalled_read_snapshot_stays_stable_while_independent_write_commits(change):
    experiment, observed = history([
        {"ar_valid": True},
        {"r_ready": False, "aw_valid": True, "w_valid": True, "w_data": 5},
        {"r_ready": False}, {"ar_valid": True}, {},
    ], [
        {"ar_ready": True},
        {"r_valid": True, "r_data": 0, "aw_ready": True, "w_ready": True},
        {"r_valid": True, "r_data": 0, "b_valid": True},
        {"r_valid": True, "r_data": 0, "ar_ready": True},
        {"r_valid": True, "r_data": 5},
    ])
    if change: observed[4].update(change)
    outcome = check(experiment, observed)
    if change:
        assert outcome.verdict == "property_failure" and outcome.failure.requirement_id == "AXIL_HANDSHAKE"
        assert outcome.failure.cycle == 4
    else:
        assert outcome.verdict == "pass_within_scope" and outcome.registers == (5, 0, 0, 0)


@pytest.mark.parametrize("channel", ["b", "r"])
def test_response_assertion_bound_does_not_depend_on_manager_ready(channel):
    request = {"aw_valid": True, "w_valid": True} if channel == "b" else {"ar_valid": True}
    accepted = {"aw_ready": True, "w_ready": True} if channel == "b" else {"ar_ready": True}
    experiment, observed = history([request, *({channel + "_ready": False} for _ in range(4))],
                                   [accepted, {}, {}, {}, {}])
    outcome = check(experiment, observed)
    assert outcome.verdict == "property_failure" and outcome.failure.requirement_id == "AXIL_PROGRESS"
    assert outcome.failure.failure_class == "axil.response_bound" and outcome.failure.cycle == 6


@pytest.mark.parametrize("channel", ["b", "r"])
def test_exact_external_stall_bound_can_drain_but_one_extra_is_inconclusive(channel):
    request = {"aw_valid": True, "w_valid": True} if channel == "b" else {"ar_valid": True}
    accepted = {"aw_ready": True, "w_ready": True} if channel == "b" else {"ar_ready": True}
    inputs = [request, *({channel + "_ready": False} for _ in range(3)), {}]
    outputs = [accepted, *({channel + "_valid": True} for _ in range(4))]
    experiment, observed = history(inputs, outputs, max_response_stall=3)
    assert check(experiment, observed).verdict == "pass_within_scope"
    inputs[-1] = {channel + "_ready": False}
    experiment, observed = history(inputs, outputs, max_response_stall=3)
    outcome = check(experiment, observed)
    assert outcome.verdict == "inconclusive" and outcome.failure is None
    assert "backpressure" in outcome.uncertainties[0]


@pytest.mark.parametrize("half", ["aw", "w"])
def test_missing_write_half_is_environment_limit_not_subordinate_defect(half):
    experiment, observed = history([{half + "_valid": True}, {}, {}, {}],
                                   [{half + "_ready": True}, {}, {}, {}], write_pair_bound=3)
    outcome = check(experiment, observed)
    assert outcome.verdict == "inconclusive" and outcome.failure is None
    assert "write halves" in outcome.uncertainties[0]


@pytest.mark.parametrize("channel", ["aw", "w", "ar"])
def test_free_request_slot_can_accept_at_last_bound_opportunity(channel):
    request = {channel + "_valid": True}
    # Reset explicitly cancels accepted partial request / pending response.
    experiment, observed = history([request, request, request, request, {"reset_n": False}],
        [{}, {}, {}, {channel + "_ready": True}, {}], request_ready_bound=4)
    assert check(experiment, observed).verdict == "pass_within_scope"
    observed[5][channel + "_ready"] = False
    outcome = check(experiment, observed)
    assert outcome.verdict == "property_failure" and outcome.failure.requirement_id == "AXIL_PROGRESS"
    assert outcome.failure.cycle == 5
