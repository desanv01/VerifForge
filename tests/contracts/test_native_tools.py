"""Synthetic contract data only; no hosted/tool execution or blob verification."""
from copy import deepcopy
from hashlib import sha256

import pytest
from pydantic import TypeAdapter, ValidationError

from veriforge.contracts.native_tools import (
    ArtifactSelection, BooleanDivergence, FirmwareRecordedReport, HostedOrigin,
    IntegerDivergence, InventoryEntry, MailboxObservation, MemoryDivergence, MemoryEffect, MmioConflict,
    ProducerCaseLocator, RecordedToolReport, RiscvRecordedReport,
    RecordedArtifactBinding, TransportDigest,
)


def digest(text):
    return sha256(text.encode()).hexdigest()


def ref(name, content=None, attempt=None):
    return dict(artifact_id=name, revision_id="adapter-revision", attempt_id=attempt, sha256=digest(content or name))


def origin(firmware=False):
    return dict(kind="github_actions", repository="desanv01/VerifForge",
                source_commit="5565985bbe2bb2a0be869d366baa48c715057657" if firmware else "44b8b5e40ce7841f076e4178df13f38ecdbd05fd",
                run_id=40929665294 if firmware else 40920450439, run_attempt=1,
                artifact_id=12922737365 if firmware else 12919420477,
                extracted_receipt_ref=ref("receipt"),
                transport_digest=dict(sha256=digest("reported-zip"), verification="github_reported"))


class Builder:
    def __init__(self, firmware=False):
        self.origin = origin(firmware)
        self.bindings = []
        self.add("receipt", self.origin, "receipt", supplied=self.origin["extracted_receipt_ref"])
        self.manifest = self.add("manifest", self.origin, "manifest", generated=True)

    def add(self, name, scope, role, content=None, supplied=None, generated=False):
        attempt = scope.get("native_attempt_id")
        value = supplied or ref(name, content, attempt)
        label = scope.get("case_label")
        path = (label + "/" if label else "") + name + ".json"
        self.bindings.append(dict(ref=value, scope=deepcopy(scope), role=role,
                                  provenance="adapter_generated" if generated else "producer",
                                  original_relative_path=None if generated else path,
                                  media_type="application/json", size_bytes=10))
        return value

    def finish(self):
        inventory = [dict(original_relative_path=x["original_relative_path"], sha256=x["ref"]["sha256"], size_bytes=x["size_bytes"]) for x in self.bindings if x["provenance"] == "producer"]
        inventory.append(dict(original_relative_path="omitted.log", sha256=digest("omitted"), size_bytes=9000000))
        return dict(origin=self.origin, selected=self.bindings, inventory=inventory, omitted_paths=["omitted.log"])


def identity(experiment="same"):
    return dict(source_hash=digest("source"), checker_hash=digest("checker"),
                contract_hash=digest("contract"), experiment_hash=digest(experiment), environment_hash=digest("environment"))


def riscv_report():
    build = Builder()
    cases = []
    attempts = ["attempt-ba01be2c331649828d66ffcca63365ba", "attempt-3507c622a1f143d894f819a0958c0f6a", "attempt-e845135395204d61b526d707bae1ef2f"]
    for label, attempt in zip(("control", "mutant", "control-replay"), attempts, strict=True):
        mutant = label == "mutant"
        locator = dict(kind="producer_attempt", origin=build.origin, case_label=label, native_attempt_id=attempt,
                       wire_experiment_id="rv32i-functional-register-mutant-v1" if mutant else "rv32i-control-v1",
                       raw_result_ref=ref(label + "-result", attempt=attempt))
        build.add(label + "-result", locator, "native_result", supplied=locator["raw_result_ref"])
        names = ("plan", "profile", "source_manifest", "checker_manifest", "environment", "raw_rvfi", "raw_spike", "normalized_pair", "comparison", "object", "elf", "binary")
        roles = dict(source_manifest="source_snapshot", raw_rvfi="native_trace", raw_spike="reference_trace", normalized_pair="native_trace", comparison="native_check", object="program", elf="program", binary="program")
        inputs = {name: build.add(label + "-" + name, locator, roles.get(name, "configuration"), content={"checker_manifest": "checker", "environment": "environment"}.get(name, ("mutant-" if mutant else "control-") + name)) for name in names}
        cases.append(dict(locator=locator, inputs=inputs, identity=identity("mutant" if mutant else "same"),
                          checker=dict(checker_id="rv32i", version="1", sha256=digest("checker")),
                          configuration_hash=digest("mutant" if mutant else "riscv-config"), producer_execution_status="completed",
                          termination="completion_store_retired", checker_evidence_status="valid", verdict="design_mismatch" if mutant else "pass_within_scope",
                          cycles=59 if mutant else 68, cycle_limit=200000, retirement_limit=4096,
                          retired_count=14 if mutant else 17, reference_count=17, compared_count=2 if mutant else 17,
                          first_divergence=dict(field="rd_value", order=1, cycle=14, expected=0x8000FEF0, observed=0x8000FEF1) if mutant else None,
                          producer_phases=["assembler", "compiler", "objcopy", "dut", "reference"]))
    comparison = dict(original=cases[0]["locator"], repeated=cases[2]["locator"], record_ref=build.add("recompiled", build.origin, "replay_record"), outcome="byte_identical_recompiled_control")
    return dict(manifest_ref=build.manifest, selection=build.finish(), cases=cases, recompiled_control=comparison)


def firmware_report():
    build = Builder(True)
    source_manifest = build.add("source-manifest", build.origin, "source_snapshot")
    tools = build.add("tool-metadata", build.origin, "configuration")
    cases = []
    for label in ("control", "bad", "candidate", "bad-replay", "candidate-replay"):
        base = label.removesuffix("-replay")
        replay = label.endswith("-replay")
        bad = base == "bad"
        fixture = {"control": "fw-regmap-control-v1", "bad": "fw-regmap-offset-bug-v1", "candidate": "fw-regmap-offset-candidate-v1"}[base]
        locator = dict(kind="producer_case", origin=build.origin, case_label=label, fixture_id=fixture, execution_ref=ref(label + "-execution"))
        build.add(label + "-execution", locator, "native_execution", supplied=locator["execution_ref"])
        names = ("preparation", "observation", "check", "register_contract", "selected_header", "bad_base_header")
        shared = {"profile": "profile", "register_contract": "contract", "checker": "checker", "bad_base_header": "bad-header", "selected_header": "bad-header" if bad else "correct-header"}
        roles = dict(observation="native_trace", check="native_check", selected_header="source_snapshot", bad_base_header="source_snapshot")
        inputs = {name: build.add(label + "-" + name, locator, roles.get(name, "configuration"), content=shared.get(name, base + "-" + name)) for name in names}
        inputs["program"] = {name: build.add(label + "-" + name, locator, "program", content=base + "-" + name) for name in ("start_object", "probe_object", "main_object", "elf", "binary", "map", "disassembly", "elf_attributes")}
        inputs["sources"] = {name: build.add(label + "-source-" + name, locator, "source_snapshot", content="source-" + name) for name in ("main", "start", "probe", "linker_script", "wrapper", "core_wrapper", "core", "peripheral")}
        inputs.update(source_manifest=source_manifest, tool_metadata=tools)
        cases.append(dict(locator=locator, inputs=inputs, identity=identity(base), checker=dict(checker_id="mmio", version="1", sha256=digest("checker")),
                          configuration_hash=digest("firmware-config-" + base), profile_hash=digest("profile"), semantic_hash=digest("semantic-" + base), producer_completed=True, termination="completion_store_retired",
                          checker_evidence_status="completed", verdict="design_mismatch" if bad else "pass_within_scope", cycles=166,
                          cycle_limit=200000, retired_count=80, retirement_limit=4096, transfer_count=100, mmio_count=4 if bad else 9,
                          first_conflict=dict(mmio_index=2, bus_sequence=31, cycle=95, operation="write", expected_address=0x40000004, observed_address=0x40000008, write_strobes=15, active_lane_value=0x55667788, expected_probe=0x55667788, observed_probe=0) if bad else None,
                          mailbox=dict(observed=True, value=2 if bad else 1, cycle=160),
                          producer_phases=["simulator"] if replay else ["assemble_start", "assemble_probe", "compile_main", "compiler", "binary", "disassembly", "elf_headers", "simulator"],
                          replay_of_execution_ref=next(x["locator"]["execution_ref"] for x in cases if x["locator"]["case_label"] == base) if replay else None))
    bad, candidate = cases[1:3]
    correction = dict(bad=bad["locator"], candidate=candidate["locator"], diff_ref=build.add("candidate-diff", candidate["locator"], "candidate_diff"),
                      original_bad_header_ref=bad["inputs"]["selected_header"], candidate_header_ref=candidate["inputs"]["selected_header"],
                      protected_checker_hash=digest("checker"), protected_contract_hash=digest("contract"), protected_profile_hash=digest("profile"),
                      covered_mmio_indices=list(range(9)), outcome="scoped_curated_correction")
    replays = [dict(original=cases[i]["locator"], repeated=cases[i + 2]["locator"], record_ref=build.add(cases[i]["locator"]["case_label"] + "-replay-record", build.origin, "replay_record"), outcome="semantic_saved_program_replay") for i in (1, 2)]
    return dict(manifest_ref=build.manifest, selection=build.finish(), cases=cases, candidate=correction, replays=replays)


@pytest.mark.parametrize("factory,model", [(riscv_report, RiscvRecordedReport), (firmware_report, FirmwareRecordedReport)])
def test_closed_report_round_trip_and_admission_scope(factory, model):
    report = model.model_validate(factory())
    assert TypeAdapter(RecordedToolReport).validate_json(report.model_dump_json()) == report
    assert report.new_execution == "not_run"
    assert report.operation == "admission_only"
    assert len(report.selection.selected) < 256
    with pytest.raises(ValidationError):
        report.new_execution = "completed"


def test_real_value_shapes_remain_distinct_from_new_execution():
    report = RiscvRecordedReport.model_validate(riscv_report())
    assert [(x.retired_count, x.cycles) for x in report.cases] == [(17, 68), (14, 59), (17, 68)]
    assert report.cases[1].first_divergence.expected == 0x8000FEF0
    firmware = FirmwareRecordedReport.model_validate(firmware_report())
    assert firmware.cases[1].first_conflict.bus_sequence == 31
    assert firmware.cases[1].first_conflict.mmio_index == 2
    assert all(x.locator.execution_ref.attempt_id is None for x in firmware.cases)
    assert firmware.replays[0].kind != report.recompiled_control.kind


@pytest.mark.parametrize("value", [True, 1.0, "1", 0, 2])
def test_origin_strict_attempt(value):
    data = origin()
    data["run_attempt"] = value
    with pytest.raises(ValidationError):
        HostedOrigin.model_validate(data)


@pytest.mark.parametrize("field,value", [("run_id", 40912735570), ("artifact_id", 12922737365), ("source_commit", "f" * 40), ("repository", "other/repo")])
def test_origin_is_exact_curated_tuple(field, value):
    data = origin()
    data[field] = value
    with pytest.raises(ValidationError):
        HostedOrigin.model_validate(data)


@pytest.mark.parametrize("path", ["../secret", "/root", "a//b", "a/./b", "a\\b", "C:/secret", "a/../b", "a\x00b"])
def test_selected_paths_cannot_escape(path):
    data = riscv_report()["selection"]
    data["selected"][0]["original_relative_path"] = path
    with pytest.raises(ValidationError):
        ArtifactSelection.model_validate(data)


@pytest.mark.parametrize("mutation", ["duplicate", "missing_omitted", "overlap", "hash", "size", "wrong_origin", "generated_path", "case_path", "attempt", "too_many", "too_large"])
def test_selection_rejects_invalid_inventory(mutation):
    data = riscv_report()["selection"]
    if mutation == "duplicate":
        data["selected"].append(data["selected"][0])
    elif mutation == "missing_omitted":
        data["omitted_paths"] = []
    elif mutation == "overlap":
        data["omitted_paths"].append(data["inventory"][0]["original_relative_path"])
    elif mutation in ("hash", "size"):
        data["inventory"][0]["sha256" if mutation == "hash" else "size_bytes"] = digest("swap") if mutation == "hash" else 11
    elif mutation == "wrong_origin":
        data["selected"][0]["scope"] = origin(True)
    elif mutation == "generated_path":
        data["selected"][1]["original_relative_path"] = "invented.json"
    elif mutation == "case_path":
        data["selected"][2]["original_relative_path"] = "mutant/stolen.json"
    elif mutation == "attempt":
        data["selected"][2]["ref"]["attempt_id"] = None
    elif mutation == "too_many":
        data["selected"] *= 7
    else:
        data["selected"][0]["size_bytes"] = 1048577
    with pytest.raises(ValidationError):
        ArtifactSelection.model_validate(data)


@pytest.mark.parametrize("mutation", ["input_swap", "missing_trace", "partial_trace", "count_bool", "wrong_native", "local_id", "wrong_prefix", "checker", "comparison_case", "recompiled_bytes", "fake_saved_replay", "invalid_checker", "unknown_schema"])
def test_riscv_rejects_invalid_or_hybrid_reports(mutation):
    data = riscv_report()
    case = data["cases"][1]
    if mutation == "input_swap":
        case["inputs"]["raw_rvfi"] = data["cases"][0]["inputs"]["raw_rvfi"]
    elif mutation == "missing_trace":
        case["inputs"].pop("raw_rvfi")
    elif mutation == "partial_trace":
        case["retired_count"] = 1
    elif mutation == "count_bool":
        case["cycles"] = True
    elif mutation == "wrong_native":
        case["locator"]["native_attempt_id"] = "attempt-invented"
    elif mutation == "local_id":
        case["db_attempt_id"] = "fabricated"
    elif mutation == "wrong_prefix":
        case["first_divergence"]["order"] = 3
    elif mutation == "checker":
        case["checker"]["sha256"] = digest("other")
    elif mutation == "comparison_case":
        data["recompiled_control"]["repeated"] = case["locator"]
    elif mutation == "recompiled_bytes":
        data["cases"][2]["identity"]["experiment_hash"] = digest("changed")
    elif mutation == "fake_saved_replay":
        data["recompiled_control"]["kind"] = "saved_program_replay"
    elif mutation == "invalid_checker":
        case["checker_evidence_status"] = "unsupported"
    else:
        data["schema_version"] = "system.bridge.v1"
    with pytest.raises(ValidationError):
        RiscvRecordedReport.model_validate(data)


@pytest.mark.parametrize("mutation", ["attempt", "fixture", "extra_local", "replay_compile", "replay_original", "program_changed", "semantic_changed", "checker_changed", "requirements_changed", "base_changed", "coverage", "candidate_pass", "mailbox", "conflict_index", "conflict_sequence", "conflict_cycle", "duplicate_replay"])
def test_firmware_rejects_invalid_comparisons(mutation):
    data = firmware_report()
    bad, candidate, repeated = data["cases"][1:4]
    if mutation == "attempt":
        bad["locator"]["execution_ref"]["attempt_id"] = "invented"
    elif mutation == "fixture":
        bad["locator"]["fixture_id"] = "fw-regmap-control-v1"
    elif mutation == "extra_local":
        bad["locator"]["native_attempt_id"] = "invented"
    elif mutation == "replay_compile":
        repeated["producer_phases"].append("compiler")
    elif mutation == "replay_original":
        repeated["replay_of_execution_ref"] = candidate["locator"]["execution_ref"]
    elif mutation == "program_changed":
        # Same scoped ref remains valid in selection; the replay equality gate rejects it.
        target = repeated["inputs"]["program"]["elf"]
        old = target["sha256"]
        target["sha256"] = digest("changed-elf")
        for binding in data["selection"]["selected"]:
            if binding["ref"]["artifact_id"] == target["artifact_id"]:
                binding["ref"]["sha256"] = target["sha256"]
                for entry in data["selection"]["inventory"]:
                    if entry["original_relative_path"] == binding["original_relative_path"]:
                        assert entry["sha256"] == old
                        entry["sha256"] = target["sha256"]
    elif mutation == "semantic_changed":
        repeated["semantic_hash"] = digest("changed")
    elif mutation == "checker_changed":
        data["candidate"]["protected_checker_hash"] = digest("changed")
    elif mutation == "requirements_changed":
        data["candidate"]["protected_contract_hash"] = digest("changed")
    elif mutation == "base_changed":
        data["candidate"]["original_bad_header_ref"] = candidate["inputs"]["selected_header"]
    elif mutation == "coverage":
        data["candidate"]["covered_mmio_indices"][-1] = 0
    elif mutation == "candidate_pass":
        candidate["verdict"] = "inconclusive"
    elif mutation == "mailbox":
        candidate["mailbox"]["value"] = 2
    elif mutation == "conflict_index":
        bad["first_conflict"]["mmio_index"] = 9
    elif mutation == "conflict_sequence":
        bad["first_conflict"]["bus_sequence"] = bad["transfer_count"]
    elif mutation == "conflict_cycle":
        bad["first_conflict"]["cycle"] = bad["cycles"] + 1
    else:
        data["replays"][1] = data["replays"][0]
    with pytest.raises(ValidationError):
        FirmwareRecordedReport.model_validate(data)


@pytest.mark.parametrize("model,data", [
    (IntegerDivergence, dict(field="rd", order=0, cycle=1, expected=31, observed=32)),
    (IntegerDivergence, dict(field="rd_value", order=0, cycle=1, expected="1", observed=2)),
    (IntegerDivergence, dict(field="rd_value", order=0, cycle=1, expected=1, observed=1)),
    (BooleanDivergence, dict(field="trap", order=0, cycle=1, expected=0, observed=True)),
    (MemoryDivergence, dict(field="memory", order=0, cycle=1, expected=None, observed=dict(kind="read", address=0, width=3, byte_mask=1, value=0))),
    (MailboxObservation, dict(observed=1, value=1, cycle=1)),
    (MailboxObservation, dict(observed=False, value=1)),
])
def test_typed_display_values_reject_coercion_and_invalid_shapes(model, data):
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_mmio_lane_mask_and_alignment():
    data = firmware_report()["cases"][1]["first_conflict"]
    data.update(write_strobes=2, active_lane_value=0x1100)
    assert MmioConflict.model_validate(data).active_lane_value == 0x1100
    for field, value in (("active_lane_value", 0x1101), ("observed_address", 0x40000009), ("write_strobes", True)):
        with pytest.raises(ValidationError):
            MmioConflict.model_validate({**data, field: value})


def test_firmware_locator_cannot_accept_local_or_native_attempt():
    data = firmware_report()["cases"][0]["locator"]
    assert ProducerCaseLocator.model_validate(data).execution_ref.attempt_id is None
    with pytest.raises(ValidationError):
        ProducerCaseLocator.model_validate({**data, "db_attempt_id": "fake"})


def test_unknown_union_schema_is_not_coerced():
    data = RiscvRecordedReport.model_validate(riscv_report()).model_dump(mode="json")
    data["schema_version"] = "riscv.recorded-report.v999"
    with pytest.raises(ValidationError):
        TypeAdapter(RecordedToolReport).validate_python(data)


def test_total_selected_bytes_limit_is_not_raised_for_native_tools():
    data = riscv_report()["selection"]
    for binding in data["selected"]:
        binding["size_bytes"] = 1048576
        for entry in data["inventory"]:
            if entry["original_relative_path"] == binding["original_relative_path"]:
                entry["size_bytes"] = 1048576
    with pytest.raises(ValidationError, match="selected byte limit"):
        ArtifactSelection.model_validate(data)


def test_report_blob_limit_also_bounds_omitted_inventory_metadata():
    data = riscv_report()
    for index in range(2500):
        path = str(index) + "/" + "a" * 490
        data["selection"]["omitted_paths"].append(path)
        data["selection"]["inventory"].append(dict(original_relative_path=path, sha256=digest(path), size_bytes=0))
    assert ArtifactSelection.model_validate(data["selection"])
    with pytest.raises(ValidationError, match="blob byte limit"):
        RiscvRecordedReport.model_validate(data)


def test_named_raw_roles_prevent_trace_reference_relabeling():
    data = riscv_report()
    target = data["cases"][0]["inputs"]["raw_spike"]
    for binding in data["selection"]["selected"]:
        if binding["ref"] == target:
            binding["role"] = "native_trace"
    with pytest.raises(ValidationError, match="wrongly scoped"):
        RiscvRecordedReport.model_validate(data)


def test_candidate_cannot_change_protected_wrapper_even_with_valid_raw_selection():
    data = firmware_report()
    target = data["cases"][2]["inputs"]["sources"]["wrapper"]
    target["sha256"] = digest("changed-wrapper")
    for binding in data["selection"]["selected"]:
        if binding["ref"]["artifact_id"] == target["artifact_id"]:
            binding["ref"]["sha256"] = target["sha256"]
            for entry in data["selection"]["inventory"]:
                if entry["original_relative_path"] == binding["original_relative_path"]:
                    entry["sha256"] = target["sha256"]
    with pytest.raises(ValidationError, match="protected source"):
        FirmwareRecordedReport.model_validate(data)


@pytest.mark.parametrize("kind", ["read", "write"])
def test_active_memory_effect_requires_mask_but_zero_data_is_valid(kind):
    effect = dict(kind=kind, address=0x80000000, width=4, byte_mask=15, value=0)
    assert MemoryEffect.model_validate(effect).value == 0
    for value in (0, True, "15", -1, 16):
        with pytest.raises(ValidationError):
            MemoryEffect.model_validate({**effect, "byte_mask": value})
    # Absence of a memory event remains null, rather than a zero-mask effect.
    divergence = MemoryDivergence.model_validate(dict(field="memory", order=0, cycle=1, expected=None, observed=effect))
    assert divergence.expected is None
    assert divergence.observed.byte_mask == 15


@pytest.mark.parametrize("binary", [bytes, bytearray])
@pytest.mark.parametrize("model,factory,field", [
    (HostedOrigin, origin, "source_commit"),
    (InventoryEntry, lambda: dict(original_relative_path="trace.json", sha256=digest("trace"), size_bytes=1), "original_relative_path"),
    (InventoryEntry, lambda: dict(original_relative_path="trace.json", sha256=digest("trace"), size_bytes=1), "sha256"),
    (TransportDigest, lambda: dict(sha256=digest("zip"), verification="github_reported"), "sha256"),
    (ProducerCaseLocator, lambda: firmware_report()["cases"][0]["locator"], "fixture_id"),
    (RecordedArtifactBinding, lambda: {**riscv_report()["selection"]["selected"][0], "original_artifact_id": "producer-original"}, "original_artifact_id"),
])
def test_leaf_text_paths_ids_and_hashes_reject_binary_coercion(binary, model, factory, field):
    data = factory()
    data[field] = binary(data[field].encode())
    with pytest.raises(ValidationError):
        model.model_validate(data)


@pytest.mark.parametrize("binary", [bytes, bytearray])
@pytest.mark.parametrize("field", ["artifact_id", "revision_id", "attempt_id", "sha256"])
def test_nested_shared_reference_fields_do_not_coerce_binary(binary, field):
    data = riscv_report()["cases"][0]
    target = data["locator"]["raw_result_ref"]
    target[field] = binary(target[field].encode())
    from veriforge.contracts.native_tools import RiscvRecordedAttempt
    with pytest.raises(ValidationError):
        RiscvRecordedAttempt.model_validate(data)


@pytest.mark.parametrize("section,field", [
    ("checker", "checker_id"), ("checker", "version"), ("checker", "sha256"),
    ("identity", "source_hash"), ("identity", "checker_hash"), ("identity", "contract_hash"),
    ("identity", "experiment_hash"), ("identity", "environment_hash"),
])
def test_nested_shared_checker_and_evidence_identity_fields_are_strict(section, field):
    data = firmware_report()["cases"][0]
    data[section][field] = data[section][field].encode()
    from veriforge.contracts.native_tools import FirmwareRecordedCase
    with pytest.raises(ValidationError):
        FirmwareRecordedCase.model_validate(data)


def test_existing_shared_models_and_json_arrays_remain_compatible():
    from veriforge.contracts import CheckerIdentity, EvidenceIdentity
    from veriforge.contracts.engineering import EvidenceReference
    from veriforge.contracts.native_tools import RiscvRecordedAttempt
    data = riscv_report()["cases"][0]
    data["checker"] = CheckerIdentity.model_validate(data["checker"])
    data["identity"] = EvidenceIdentity.model_validate(data["identity"])
    data["locator"]["raw_result_ref"] = EvidenceReference.model_validate(data["locator"]["raw_result_ref"])
    case = RiscvRecordedAttempt.model_validate(data)
    assert RiscvRecordedAttempt.model_validate_json(case.model_dump_json()) == case
