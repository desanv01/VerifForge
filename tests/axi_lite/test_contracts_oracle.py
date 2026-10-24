"""Hand-derived AXI-Lite examples; no HDL/driver/simulator or model calls."""
import json

import pytest
from pydantic import ValidationError

from veriforge.axi_lite import (
    AxiLiteCycle, AxiLiteExperiment, AxiLiteObservation, AxiLiteTrace, CheckOutcome, Parameters,
    apply_strobes, check, reviewed_contract,
)
from veriforge.contracts import Experiment, canonical_hash


def plan(*cycles, **changes):
    values = dict(experiment_id="axil-hand-derived", cycles=({"reset_n": False}, {}, *cycles))
    values.update(changes)
    return AxiLiteExperiment(**values)


def observations(*rows):
    return [AxiLiteObservation(cycle=i, **{
        "aw_ready": False, "w_ready": False, "b_valid": False,
        "ar_ready": False, "r_valid": False, **row,
    }) for i, row in enumerate(({}, {}, *rows))]


@pytest.mark.parametrize("mask,expected", tuple(enumerate([
    0x11223344, 0x112233DD, 0x1122CC44, 0x1122CCDD,
    0x11BB3344, 0x11BB33DD, 0x11BBCC44, 0x11BBCCDD,
    0xAA223344, 0xAA2233DD, 0xAA22CC44, 0xAA22CCDD,
    0xAABB3344, 0xAABB33DD, 0xAABBCC44, 0xAABBCCDD,
])))
def test_all_byte_masks_from_hand_written_hex_table(mask, expected):
    assert apply_strobes(0x11223344, 0xAABBCCDD, mask) == expected


@pytest.mark.parametrize("data_first", [False, True])
def test_aw_w_ordering_independent_with_intervening_idle_and_readback(data_first):
    address = {"aw_valid": True, "aw_addr": 8, "aw_prot": 7}
    data = {"w_valid": True, "w_data": 0xAABBCCDD, "w_strb": 5}
    first, last = (data, address) if data_first else (address, data)
    first_ready, last_ready = ({"w_ready": True}, {"aw_ready": True}) if data_first else ({"aw_ready": True}, {"w_ready": True})
    experiment = plan(first, {}, last, {}, {"ar_valid": True, "ar_addr": 8, "ar_prot": 5}, {})
    got = observations(first_ready, {}, last_ready, {"b_valid": True}, {"ar_ready": True},
                       {"r_valid": True, "r_data": 0x00BB00DD})
    result = check(experiment, got)
    assert result.verdict == "pass_within_scope" and result.registers == (0, 0, 0x00BB00DD, 0)


def stalled_exchange():
    return plan(
        {"aw_valid": True, "w_valid": True, "w_data": 37, "ar_valid": True},
        {"b_ready": False, "r_ready": False}, {"b_ready": False, "r_ready": False}, {},
        {"ar_valid": True}, {},
    ), observations(
        {"aw_ready": True, "w_ready": True, "ar_ready": True},
        {"b_valid": True, "r_valid": True, "r_data": 0},
        {"b_valid": True, "r_valid": True, "r_data": 0},
        {"b_valid": True, "r_valid": True, "r_data": 0},
        {"ar_ready": True}, {"r_valid": True, "r_data": 37},
    )


def test_read_before_same_edge_write_and_both_response_stalls():
    experiment, got = stalled_exchange()
    result = check(experiment, got)
    assert result.verdict == "pass_within_scope" and result.registers[0] == 37


@pytest.mark.parametrize("channel,change", [
    ("b", {"b_valid": False}), ("b", {"b_resp": 2}),
    ("r", {"r_valid": False}), ("r", {"r_data": 1}), ("r", {"r_resp": 2}),
])
def test_stalled_response_valid_and_payload_cannot_change(channel, change):
    experiment, got = stalled_exchange()
    got[4] = got[4].model_copy(update=change)
    result = check(experiment, got)
    assert result.verdict == "property_failure" and result.failure.requirement_id == "AXIL_HANDSHAKE"


def test_reset_discards_saved_aw_before_a_later_w_first_write():
    experiment = plan(
        {"aw_valid": True, "aw_addr": 0}, {"reset_n": False}, {},
        {"w_valid": True, "w_data": 7}, {"aw_valid": True, "aw_addr": 4}, {},
        {"ar_valid": True}, {},
    )
    result = check(experiment, observations({"aw_ready": True}, {}, {}, {"w_ready": True},
        {"aw_ready": True}, {"b_valid": True}, {"ar_ready": True}, {"r_valid": True, "r_data": 0}))
    assert result.verdict == "pass_within_scope" and result.registers == (0, 7, 0, 0)


def test_reset_cancels_stalled_responses_and_zeroes_committed_registers():
    experiment = plan({"aw_valid": True, "w_valid": True, "w_data": 17},
        {"b_ready": False}, {"reset_n": False}, {}, {"ar_valid": True}, {})
    result = check(experiment, observations({"aw_ready": True, "w_ready": True},
        {"b_valid": True}, {}, {}, {"ar_ready": True}, {"r_valid": True, "r_data": 0}))
    assert result.verdict == "pass_within_scope" and result.registers == (0, 0, 0, 0)


@pytest.mark.parametrize("address", [1, 2, 3, 16, 255])
def test_invalid_addresses_slverr_and_no_write_side_effect(address):
    experiment = plan({"aw_valid": True, "aw_addr": address, "w_valid": True, "w_data": 0xFFFFFFFF}, {},
                      {"ar_valid": True, "ar_addr": address}, {})
    result = check(experiment, observations({"aw_ready": True, "w_ready": True}, {"b_valid": True, "b_resp": 2},
                       {"ar_ready": True}, {"r_valid": True, "r_resp": 2, "r_data": 0}))
    assert result.verdict == "pass_within_scope" and not any(result.registers)


def test_zero_strobe_successfully_preserves_existing_register():
    experiment = plan({"aw_valid": True, "w_valid": True, "w_data": 91}, {},
        {"aw_valid": True, "w_valid": True, "w_data": 0xFFFFFFFF, "w_strb": 0}, {},
        {"ar_valid": True}, {})
    result = check(experiment, observations({"aw_ready": True, "w_ready": True}, {"b_valid": True},
        {"aw_ready": True, "w_ready": True}, {"b_valid": True}, {"ar_ready": True}, {"r_valid": True, "r_data": 91}))
    assert result.verdict == "pass_within_scope"


@pytest.mark.parametrize("response", ["b", "r"])
def test_unsolicited_responses_do_not_masquerade_as_success(response):
    result = check(plan({}), observations({response + "_valid": True}))
    assert result.verdict == "property_failure" and result.failure.requirement_id == "AXIL_ORDER"


def test_bvalid_requires_both_previously_accepted_write_channels():
    result = check(plan({"aw_valid": True}, {}), observations({"aw_ready": True}, {"b_valid": True}))
    assert result.failure.failure_class == "axil.unsolicited_b"


@pytest.mark.parametrize("channel", ["aw", "w", "ar"])
def test_second_accepted_request_overflows_only_this_products_single_slot(channel):
    experiment = plan({channel + "_valid": True}, {channel + "_valid": True})
    result = check(experiment, observations({channel + "_ready": True}, {channel + "_ready": True}))
    assert result.verdict == "property_failure" and result.failure.requirement_id == "AXIL_ORDER"


def test_response_cannot_be_duplicated_after_consumption():
    experiment = plan({"ar_valid": True}, {}, {})
    result = check(experiment, observations({"ar_ready": True}, {"r_valid": True}, {"r_valid": True}))
    assert result.failure.failure_class == "axil.unsolicited_r"


def test_new_read_on_same_edge_previous_response_consumed_is_permitted():
    experiment = plan({"ar_valid": True}, {"ar_valid": True, "ar_addr": 4}, {})
    result = check(experiment, observations({"ar_ready": True}, {"r_valid": True, "ar_ready": True}, {"r_valid": True}))
    assert result.verdict == "pass_within_scope"


def test_manager_dropping_stalled_valid_is_harness_error():
    result = check(plan({"aw_valid": True}, {}), observations({}, {}))
    assert result.verdict == "harness_error" and result.failure is None


@pytest.mark.parametrize("channel", ["b", "r"])
def test_reset_forbids_response_valid(channel):
    got = observations({})
    got[0] = got[0].model_copy(update={channel + "_valid": True})
    result = check(plan({}), got)
    assert result.verdict == "design_mismatch" and result.failure.requirement_id == "AXIL_RESET"


def test_invalid_response_payload_is_unspecified_outside_reset_and_valid():
    got = observations({"r_data": 0xFFFFFFFF, "r_resp": 3, "b_resp": 3})
    assert check(plan({}), got).verdict == "pass_within_scope"


@pytest.mark.parametrize("channel,payload", [("aw", {"aw_addr": 4}), ("w", {"w_data": 2}), ("ar", {"ar_addr": 4})])
def test_changed_manager_payload_while_stalled_is_harness_error(channel, payload):
    result = check(plan({channel + "_valid": True}, {channel + "_valid": True, **payload}), observations({}, {}))
    assert result.verdict == "harness_error" and result.failure is None


def test_ready_bound_is_a_product_property_independent_of_other_valid():
    result = check(plan(*({"aw_valid": True} for _ in range(4))), observations({}, {}, {}, {}))
    assert result.verdict == "property_failure" and result.failure.requirement_id == "AXIL_PROGRESS"
    assert result.failure.cycle == 5


def test_response_may_arrive_at_bound_and_is_not_tied_to_ready():
    experiment = plan({"ar_valid": True}, {}, {}, {}, {"r_ready": False}, {})
    result = check(experiment, observations({"ar_ready": True}, {}, {}, {},
                                           {"r_valid": True}, {"r_valid": True}))
    assert result.verdict == "pass_within_scope"


def test_missing_response_exceeds_product_bound_even_when_request_completed():
    result = check(plan({"ar_valid": True}, {}, {}, {}, {}), observations({"ar_ready": True}, {}, {}, {}, {}))
    assert result.verdict == "property_failure" and result.failure.failure_class == "axil.response_bound"


def test_unbounded_external_stall_is_inconclusive_not_generic_protocol_failure():
    experiment = plan({"ar_valid": True}, *({"r_ready": False} for _ in range(9)))
    got = observations({"ar_ready": True}, *({"r_valid": True} for _ in range(9)))
    result = check(experiment, got)
    assert result.verdict == "inconclusive" and result.failure is None
    assert "backpressure" in result.uncertainties[0]


def test_missing_manager_write_half_and_incomplete_tail_are_inconclusive():
    result = check(plan({"aw_valid": True}, *({} for _ in range(8))), observations({"aw_ready": True}, *({} for _ in range(8))))
    assert result.verdict == "inconclusive" and "write halves" in result.uncertainties[0]
    result = check(plan({"aw_valid": True}), observations({"aw_ready": True}))
    assert result.verdict == "inconclusive" and result.failure is None


@pytest.mark.parametrize("damage", ["short", "order", "unknown", "boolean", "late_bad"])
def test_malformed_complete_observation_envelope_is_harness_error(damage):
    experiment = plan({})
    got = [row.model_dump() for row in observations({})]
    if damage == "short": got.pop()
    if damage == "order": got[1]["cycle"] = 0
    if damage == "unknown": got[0]["command"] = "forbidden"
    if damage == "boolean": got[0]["r_data"] = True
    if damage == "late_bad":
        got[0]["r_valid"] = True
        got[1]["r_data"] = -1
    result = check(experiment, got)
    assert result.verdict == "harness_error" and result.checked_cycles == 0


@pytest.mark.parametrize("changes", [
    {"command": "anything"}, {"fixture_id": "../arbitrary"}, {"target": "fifo"}, {"schema_version": "1"},
    {"cycles": []}, {"cycles": [{}]}, {"cycles": [{"reset_n": False, "aw_valid": True}]},
    {"cycles": [{"reset_n": False}, {"aw_valid": True}]},
    {"cycles": [{"reset_n": False}, {"w_valid": True, "w_data": True}]},
    {"cycles": [{"reset_n": False}, {"aw_addr": 256}]},
    {"cycles": [{"reset_n": False}, {"w_strb": 16}]},
    {"parameters": {"data_width": 64}}, {"parameters": {"max_outstanding_writes": 2}},
    {"parameters": {"data_width": 32.0}}, {"parameters": {"max_outstanding_writes": True}},
    {"parameters": {"request_ready_bound": 0}}, {"parameters": {"response_bound": True}},
    {"parameters": {"max_response_stall": 33}}, {"objective_ids": ["AXIL_READ", "AXIL_READ"]},
    {"budget": {"max_cycles": 1}},
])
def test_strict_invalid_stimulus_rejected(changes):
    with pytest.raises(ValidationError):
        plan({}, **changes)


def test_roundtrip_immutable_identity_and_family_boundary():
    experiment = plan({})
    assert AxiLiteExperiment.model_validate_json(experiment.model_dump_json()) == experiment
    assert canonical_hash(reviewed_contract()) != canonical_hash(reviewed_contract(Parameters(response_bound=5)))
    with pytest.raises(ValidationError): Experiment.model_validate_json(experiment.model_dump_json())
    with pytest.raises(ValidationError): experiment.parameters.response_bound = 9
    outcome = check(experiment, observations({}))
    assert CheckOutcome.model_validate_json(outcome.model_dump_json()) == outcome
    assert json.loads(experiment.model_dump_json())["schema_version"] == "axi_lite.v1"


def test_versioned_observation_envelope_roundtrip_and_bounds():
    trace = AxiLiteTrace(observations=observations({}))
    assert AxiLiteTrace.model_validate_json(trace.model_dump_json()) == trace
    assert check(plan({}), trace).verdict == "pass_within_scope"
    assert check(plan({}), trace.model_dump()).verdict == "pass_within_scope"
    assert check(plan({}), {"schema_version": "wrong", "observations": observations({})}).verdict == "harness_error"
    with pytest.raises(ValidationError):
        AxiLiteTrace(observations=[observations({})[0]] * 4097)
