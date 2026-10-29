"""Independent authored contract fixtures, no blobs/tools/DB/execution evidence."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from veriforge.contracts import native_tools as n


def digest(label):
    return sha256(label.encode()).hexdigest()


def ref(label, *, attempt=None, content=None):
    return {"artifact_id": label, "revision_id": "independent-adapter-revision", "attempt_id": attempt,
            "sha256": digest(label if content is None else content)}


def origin(firmware=False):
    return {"kind": "github_actions", "repository": "desanv01/VerifForge",
        "source_commit": "5565985bbe2bb2a0be869d366baa48c715057657" if firmware else "44b8b5e40ce7841f076e4178df13f38ecdbd05fd",
        "run_id": 40929665294 if firmware else 40920450439, "run_attempt": 1,
        "artifact_id": 12922737365 if firmware else 12919420477, "extracted_receipt_ref": ref("receipt"),
        "transport_digest": {"sha256": digest("synthetic-github-reported"), "verification": "github_reported"}}


class Authored:
    def __init__(self, firmware=False):
        self.origin = origin(firmware)
        self.selected = []
        self.add("receipt", self.origin, "receipt", value=self.origin["extracted_receipt_ref"])
        self.manifest = self.add("manifest", self.origin, "manifest", generated=True)

    def add(self, name, scope, role="configuration", *, content=None, value=None, generated=False):
        value = value or ref(name, attempt=scope.get("native_attempt_id"), content=content)
        prefix = scope.get("case_label")
        self.selected.append({"ref": value, "scope": deepcopy(scope), "role": role,
            "provenance": "adapter_generated" if generated else "producer",
            "original_relative_path": None if generated else (prefix + "/" if prefix else "") + name + ".json",
            "media_type": "application/json", "size_bytes": 12})
        return value

    def selection(self):
        inventory = [{"original_relative_path": b["original_relative_path"], "sha256": b["ref"]["sha256"],
                      "size_bytes": b["size_bytes"]} for b in self.selected if b["provenance"] == "producer"]
        inventory.append({"original_relative_path": "unused-original.log", "sha256": digest("omitted"), "size_bytes": 9000000})
        return {"origin": self.origin, "selected": self.selected, "inventory": inventory,
                "omitted_paths": ["unused-original.log"]}


def identity(label):
    return {"source_hash": digest("source-" + label), "checker_hash": digest("checker"),
            "contract_hash": digest("contract"), "experiment_hash": digest("experiment-" + label),
            "environment_hash": digest("environment")}


def riscv():
    b = Authored()
    cases = []
    ids = ("attempt-ba01be2c331649828d66ffcca63365ba", "attempt-3507c622a1f143d894f819a0958c0f6a", "attempt-e845135395204d61b526d707bae1ef2f")
    for label, attempt in zip(("control", "mutant", "control-replay"), ids, strict=True):
        mutation = label == "mutant"
        stable = "mutant" if mutation else "control"
        locator = {"kind": "producer_attempt", "origin": b.origin, "case_label": label,
            "native_attempt_id": attempt, "wire_experiment_id": "rv32i-functional-register-mutant-v1" if mutation else "rv32i-control-v1",
            "raw_result_ref": ref(label + "-result", attempt=attempt)}
        b.add(label + "-result", locator, "native_result", value=locator["raw_result_ref"])
        roles = {"source_manifest": "source_snapshot", "raw_rvfi": "native_trace", "raw_spike": "reference_trace",
                 "normalized_pair": "native_trace", "comparison": "native_check", "object": "program", "elf": "program", "binary": "program"}
        inputs = {name: b.add(label + "-" + name, locator, roles.get(name, "configuration"), content={"checker_manifest": "checker", "environment": "environment"}.get(name, stable + "-" + name))
                  for name in n.RiscvInputs.model_fields}
        cases.append({"locator": locator, "inputs": inputs, "identity": identity(stable),
            "checker": {"checker_id": "independent-rv32", "version": "1", "sha256": digest("checker")},
            "configuration_hash": digest(stable + "-configuration"), "producer_execution_status": "completed",
            "termination": "completion_store_retired", "checker_evidence_status": "valid",
            "verdict": "design_mismatch" if mutation else "pass_within_scope", "cycles": 59 if mutation else 68,
            "cycle_limit": 200000, "retirement_limit": 4096, "retired_count": 14 if mutation else 17,
            "reference_count": 17, "compared_count": 2 if mutation else 17,
            "first_divergence": {"field": "rd_value", "order": 1, "cycle": 14, "expected": 0x8000FEF0, "observed": 0x8000FEF1} if mutation else None,
            "producer_phases": ["assembler", "compiler", "objcopy", "dut", "reference"]})
    comparison = {"original": cases[0]["locator"], "repeated": cases[2]["locator"],
        "record_ref": b.add("recompile-proof", b.origin, "replay_record"), "outcome": "byte_identical_recompiled_control"}
    return {"schema_version": "riscv.recorded-report.v1", "manifest_ref": b.manifest, "selection": b.selection(),
            "cases": cases, "recompiled_control": comparison}


def firmware():
    b = Authored(True)
    source_manifest = b.add("source-manifest", b.origin, "source_snapshot")
    tools = b.add("tools", b.origin)
    cases = []
    for label in ("control", "bad", "candidate", "bad-replay", "candidate-replay"):
        base = label.removesuffix("-replay")
        replay, bad = label.endswith("-replay"), base == "bad"
        locator = {"kind": "producer_case", "origin": b.origin, "case_label": label,
            "fixture_id": {"control": "fw-regmap-control-v1", "bad": "fw-regmap-offset-bug-v1", "candidate": "fw-regmap-offset-candidate-v1"}[base],
            "execution_ref": ref(label + "-execution")}
        b.add(label + "-execution", locator, "native_execution", value=locator["execution_ref"])
        roles = {"observation": "native_trace", "check": "native_check", "selected_header": "source_snapshot", "bad_base_header": "source_snapshot"}
        inputs = {name: b.add(label + "-" + name, locator, roles.get(name, "configuration"), content={"register_contract": "contract", "bad_base_header": "bad-header",
                  "selected_header": "bad-header" if bad else "correct-header"}.get(name, base + "-" + name))
                  for name in ("preparation", "observation", "check", "register_contract", "selected_header", "bad_base_header")}
        inputs["program"] = {name: b.add(label + "-program-" + name, locator, "program", content=base + "-program-" + name) for name in n.FirmwareProgram.model_fields}
        inputs["sources"] = {name: b.add(label + "-source-" + name, locator, "source_snapshot", content="protected-source-" + name) for name in n.FirmwareSources.model_fields}
        inputs.update(source_manifest=source_manifest, tool_metadata=tools)
        cases.append({"locator": locator, "inputs": inputs, "identity": identity(base),
            "checker": {"checker_id": "independent-mmio", "version": "1", "sha256": digest("checker")},
            "configuration_hash": digest(base + "-configuration"), "profile_hash": digest("profile"),
            "semantic_hash": digest(base + "-semantic"), "producer_completed": True, "termination": "completion_store_retired",
            "checker_evidence_status": "completed", "verdict": "design_mismatch" if bad else "pass_within_scope",
            "cycles": 166, "cycle_limit": 200000, "retired_count": 80, "retirement_limit": 4096,
            "transfer_count": 100, "mmio_count": 4 if bad else 9,
            "first_conflict": {"mmio_index": 2, "bus_sequence": 31, "cycle": 95, "operation": "write",
                "expected_address": 0x40000004, "observed_address": 0x40000008, "write_strobes": 15,
                "active_lane_value": 0x55667788, "expected_probe": 0x55667788, "observed_probe": 0} if bad else None,
            "mailbox": {"observed": True, "value": 2 if bad else 1, "cycle": 160},
            "producer_phases": ["simulator"] if replay else ["assemble_start", "assemble_probe", "compile_main", "compiler", "binary", "disassembly", "elf_headers", "simulator"],
            "replay_of_execution_ref": next(c["locator"]["execution_ref"] for c in cases if c["locator"]["case_label"] == base) if replay else None})
    bad, candidate = cases[1:3]
    report = {"schema_version": "firmware.recorded-report.v1", "manifest_ref": b.manifest, "cases": cases,
        "candidate": {"bad": bad["locator"], "candidate": candidate["locator"],
            "diff_ref": b.add("candidate-diff", candidate["locator"], "candidate_diff"),
            "original_bad_header_ref": bad["inputs"]["selected_header"], "candidate_header_ref": candidate["inputs"]["selected_header"],
            "protected_checker_hash": digest("checker"), "protected_contract_hash": digest("contract"),
            "protected_profile_hash": digest("profile"), "covered_mmio_indices": list(range(9)), "outcome": "scoped_curated_correction"},
        "replays": [{"original": cases[i]["locator"], "repeated": cases[i+2]["locator"],
            "record_ref": b.add("replay-" + str(i), b.origin, "replay_record"), "outcome": "semantic_saved_program_replay"} for i in (1, 2)]}
    report["selection"] = b.selection()
    return report


@pytest.mark.parametrize("factory", [riscv, firmware])
def test_independent_report_roundtrip_and_no_local_execution_authority(factory):
    report = TypeAdapter(n.RecordedToolReport).validate_python(factory())
    assert TypeAdapter(n.RecordedToolReport).validate_json(report.model_dump_json()) == report
    assert report.operation == "admission_only" and report.new_execution == "not_run"
    for key in ("campaign_id", "db_attempt_id", "lease", "command"):
        changed = factory()
        changed[key] = "forbidden"
        with pytest.raises(ValidationError):
            TypeAdapter(n.RecordedToolReport).validate_python(changed)


@pytest.mark.parametrize("factory", [riscv, firmware])
@pytest.mark.parametrize("damage", ["missing", "swapped_case", "wrong_raw_hash", "inventory_omission", "duplicate_identity", "overlap"])
def test_exact_reference_case_inventory_closure(factory, damage):
    report = factory()
    selected = report["selection"]["selected"]
    if damage == "missing":
        report["cases"][0]["inputs"].pop(next(iter(report["cases"][0]["inputs"])))
    elif damage == "swapped_case":
        field = "plan" if factory is riscv else "observation"
        report["cases"][1]["inputs"][field] = report["cases"][0]["inputs"][field]
    elif damage == "wrong_raw_hash":
        report["cases"][0]["inputs"]["plan" if factory is riscv else "observation"]["sha256"] = digest("different-bytes")
    elif damage == "inventory_omission":
        report["selection"]["omitted_paths"] = []
    elif damage == "duplicate_identity":
        selected.append(deepcopy(selected[0]))
    else:
        report["selection"]["omitted_paths"].append(selected[0]["original_relative_path"])
    with pytest.raises(ValidationError):
        TypeAdapter(n.RecordedToolReport).validate_python(report)


@pytest.mark.parametrize("factory", [riscv, firmware])
def test_external_origin_native_id_absence_and_unknown_schema(factory):
    data = factory()
    case = data["cases"][0]
    if factory is riscv:
        case["locator"]["native_attempt_id"] = "local-db-attempt"
    else:
        case["locator"]["native_attempt_id"] = "invented-firmware-attempt"
    with pytest.raises(ValidationError):
        TypeAdapter(n.RecordedToolReport).validate_python(data)
    data = factory()
    data["schema_version"] = "system.bridge.v1"
    with pytest.raises(ValidationError):
        TypeAdapter(n.RecordedToolReport).validate_python(data)


@pytest.mark.parametrize("model,values", [(n.MemoryEffect, {"kind": "write", "address": 0x80000000, "width": 4, "byte_mask": 0, "value": 1})])
def test_memory_effect_requires_an_active_lane(model, values):
    with pytest.raises(ValidationError):
        model.model_validate(values)


@pytest.mark.parametrize("model,values,field", [
    (n.HostedOrigin, origin(), "source_commit"),
    (n.InventoryEntry, {"original_relative_path": "trace.json", "sha256": digest("trace"), "size_bytes": 1}, "original_relative_path"),
    (n.TransportDigest, {"sha256": digest("zip"), "verification": "github_reported"}, "sha256"),
])
def test_new_leaf_string_primitives_are_not_coerced_from_python_bytes(model, values, field):
    values = deepcopy(values)
    values[field] = values[field].encode()
    with pytest.raises(ValidationError):
        model.model_validate(values)


def test_nested_v1_reference_primitives_cannot_bypass_new_leaf_strictness():
    data = firmware()["selection"]
    data["selected"][0]["ref"]["sha256"] = data["selected"][0]["ref"]["sha256"].encode()
    with pytest.raises(ValidationError):
        n.ArtifactSelection.model_validate(data)


@pytest.mark.parametrize("field,value", [("run_id", True), ("run_attempt", "1"), ("artifact_id", 12922737365),
    ("repository", "different/repository"), ("source_commit", "f" * 40)])
def test_hosted_locator_requires_exact_curated_origin_and_strict_numbers(field, value):
    data = origin()
    data[field] = value
    with pytest.raises(ValidationError):
        n.HostedOrigin.model_validate(data)


@pytest.mark.parametrize("field,value", [("cycles", True), ("retired_count", "17"), ("producer_completed", 1)])
def test_numeric_and_boolean_fields_are_truly_strict(field, value):
    data = firmware()["cases"][0]
    data[field] = value
    with pytest.raises(ValidationError):
        n.FirmwareRecordedCase.model_validate(data)


def test_raw_and_semantic_identity_remain_distinct_and_case_input_is_required():
    data = firmware()
    assert data["cases"][1]["semantic_hash"] != data["cases"][1]["inputs"]["observation"]["sha256"]
    assert n.FirmwareRecordedReport.model_validate(data).cases[1].semantic_hash == digest("bad-semantic")
    data["cases"][1]["inputs"]["observation"]["sha256"] = data["cases"][1]["semantic_hash"]
    with pytest.raises(ValidationError):
        n.FirmwareRecordedReport.model_validate(data)


@pytest.mark.parametrize("over", ["count", "per_blob", "total"])
def test_selected_quotas_are_independent_of_omitted_original_size(over):
    data = firmware()["selection"]
    if over == "per_blob":
        data["selected"][1]["size_bytes"] = 1048577
    else:
        amount = 257 if over == "count" else 17
        data["selected"] = data["selected"][:1] + [dict(ref=ref("adapter-" + str(i)), scope=data["origin"],
            provenance="adapter_generated", role="manifest", media_type="application/json",
            size_bytes=1 if over == "count" else 1048576) for i in range(amount)]
        data["omitted_paths"] = [entry["original_relative_path"] for entry in data["inventory"][1:]]
    with pytest.raises(ValidationError):
        n.ArtifactSelection.model_validate(data)


@pytest.mark.parametrize("path", [("identity", "checker_hash"), ("identity", "contract_hash"), ("profile_hash",)])
def test_candidate_cannot_change_protected_hashes(path):
    data = firmware()
    node = data["cases"][2]
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = digest("changed-protected")
    with pytest.raises(ValidationError):
        n.FirmwareRecordedReport.model_validate(data)


@pytest.mark.parametrize("group", ["program", "sources"])
def test_saved_program_replay_protects_every_retained_input_hash(group):
    for name in getattr(n, "FirmwareProgram" if group == "program" else "FirmwareSources").model_fields:
        data = firmware()
        data["cases"][3]["inputs"][group][name]["sha256"] = digest("changed-" + name)
        # Keep selection/inventory consistent with the changed replay blob so
        # rejection must include the saved-program equality invariant.
        updated = data["cases"][3]["inputs"][group][name]
        for binding in data["selection"]["selected"]:
            if binding["ref"]["artifact_id"] == updated["artifact_id"]:
                binding["ref"]["sha256"] = updated["sha256"]
                for entry in data["selection"]["inventory"]:
                    if entry["original_relative_path"] == binding["original_relative_path"]:
                        entry["sha256"] = updated["sha256"]
        with pytest.raises(ValidationError):
            n.FirmwareRecordedReport.model_validate(data)
