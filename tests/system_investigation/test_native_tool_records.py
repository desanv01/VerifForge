"""Pure admission tests. Synthetic traces never represent tool execution.

Optional accepted bundles are read as inert bytes when present, never executed.
All filesystem reads/preparation below belong to the trusted test host, outside
the pure admission module. No DB, compiler, simulator or network is used.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import struct

import pytest

from veriforge.contracts import AttemptResult, FailureSignature, canonical_hash
from veriforge.contracts.engineering import EvidenceReference, SystemManifest
from veriforge.contracts.native_tools import (
    ArtifactSelection, FirmwareInputs, FirmwareProgram, FirmwareSources, FirmwareRecordedReport,
    HostedOrigin, ProducerAttemptLocator, ProducerCaseLocator, RecordedArtifactBinding,
    RiscvInputs, RiscvRecordedReport,
)
from veriforge.riscv.contracts import RiscvExperiment, SimulationOutput
from veriforge.riscv.profile import PLATFORM, CORE_REVISION, REFERENCE_REVISION, ASSEMBLER_FLAGS, COMPILER_FLAGS
from veriforge.riscv.trace import normalize_rvfi, parse_spike_commits
from veriforge.riscv.checker import compare_retirements
from veriforge.system_investigation import records as v1
from veriforge.system_investigation import native_tool_records as native

ROOT = Path(__file__).resolve().parents[2]
RV_IDS = {
    "control": ("attempt-ba01be2c331649828d66ffcca63365ba", "rv32i-control-v1"),
    "mutant": ("attempt-3507c622a1f143d894f819a0958c0f6a", "rv32i-functional-register-mutant-v1"),
    "control-replay": ("attempt-e845135395204d61b526d707bae1ef2f", "rv32i-control-v1"),
}
FW_IDS = {"control": "fw-regmap-control-v1", "bad": "fw-regmap-offset-bug-v1", "candidate": "fw-regmap-offset-candidate-v1",
    "bad-replay": "fw-regmap-offset-bug-v1", "candidate-replay": "fw-regmap-offset-candidate-v1"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value, *, pretty=False):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=None if pretty else (",", ":"), indent=2 if pretty else None).encode()


def fake_elf(instructions):
    code = b"".join(value.to_bytes(4, "little") for value in instructions)
    shoff = 256 + len(code)
    header = struct.pack("<16sHHIIIIIHHHHHH", b"\x7fELF\x01\x01\x01" + b"\0" * 9,
        2, 243, 1, 0x80000000, 52, shoff, 0, 52, 32, 1, 40, 2, 0)
    program = struct.pack("<IIIIIIII", 1, 256, 0x80000000, 0x80000000, len(code), len(code), 5, 4)
    section = struct.pack("<IIIIIIIIII", 0, 1, 6, 0x80000000, 256, len(code), 0, 0, 4, 0)
    return header + program + b"\0" * (256-len(header)-len(program)) + code + b"\0" * 40 + section, code


def rv_event(order, instruction, *, value=0, address=0, mask=0, data=0):
    return dict(rvfi_order=order, cycle=6+order, rvfi_insn=instruction, rvfi_pc_rdata=0x80000000+4*order,
        rvfi_rd_addr=1 if order == 0 else 0, rvfi_rd_wdata=value, rvfi_rs1_rdata=address,
        rvfi_mem_addr=address, rvfi_mem_rmask=0, rvfi_mem_wmask=mask, rvfi_mem_rdata=0, rvfi_mem_wdata=data,
        rvfi_trap=False, rvfi_intr=False)


def synthetic_riscv():
    source_names = ["src/veriforge/riscv/"+name for name in ("checker.py", "trace.py", "contracts.py", "profile.py", "memory.py", "sim_test.py", "sim_job.py")]
    source_names += ["targets/riscv/"+name for name in ("upstream/picorv32.v", "picorv32_top.sv", "smoke.S", "link.ld")]
    raw = {"source/"+name: (ROOT/name).read_bytes() for name in source_names}
    source = {name: sha(raw["source/"+name]) for name in source_names}
    raw["source-manifest.json"] = encoded(source, pretty=True)
    elf, binary = fake_elf((0x00100093, 0x00112023))
    reference_raw = b"core 0: 3 0x80000000 (0x00100093) x1 0x00000001\ncore 0: 3 0x80000004 (0x00112023) mem 0x8000ff00 0x00000001\n"
    summaries = {}
    for label, (attempt_id, experiment_id) in RV_IDS.items():
        plan = RiscvExperiment(experiment_id=experiment_id, fixture_id="picorv32_reg_write_bug" if label == "mutant" else "picorv32_rv32i")
        native_source = {name: source["targets/riscv/"+name] for name in ("upstream/picorv32.v", "picorv32_top.sv", "smoke.S", "link.ld")}
        native_source.update(core_revision=CORE_REVISION, fixture_id=plan.fixture_id, profile_code_hash=source["src/veriforge/riscv/profile.py"])
        checker = {name: source["src/veriforge/riscv/"+name] for name in ("checker.py", "trace.py", "contracts.py")}
        environment = dict(reference_revision=REFERENCE_REVISION, elf_hash=sha(elf), binary_hash=sha(binary), object_hash=sha(b"synthetic-object"),
            driver_hash=source["src/veriforge/riscv/sim_test.py"], memory_hash=source["src/veriforge/riscv/memory.py"], job_hash=source["src/veriforge/riscv/sim_job.py"])
        environment.update(tools=[dict(name=name,version=REFERENCE_REVISION if name=="spike-revision" else "synthetic-tool-version") for name in ("verilator","riscv-gcc","spike-revision","cocotb","python")],
            reference_submodules="Synthetic fixture metadata; no reference binary executed",spike_binary_hash="7020158c1a7b39119a6b1a08b6f43bc6097f95db883ea53a79cf98b4994765e7",image_package_hash="e43d1b9d3074e5c9b6a7718a68b81096fef1559b2ad100b6bb58bb97e21de994",
            assembly_command=["riscv64-unknown-elf-gcc",*ASSEMBLER_FLAGS,"-c","/workspace/targets/riscv/smoke.S","-o","/output/smoke.o"],
            compiler_command=["riscv64-unknown-elf-gcc",*COMPILER_FLAGS,"-T","/workspace/targets/riscv/link.ld","/output/smoke.o","-o","/output/smoke.elf"],
            reference_command=["spike","--isa=RV32I","--priv=m","--pcs=0:0x80000000","-m0x80000000:0x10000","--log-commits","--log=/output/reference/commit.log","--instructions="+str(plan.max_retired),"/output/smoke.elf"])
        observed_raw = SimulationOutput(termination="completion_store_retired", cycles=8, events=[
            rv_event(0, 0x00100093, value=2 if label == "mutant" else 1), rv_event(1, 0x00112023, address=0x8000FF00, mask=15, data=1)])
        reference, observed = parse_spike_commits(reference_raw.decode()), normalize_rvfi(observed_raw.events)
        comparison = compare_retirements(reference, observed)
        files = {"experiment.json": encoded(plan, pretty=True), "profile.json": encoded(asdict(PLATFORM)),
            "source-manifest.json": encoded(native_source), "checker-manifest.json": encoded(checker), "environment.json": encoded(environment),
            "smoke.o": b"synthetic-object", "smoke.elf": elf, "smoke.bin": binary, "reference/commit.log": reference_raw,
            "dut/rvfi.json": encoded(observed_raw, pretty=True), "retirement.json": encoded(dict(schema_version="riscv.retirement-pair.v1",
                reference=[x.model_dump(mode="json") for x in reference], observed=[x.model_dump(mode="json") for x in observed])),
            "comparison.json": encoded(comparison, pretty=True)}
        files.update({phase+"/execution.log": b"Synthetic phase marker, no tool ran" for phase in ("assembler", "compiler", "objcopy", "reference", "dut")})
        identity = dict(source_hash=sha(encoded(native_source)), checker_hash=sha(encoded(checker)), contract_hash=sha(encoded(asdict(PLATFORM))),
            experiment_hash=canonical_hash(plan), environment_hash=sha(encoded(environment)))
        signature = None
        if comparison.first_divergence:
            first = comparison.first_divergence
            signature = FailureSignature(requirement_id=first.requirement_id, failure_class="riscv."+first.field, first_cycle=6,
                expected=first.expected, observed=first.observed,
                signature_hash=sha(encoded(dict(checker=identity["checker_hash"], contract=identity["contract_hash"], field=first.field))))
        result = AttemptResult(attempt_id=attempt_id, experiment_id=experiment_id, execution_status="completed", verdict=comparison.verdict,
            identity=identity, failure_signature=signature, usage={"cycles": 8}, uncertainties=("Synthetic unit fixture; no tool ran",),
            tool_versions=environment["tools"],
            artifacts=tuple(dict(artifact_id="artifact-"+str(index), path=name, sha256=sha(data), size_bytes=len(data), media_type="application/json" if name.endswith(".json") else "application/octet-stream")
                for index, (name, data) in enumerate(sorted(files.items()))))
        files["result.json"] = encoded(result, pretty=True)
        raw.update({label+"/"+name: data for name,data in files.items()})
        summaries[label] = dict(binary_hash=sha(binary), compared=comparison.compared, cycles=8, elf_hash=sha(elf), first_divergence=comparison.first_divergence.model_dump(mode="json") if comparison.first_divergence else None,
            identity=identity, object_hash=sha(b"synthetic-object"), pair_hash=sha(files["retirement.json"]), reference_hash=sha(encoded([x.model_dump(mode="json") for x in reference])), verdict=comparison.verdict)
    raw["summary.json"] = encoded(dict(schema_version="riscv.ci.v1", accepted=True, builder_settled=True, commit="44b8b5e40ce7841f076e4178df13f38ecdbd05fd",
        run_id="40920450439", run_attempt="1", image_id="sha256:"+"a"*64, wall_seconds=1.0, attempts=summaries), pretty=True)
    raw["receipt-sha256.json"] = encoded({name:sha(data) for name,data in raw.items()}, pretty=True)
    return raw


def synthetic_firmware(tmp_path):
    from veriforge.firmware.executable import FirmwarePreparation, prepare_executable_case
    from veriforge.firmware.harness import EXPECTED, HarnessOutput, check_observation
    names = ["src/veriforge/firmware/"+name for name in ("__init__.py","contracts.py","executable.py","extract.py","workflow.py","harness.py","sim_test.py","sim_job.py")]
    names += ["scripts/ci_firmware_acceptance.py"]
    names += ["src/veriforge/riscv/"+name for name in ("contracts.py","memory.py","profile.py","trace.py","checker.py")]
    names += ["targets/riscv/upstream/picorv32.v","targets/riscv/picorv32_top.sv"]
    names += ["targets/firmware/executable/"+name for name in ("main.c","start.S","independent_probe.S","link.ld","firmware_top.sv","native_registers.sv","register-contract.json","control/registers.h","candidate/registers.h","offset_bug/registers.h")]
    raw = {"source/"+name:(ROOT/name).read_bytes() for name in names}
    source = {name:sha(raw["source/"+name]) for name in names}
    raw["source-manifest.json"] = encoded(source,pretty=True)
    tools = dict(image_id="sha256:"+"a"*64, tools=[], reference_support="Spike MMIO unsupported")
    raw["tool-metadata.json"] = encoded(tools,pretty=True)
    original_files, summaries = {}, {}
    for label, fixture_id in FW_IDS.items():
        base = label.removesuffix("-replay")
        replay = label.endswith("-replay")
        plan = FirmwarePreparation(fixture_id=fixture_id)
        bad = base == "bad"
        if not replay:
            # Trusted installed prepare-only helper writes synthetic inputs to
            # pytest temp storage; no compiler/process/tool is invoked.
            directory = tmp_path/label
            prep = prepare_executable_case(plan,directory)
            recipes = native._firmware_recipes()
            prep["recipes"] = {k:v for k,v in recipes.items() if k != "simulator"}
            files = {p.name:p.read_bytes() for p in directory.iterdir() if p.is_file()}
            files["preparation.json"] = encoded(prep,pretty=True)
            mmio = ((0x40000000,15,0x11223344),(0x40000000,0,0x11223344),(0x40000008,15,0x55667788),(0x40000004,0,0)) if bad else EXPECTED
            transactions = (*mmio,(0x8000FF00,15,2 if bad else 1))
            instructions = [0x00112023 if mask else 0x00012083 for address,mask,value in transactions]
            elf,binary = fake_elf(instructions)
            events,transfers = [],[]
            for order,(address,mask,value) in enumerate(transactions):
                events.append(dict(rvfi_order=order,cycle=7+2*order,rvfi_insn=instructions[order],rvfi_pc_rdata=0x80000000+4*order,
                    rvfi_rd_addr=0 if mask else 1,rvfi_rd_wdata=0 if mask else value,rvfi_rs1_rdata=address,
                    rvfi_mem_addr=address,rvfi_mem_rmask=0 if mask else 15,rvfi_mem_wmask=mask,rvfi_mem_rdata=0 if mask else value,rvfi_mem_wdata=value if mask else 0))
                transfers.append(dict(sequence=order,cycle=6+2*order,address=address,instruction=False,write_strobes=mask,write_data=value if mask else 0,read_data=0 if mask else value))
            observation = HarnessOutput(termination="completion_store_retired",cycles=events[-1]["cycle"]+1,events=events,transfers=transfers)
            files.update({"start.o":b"synthetic start object","independent_probe.o":b"synthetic probe object","main.o":b"synthetic main object",
                "firmware.elf":elf,"firmware.bin":binary,"firmware.map":b"synthetic map, no linker ran",
                "disassembly.txt":"".join(f"{0x80000000+4*i:08x}: {word:08x} synthetic\n" for i,word in enumerate(instructions)).encode(),
                "elf_headers.txt":b'Tag_RISCV_arch: "rv32i2p1"\n',"observation.json":encoded(observation,pretty=True)})
            files["execution-identities.json"] = encoded(dict(elf_sha256=sha(elf),binary_sha256=sha(binary),preparation_sha256=sha(files["preparation.json"])))
            files["simulation-job.json"] = encoded(dict(preparation=plan.model_dump(mode="json"),binary="/tmp/firmware-snapshot/firmware.bin",trace="/tmp/firmware-snapshot/observation.json"))
            original_files[base] = dict(files)
        else:
            files = dict(original_files[base])
            prep = json.loads(files["preparation.json"])
            observation = HarnessOutput.model_validate(json.loads(files["observation.json"]))
        recipes = native._firmware_recipes()
        phases = ("simulator",) if replay else tuple(recipes)
        execution = dict(schema_version="firmware.ci-execution.v1",completed=True,fixture_id=fixture_id,replay=replay,wall_seconds=1.0,
            commands=[dict(argv=recipes[phase],phase=phase,returncode=0,wall_seconds=0.1) for phase in phases],files={name:sha(data) for name,data in files.items()})
        if replay:
            execution["replay_original_hash"] = sha(raw[base+"/execution.json"])
        files["execution.json"] = encoded(execution,pretty=True)
        checker = check_observation(observation,plan)
        first,signature = None,None
        subset = {key:value for key,value in source.items() if key.startswith("src/veriforge/firmware/") or key=="scripts/ci_firmware_acceptance.py"}
        identity = dict(source_hash=sha(encoded(prep)),checker_hash=sha(encoded(subset)),contract_hash=sha(encoded([prep["contract_hash"],prep["profile_hash"]])),experiment_hash=canonical_hash(plan),environment_hash=sha(encoded(tools)))
        if bad:
            bus = observation.transfers[2]
            first = dict(requirement_id="FIRMWARE_STATUS_ADDRESS",mmio_sequence=2,bus_sequence=bus.sequence,cycle=bus.cycle,
                expected_address=0x40000004,observed_address=0x40000008,expected_probe=0x55667788,observed_probe=0)
            signature = FailureSignature(requirement_id=first["requirement_id"],failure_class="firmware.status_offset",first_cycle=bus.cycle,
                expected=json.dumps(dict(address=0x40000004,probe=0x55667788),sort_keys=True),observed=json.dumps(dict(address=0x40000008,probe=0),sort_keys=True),
                signature_hash=sha(encoded(dict(checker=identity["checker_hash"],contract=identity["contract_hash"],requirement=first["requirement_id"])))).model_dump(mode="json")
        summaries[label] = dict(identity=identity,program_hashes={name:sha(files[name]) for name in native._FW_PROGRAM.values()},observation_sha256=sha(files["observation.json"]),semantic_hash=sha(encoded(observation)),
            check=checker.model_dump(mode="json"),first_conflict=first,failure_signature=signature,cycles=observation.cycles,selected_header_hash=prep["selected_header_hash"],original_bad_hash=prep["original_bad_hash"],
            candidate_diff_sha256=sha(files["candidate.diff"]) if "candidate.diff" in files else None,replay_original_hash=execution.get("replay_original_hash"),execution_sha256=sha(files["execution.json"]),reference_support="Spike MMIO unsupported; independent literal MMIO checker only")
        raw.update({label+"/"+name:data for name,data in files.items()})
        raw[label+".check.json"] = encoded(summaries[label],pretty=True)
    raw["summary.json"] = encoded(dict(schema_version="firmware.ci.v1",accepted=True,builder_settled=True,commit="5565985bbe2bb2a0be869d366baa48c715057657",run_id="40929665294",run_attempt="1",
        image_id=tools["image_id"],wall_seconds=1.0,reference_support="Spike MMIO unsupported",cases=summaries),pretty=True)
    raw["receipt-sha256.json"] = encoded({name:sha(data) for name,data in raw.items()},pretty=True)
    return raw


def bundle(raw, kind="riscv"):
    """Trusted test-host adapter, never an importer entry point."""
    raw = dict(raw)
    commit = "44b8b5e40ce7841f076e4178df13f38ecdbd05fd" if kind == "riscv" else "5565985bbe2bb2a0be869d366baa48c715057657"
    def ref(name):
        label = name.split("/", 1)[0]
        return EvidenceReference(artifact_id="raw-"+sha(name.encode())[:24], sha256=sha(raw[name]), revision_id=commit,
            attempt_id=RV_IDS[label][0] if kind == "riscv" and label in RV_IDS else None)
    origin = HostedOrigin(source_commit=commit, run_id=40920450439 if kind == "riscv" else 40929665294,
        artifact_id=12919420477 if kind == "riscv" else 12922737365, extracted_receipt_ref=ref("receipt-sha256.json"))
    labels = tuple(RV_IDS if kind == "riscv" else FW_IDS)
    locators = {label: ProducerAttemptLocator(origin=origin, case_label=label, native_attempt_id=RV_IDS[label][0], wire_experiment_id=RV_IDS[label][1], raw_result_ref=ref(label+"/result.json")) if kind == "riscv"
        else ProducerCaseLocator(origin=origin, case_label=label, fixture_id=FW_IDS[label], execution_ref=ref(label+"/execution.json")) for label in labels}
    by_ref = {v1._key(ref(name)): name for name in raw}
    needed, role_map = set(), {}
    original_ids = {label+"/"+item["path"]:item["artifact_id"] for label in labels for item in json.loads(raw[label+"/result.json"])["artifacts"]} if kind == "riscv" else {}
    def path(name, digest=None, *, size=None, role=None, original_id=None):
        assert name in raw, name
        assert digest is None or sha(raw[name]) == digest
        assert size is None or len(raw[name]) == size
        assert original_id is None or original_ids[name] == original_id
        needed.add(name)
        if role:
            role_map[name] = role
        return raw[name]
    def resolve(reference, role):
        name = by_ref[v1._key(reference)]
        assert ref(name) == reference
        role_map[name] = role
        return path(name)
    source = native._source_closure(path)
    cases = []
    for label in labels:
        if kind == "riscv":
            names = {"plan":"experiment.json", "profile":"profile.json", "source_manifest":"source-manifest.json", "checker_manifest":"checker-manifest.json", "environment":"environment.json",
                "raw_rvfi":"dut/rvfi.json", "raw_spike":"reference/commit.log", "normalized_pair":"retirement.json", "comparison":"comparison.json", "object":"smoke.o", "elf":"smoke.elf", "binary":"smoke.bin"}
            inputs = RiscvInputs(**{field:ref(label+"/"+name) for field,name in names.items()})
            cases.append(native._riscv_case(locators[label], inputs, resolve, path, source))
        else:
            inputs = FirmwareInputs(preparation=ref(label+"/preparation.json"), observation=ref(label+"/observation.json"), check=ref(label+".check.json"), register_contract=ref(label+"/register-contract.json"),
                selected_header=ref(label+"/registers.h"), bad_base_header=ref(label+"/original-bad-registers.h"), source_manifest=ref("source-manifest.json"), tool_metadata=ref("tool-metadata.json"),
                program=FirmwareProgram(**{field:ref(label+"/"+name) for field,name in native._FW_PROGRAM.items()}), sources=FirmwareSources(**{field:ref(label+"/"+name) for field,name in native._FW_SOURCES.items()}))
            cases.append(native._firmware_case(locators[label], inputs, resolve, path, source,
                original_ref=locators[label.removesuffix("-replay")].execution_ref if label.endswith("-replay") else None))
    needed.update(("receipt-sha256.json", "summary.json"))
    if kind == "firmware":
        needed.add("candidate/candidate.diff")
    validator_hash = sha(Path(native.__file__).read_bytes())
    revision_id = "native-v2-revision"
    metadata = {}
    def derived(name, data):
        reference = EvidenceReference(artifact_id=name, sha256=sha(data), revision_id=revision_id)
        metadata[v1._key(reference)] = v1.ArtifactBytes(reference=reference, data=data)
        return reference
    attachments, facts, sources = [], [], [ref("source-manifest.json")]
    for case in cases:
        reference = derived("case-"+case.locator.case_label, v1._encode(case))
        primary = case.locator.raw_result_ref if kind == "riscv" else case.inputs.check
        sources.append(primary)
        attachments.append(dict(fact_id="fact-"+case.locator.case_label, domain=kind, native_schema_id=case.schema_version, reference=reference))
        facts.append(dict(fact_id="fact-"+case.locator.case_label, domain=kind, native_schema_id=case.schema_version, native_record_hash=reference.sha256, native_record=reference,
            category="observation", provenance=dict(state="complete", sources=[dict(artifact=primary, location=dict(kind="json_pointer",pointer=""))],
                extraction=dict(method="native_parser", extractor_id=native.VALIDATOR_ID, extractor_version=native.VALIDATOR_VERSION, implementation_hash=validator_hash))))
    manifest = SystemManifest(manifest_id="native-observation-manifest", revision_id=revision_id, facts=facts)
    manifest_ref = derived("native-manifest", v1._encode(manifest))
    revision = v1.EngineeringProjectRevision(project_id="recorded-project", revision_id=revision_id, source_refs=tuple(sources), configuration_hash=ref("source-manifest.json").sha256)
    revision_ref = derived("project-revision", v1._encode(revision))
    def role(name):
        base = name.split("/",1)[-1]
        if name == "receipt-sha256.json": return "receipt"
        if name == "summary.json": return "replay_record"
        if name.startswith("source/") or name == "source-manifest.json": return "source_snapshot"
        if name.endswith("/result.json"): return "native_result"
        if name.endswith("/execution.json"): return "native_execution"
        if base == "comparison.json" or name.endswith(".check.json"): return "native_check"
        if base in ("dut/rvfi.json", "retirement.json", "observation.json"): return "native_trace"
        if base == "reference/commit.log": return "reference_trace"
        if base == "candidate.diff": return "candidate_diff"
        if base in ("smoke.o", "smoke.elf", "smoke.bin", *native._FW_PROGRAM.values()): return "program"
        if base == "source-manifest.json" or base in (*native._FW_SOURCES.values(), "registers.h", "original-bad-registers.h"): return "source_snapshot"
        return "configuration"
    bindings = []
    for name in sorted(needed):
        label = name.split("/",1)[0]
        if kind == "firmware" and name.endswith(".check.json"):
            label = name.removesuffix(".check.json")
        bindings.append(RecordedArtifactBinding(ref=ref(name), scope=locators.get(label, origin), original_relative_path=name,
            original_artifact_id=original_ids.get(name), role=role(name), media_type="application/json" if name.endswith(".json") else "application/octet-stream", size_bytes=len(raw[name])))
    bindings.append(RecordedArtifactBinding(ref=manifest_ref, scope=origin, provenance="adapter_generated", role="manifest", media_type="application/json", size_bytes=len(metadata[v1._key(manifest_ref)].data)))
    selection = ArtifactSelection(origin=origin, inventory=tuple(dict(original_relative_path=name,sha256=sha(data),size_bytes=len(data)) for name,data in sorted(raw.items())), selected=tuple(bindings), omitted_paths=tuple(sorted(set(raw)-needed)))
    if kind == "riscv":
        report = RiscvRecordedReport(manifest_ref=manifest_ref, selection=selection, cases=cases,
            recompiled_control=dict(original=locators["control"], repeated=locators["control-replay"], record_ref=ref("summary.json"), outcome="byte_identical_recompiled_control"))
    else:
        bad, fixed = cases[1], cases[2]
        report = FirmwareRecordedReport(manifest_ref=manifest_ref, selection=selection, cases=cases, candidate=dict(bad=locators["bad"], candidate=locators["candidate"],
            diff_ref=ref("candidate/candidate.diff"), original_bad_header_ref=bad.inputs.selected_header, candidate_header_ref=fixed.inputs.selected_header,
            protected_checker_hash=bad.identity.checker_hash, protected_contract_hash=bad.identity.contract_hash, protected_profile_hash=bad.profile_hash,
            covered_mmio_indices=tuple(range(9)), outcome="scoped_curated_correction"), replays=tuple(dict(original=locators[label], repeated=locators[label+"-replay"], record_ref=ref("summary.json"), outcome="semantic_saved_program_replay") for label in ("bad","candidate")))
    report_ref = derived("native-report", v1._encode(report))
    record = native.EngineeringInvestigationV2(investigation_id="native-record", project_id=revision.project_id, revision_id=revision_id,
        project_revision_ref=revision_ref, manifest_ref=manifest_ref, report_kind=report.schema_version, report_ref=report_ref, native_records=attachments)
    authority = native.create_external_admission_authority(project_id=revision.project_id, revision_id=revision_id, project_revision_ref=revision_ref,
        manifest_ref=manifest_ref, report_kind=report.schema_version, report_ref=report_ref, locators=tuple(locators.values()), validator_implementation_hash=validator_hash)
    artifacts = tuple(v1.ArtifactBytes(reference=ref(name),data=raw[name]) for name in sorted(needed)) + tuple(metadata.values())
    return record, artifacts, authority


def test_synthetic_riscv_complete_canonical_round_trip_retains_pretty_raw_bytes():
    record, artifacts, authority = bundle(synthetic_riscv())
    admitted = v1.admit_investigation(record, artifacts, authority=authority)
    assert [(case.verdict,case.retired_count,case.compared_count) for case in admitted.native_records] == [("pass_within_scope",2,2),("design_mismatch",2,1),("pass_within_scope",2,2)]
    first = admitted.native_records[1].first_divergence
    assert (first.order,first.cycle,first.expected,first.observed) == (0,6,1,2)
    wire = v1.serialize_investigation(admitted)
    assert len(wire) <= 262144 and b'"engineering.admission.v2"' in wire
    assert v1.read_investigation(wire,artifacts,authority=authority).envelope == admitted.envelope
    assert not hasattr(admitted,"attempt_results")
    result = next(item for item in artifacts if item.reference == admitted.native_records[0].locator.raw_result_ref)
    assert b"\n  " in result.data
    assert sha(result.data) != canonical_hash(v1._decode(result.data,AttemptResult))


def test_synthetic_firmware_complete_round_trip_keeps_case_identity_without_attempt_id(tmp_path):
    record,artifacts,authority = bundle(synthetic_firmware(tmp_path),"firmware")
    admitted = v1.admit_investigation(record,artifacts,authority=authority)
    assert [case.verdict for case in admitted.native_records] == ["pass_within_scope","design_mismatch","pass_within_scope","design_mismatch","pass_within_scope"]
    assert all(case.locator.execution_ref.attempt_id is None for case in admitted.native_records)
    assert (admitted.native_records[1].first_conflict.mmio_index,admitted.native_records[1].first_conflict.bus_sequence) == (2,2)
    assert admitted.native_records[1].mailbox.value == 2 and admitted.native_records[2].mailbox.value == 1
    assert admitted.native_records[3].producer_phases == ("simulator",)
    assert v1.read_investigation(v1.serialize_investigation(admitted),artifacts,authority=authority).envelope == admitted.envelope
    assert len(artifacts) <= 256


@pytest.mark.parametrize("kind,path", [
    ("riscv",".artifacts/u4-riscv/ci-40920450439"), ("firmware",".artifacts/u5-firmware/ci-40929665294")])
def test_optional_accepted_bundle_bytes_revalidate_without_tools(kind,path):
    directory = ROOT/path
    if not directory.exists():
        pytest.skip("Accepted inert bundle not installed on this host")
    root = next(directory.glob(kind+"-evidence-*"))
    raw = {item.relative_to(root).as_posix():item.read_bytes() for item in root.rglob("*") if item.is_file()}
    record, artifacts, authority = bundle(raw,kind)
    admitted = v1.admit_investigation(record,artifacts,authority=authority)
    assert v1.read_investigation(v1.serialize_investigation(admitted),artifacts,authority=authority).envelope == admitted.envelope
    assert len(artifacts) <= 256
    if kind == "riscv":
        assert [(x.retired_count,x.cycles) for x in admitted.native_records] == [(17,68),(14,59),(17,68)]
    else:
        assert all(x.locator.execution_ref.attempt_id is None for x in admitted.native_records)
        assert (admitted.native_records[1].first_conflict.mmio_index,admitted.native_records[1].first_conflict.bus_sequence,admitted.native_records[1].first_conflict.cycle) == (2,31,95)


def forge_case_metadata(record,artifacts,authority,changes):
    """Rehash all derived ancestors and re-pin host refs; retain valid raw inputs."""
    blobs = {v1._key(item.reference):item for item in artifacts}
    report = json.loads(blobs[v1._key(record.report_ref)].data)
    report["cases"][1].update(changes)
    def replace(reference,data):
        blobs.pop(v1._key(reference))
        updated = reference.model_copy(update={"sha256":sha(data)})
        blobs[v1._key(updated)] = v1.ArtifactBytes(reference=updated,data=data)
        return updated
    model = native.RiscvRecordedAttempt.model_validate(report["cases"][1])
    old_case = record.native_records[1].reference
    case_ref = replace(old_case,v1._encode(model))
    manifest = json.loads(blobs[v1._key(record.manifest_ref)].data)
    manifest["facts"][1]["native_record"] = case_ref.model_dump(mode="json")
    manifest["facts"][1]["native_record_hash"] = case_ref.sha256
    manifest_ref = replace(record.manifest_ref,v1._encode(SystemManifest.model_validate(manifest)))
    report["manifest_ref"] = manifest_ref.model_dump(mode="json")
    for binding in report["selection"]["selected"]:
        if binding["ref"] == record.manifest_ref.model_dump(mode="json"):
            binding["ref"] = manifest_ref.model_dump(mode="json")
            binding["size_bytes"] = len(blobs[v1._key(manifest_ref)].data)
    report_model = RiscvRecordedReport.model_validate(report)
    report_ref = replace(record.report_ref,v1._encode(report_model))
    changed = record.model_dump(mode="json")
    changed["manifest_ref"],changed["report_ref"] = manifest_ref.model_dump(mode="json"),report_ref.model_dump(mode="json")
    changed["native_records"][1]["reference"] = case_ref.model_dump(mode="json")
    pins = authority.pins.model_dump(mode="python")
    pins.update(manifest_ref=manifest_ref,report_ref=report_ref)
    return native.EngineeringInvestigationV2.model_validate(changed),tuple(blobs.values()),native.create_external_admission_authority(**pins)


@pytest.mark.parametrize("changes", [{"cycles":9},{"retired_count":3},{"configuration_hash":"f"*64},
    {"first_divergence":{"field":"rd_value","order":0,"cycle":6,"expected":3,"observed":2}}])
def test_validly_rehashed_host_pinned_metadata_cannot_replace_independent_checker_result(changes):
    forged = forge_case_metadata(*bundle(synthetic_riscv()),changes)
    with pytest.raises(ValueError,match="registered checker revalidation"):
        v1.admit_investigation(forged[0],forged[1],authority=forged[2])


@pytest.mark.parametrize("mode", ["missing","duplicate","changed_bytes","orphan","over_count","over_blob","over_total"])
def test_every_exact_dependency_is_rehashed_with_unchanged_global_caps(mode):
    record,artifacts,authority = bundle(synthetic_riscv())
    first = artifacts[0]
    if mode == "missing": artifacts = artifacts[1:]
    elif mode == "duplicate": artifacts += (first,)
    elif mode == "changed_bytes": artifacts = (first.model_copy(update={"data":first.data+b"changed"}),*artifacts[1:])
    elif mode == "orphan":
        artifacts += (v1.ArtifactBytes(reference=EvidenceReference(artifact_id="orphan",revision_id="extra",sha256=sha(b"orphan")),data=b"orphan"),)
    elif mode == "over_count": artifacts = artifacts + (first,)*257
    elif mode == "over_blob":
        data = b"x"*(1048576+1)
        item = v1.ArtifactBytes.model_construct(reference=first.reference.model_copy(update={"sha256":sha(data)}),data=data)
        artifacts = (item,*artifacts[1:])
    else:
        data = b"x"*1048576
        artifacts = tuple(v1.ArtifactBytes(reference=EvidenceReference(artifact_id="large-"+str(i),revision_id="extra",sha256=sha(data)),data=data) for i in range(17))
    with pytest.raises(ValueError):
        v1.admit_investigation(record,artifacts,authority=authority)


@pytest.mark.parametrize("payload", [b'{"schema_version":"x","schema_version":"y"}',b'{"nested":{"a":1,"a":2}}',b'{"x":NaN}',b'{"x":Infinity}',b'{"x":1e999}',b'[1,2]',b'{',b'\xff'])
def test_native_json_duplicate_nonfinite_and_malformed_payloads_fail_closed(payload):
    with pytest.raises(ValueError):
        native._json(payload)


@pytest.mark.parametrize("mode", ["native_result_duplicate","pair_truncated","checker_changed","source_changed","unknown_schema","summary_changed"])
def test_raw_dependency_rederivation_rejects_invalid_synthetic_closure(mode):
    raw = synthetic_riscv()
    if mode == "native_result_duplicate":
        raw["control/result.json"] = raw["control/result.json"].replace(b'"attempt_id":',b'"attempt_id":"duplicate", "attempt_id":',1)
    elif mode == "pair_truncated":
        value = json.loads(raw["mutant/retirement.json"])
        value["observed"] = value["observed"][:1]
        raw["mutant/retirement.json"] = encoded(value)
    elif mode == "checker_changed":
        value = json.loads(raw["control/checker-manifest.json"])
        value["checker.py"] = "f"*64
        raw["control/checker-manifest.json"] = encoded(value)
    elif mode == "source_changed":
        raw["source/targets/riscv/picorv32_top.sv"] += b"\nmodified"
        value = json.loads(raw["source-manifest.json"])
        value["targets/riscv/picorv32_top.sv"] = sha(raw["source/targets/riscv/picorv32_top.sv"])
        raw["source-manifest.json"] = encoded(value)
    elif mode == "unknown_schema":
        value = json.loads(raw["control/experiment.json"])
        value["schema_version"] = "riscv.unknown.v9"
        raw["control/experiment.json"] = encoded(value)
    else:
        value = json.loads(raw["summary.json"])
        value["attempts"]["mutant"]["cycles"] = 9
        raw["summary.json"] = encoded(value)
    raw["receipt-sha256.json"] = encoded({name:sha(data) for name,data in raw.items() if name != "receipt-sha256.json"})
    with pytest.raises((ValueError,AssertionError)):
        record,artifacts,authority = bundle(raw)
        v1.admit_investigation(record,artifacts,authority=authority)


def test_external_authority_is_sealed_and_local_associations_are_not_a_v2_capability():
    record,artifacts,authority = bundle(synthetic_riscv())
    with pytest.raises(TypeError): native.TrustedExternalAdmissionAuthority()
    with pytest.raises(TypeError): native.AdmittedNativeInvestigation()
    with pytest.raises(ValueError): v1.admit_investigation(record,artifacts,authority=authority.pins.model_dump())
    with pytest.raises(ValueError): native.EngineeringInvestigationV2.model_validate({**record.model_dump(mode="json"),"attempts":[]})
    with pytest.raises(TypeError): native.create_external_admission_authority(**authority.pins.model_dump(mode="python"),attempts=())
    with pytest.raises(ValueError): v1.serialize_investigation(object())
    wrong = authority.pins.model_dump(mode="python")
    wrong["validator_implementation_hash"] = "f"*64
    with pytest.raises(ValueError,match="validator"):
        v1.read_investigation(v1.serialize_investigation(v1.admit_investigation(record,artifacts,authority=authority)),artifacts,
            authority=native.create_external_admission_authority(**wrong))


@pytest.mark.parametrize("mode", ["unknown_version","duplicate_version","wrong_record_schema","unknown_field","wrong_artifact_metadata","oversized"])
def test_version_facade_rejects_malformed_or_unrevalidated_receipts(mode):
    record,artifacts,authority = bundle(synthetic_riscv())
    wire = v1.serialize_investigation(v1.admit_investigation(record,artifacts,authority=authority))
    data = json.loads(wire)
    if mode == "unknown_version": data["schema_version"] = "engineering.admission.v9"
    elif mode == "wrong_record_schema": data["record"]["schema_version"] = "engineering.investigation.v1"
    elif mode == "unknown_field": data["local_campaign_id"] = "pretend"
    elif mode == "wrong_artifact_metadata": data["artifacts"][0]["size_bytes"] += 1
    wire = encoded(data)
    if mode == "duplicate_version": wire = wire.replace(b'"schema_version":"engineering.admission.v2"',b'"schema_version":"engineering.admission.v1","schema_version":"engineering.admission.v2"')
    if mode == "oversized": wire += b" "*262145
    with pytest.raises(ValueError): v1.read_investigation(wire,artifacts,authority=authority)


def test_admission_and_read_have_no_filesystem_process_network_or_database_io(monkeypatch):
    import builtins
    import socket
    import subprocess
    import sqlite3
    record,artifacts,authority = bundle(synthetic_riscv())
    def forbidden(*args,**kwargs):
        raise AssertionError("Pure admission attempted external I/O")
    monkeypatch.setattr(builtins,"open",forbidden)
    monkeypatch.setattr(Path,"read_bytes",forbidden)
    monkeypatch.setattr(Path,"write_bytes",forbidden)
    monkeypatch.setattr(subprocess,"run",forbidden)
    monkeypatch.setattr(subprocess,"Popen",forbidden)
    monkeypatch.setattr(socket,"create_connection",forbidden)
    monkeypatch.setattr(sqlite3,"connect",forbidden)
    admitted = v1.admit_investigation(record,artifacts,authority=authority)
    assert v1.read_investigation(v1.serialize_investigation(admitted),artifacts,authority=authority).envelope == admitted.envelope


@pytest.mark.parametrize("mode", ["recompiled_replay","changed_profile","changed_checker","changed_bus"])
def test_synthetic_firmware_invalid_native_dependencies_fail_closed(tmp_path,mode):
    raw = synthetic_firmware(tmp_path)
    if mode == "recompiled_replay":
        execution = json.loads(raw["bad-replay/execution.json"])
        execution["commands"].insert(0,json.loads(raw["bad/execution.json"])["commands"][0])
        raw["bad-replay/execution.json"] = encoded(execution)
    elif mode == "changed_profile":
        prep = json.loads(raw["control/preparation.json"])
        prep["profile"]["compiler_flags"].append("-unsupported")
        prep["profile_hash"] = sha(encoded(prep["profile"]))
        raw["control/preparation.json"] = encoded(prep)
    elif mode == "changed_checker":
        name = "src/veriforge/firmware/harness.py"
        raw["source/"+name] = b"raise RuntimeError('COPIED_CODE_MUST_NOT_RUN')"
        source = json.loads(raw["source-manifest.json"])
        source[name] = sha(raw["source/"+name])
        raw["source-manifest.json"] = encoded(source)
    else:
        observation = json.loads(raw["control/observation.json"])
        observation["transfers"][0]["write_strobes"] = 0
        raw["control/observation.json"] = encoded(observation)
    raw["receipt-sha256.json"] = encoded({name:sha(data) for name,data in raw.items() if name != "receipt-sha256.json"})
    with pytest.raises((ValueError,AssertionError)):
        record,artifacts,authority = bundle(raw,"firmware")
        v1.admit_investigation(record,artifacts,authority=authority)


def test_rehashed_saved_replay_cannot_point_to_another_original_execution(tmp_path):
    raw = synthetic_firmware(tmp_path)
    execution = json.loads(raw["bad-replay/execution.json"])
    execution["replay_original_hash"] = "f"*64
    raw["bad-replay/execution.json"] = encoded(execution,pretty=True)
    check = json.loads(raw["bad-replay.check.json"])
    check.update(replay_original_hash="f"*64,execution_sha256=sha(raw["bad-replay/execution.json"]))
    raw["bad-replay.check.json"] = encoded(check,pretty=True)
    summary = json.loads(raw["summary.json"])
    summary["cases"]["bad-replay"] = check
    raw["summary.json"] = encoded(summary,pretty=True)
    raw["receipt-sha256.json"] = encoded({name:sha(data) for name,data in raw.items() if name != "receipt-sha256.json"})
    record,artifacts,authority = bundle(raw,"firmware")
    with pytest.raises(ValueError,match="registered checker revalidation"):
        v1.admit_investigation(record,artifacts,authority=authority)


def test_native_schema_registry_does_not_widen_v1_or_accept_hybrid_attachments():
    assert v1.NATIVE_SCHEMA_ALLOWLIST == frozenset({("firmware","AddressFact.v1"),("rtl","AddressFact.v1"),
        ("vivado","PinObservation.v1"),("kicad","ConnectorObservation.v1"),("system","RootObservation.v1"),("system","IntentObservation.v1")})
    record,artifacts,authority = bundle(synthetic_riscv())
    changed = record.model_dump(mode="json")
    changed["native_records"][0]["domain"] = "firmware"
    with pytest.raises(ValueError): native.EngineeringInvestigationV2.model_validate(changed)
    wire = v1.serialize_investigation(v1.admit_investigation(record,artifacts,authority=authority))
    value = json.loads(wire)
    del value["schema_version"]
    with pytest.raises(ValueError): native.read_native_investigation(encoded(value),artifacts,authority=authority)
