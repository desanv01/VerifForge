"""Prepared bounded real-tool path. Missing tools never become a synthetic pass."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import stat
import struct
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from uuid import uuid4

from veriforge.contracts import (AttemptResult, ArtifactReference, EvidenceIdentity,
    FailureSignature, ResourceUsage, ToolVersion, canonical_hash)
from veriforge.rtl.runner import _bounded, _environment
from .contracts import RiscvExperiment, SimulationOutput
from .profile import ROOT, PLATFORM, CORE_REVISION, REFERENCE_REVISION, assemble_command, compile_command, reference_command
from .trace import parse_spike_commits, normalize_rvfi, UnsupportedTrace, TraceFormatError
from .checker import compare_retirements


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _read_bounded(path, cap):
    before = path.stat()
    if path.is_symlink() or path.is_junction() or not stat.S_ISREG(before.st_mode) or before.st_size > cap:
        raise ValueError('file is linked, nonregular or exceeds byte bound')
    stamp = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if stamp(opened) != stamp(before):
            raise ValueError('file changed before bounded read')
        data = stream.read(cap+1)
        after = os.fstat(stream.fileno())
    if len(data) > cap or len(data) != before.st_size or stamp(after) != stamp(before) or stamp(path.stat()) != stamp(before):
        raise ValueError('file changed or exceeded bounded read')
    return data


def _simulation_output(path, experiment, *, raw_bytes=None):
    try:
        data = _read_bounded(path,16*1024*1024) if raw_bytes is None else raw_bytes
    except ValueError as exc:
        raise TraceFormatError(str(exc)) from None
    if len(data) > 16*1024*1024:
        raise TraceFormatError('DUT output exceeds byte bound')
    result = SimulationOutput.model_validate_json(data)
    if result.cycles > experiment.max_cycles or len(result.events) > experiment.max_retired:
        raise TraceFormatError('DUT output exceeds experiment bounds')
    previous = -1
    for event in result.events:
        if event.cycle is None or not previous < event.cycle < result.cycles:
            raise TraceFormatError('DUT event timing is missing, unordered or out of range')
        previous = event.cycle
    def completion(event):
        return event.rvfi_mem_addr == PLATFORM.completion and bool(event.rvfi_mem_wmask)
    if any(event.rvfi_trap or completion(event) for event in result.events[:-1]):
        raise TraceFormatError('DUT trace continues after a stopping event')
    last = result.events[-1] if result.events else None
    if result.termination == 'completion_store_retired':
        consistent = last is not None and completion(last) and not last.rvfi_trap
    elif result.termination == 'trap':
        consistent = last is not None and last.rvfi_trap
    elif result.termination == 'retirement_limit':
        consistent = len(result.events) == experiment.max_retired and not (last.rvfi_trap or completion(last))
    else:
        consistent = result.cycles == experiment.max_cycles and (last is None or not (last.rvfi_trap or completion(last)))
    if not consistent or (result.termination != 'cycle_limit' and last.cycle+1 != result.cycles):
        raise TraceFormatError('DUT termination is inconsistent with observed events and budget')
    return result


def _program_layout(object_data, elf, binary):
    """Pure fixed RV32I ELF/bin admission; no objcopy or artifact code execution."""
    for data, kind in ((object_data, 1), (elf, 2)):
        if len(data) < 52 or data[:7] != b'\x7fELF\x01\x01\x01':
            raise ValueError('saved program requires ELF32 little-endian files')
        header = struct.unpack_from('<16sHHIIIIIHHHHHH', data)
        if header[1:4] != (kind, 243, 1) or header[8] != 52 or header[7] != 0:
            raise ValueError('saved program ELF type/machine/flags differ')
    if not binary or len(binary) > PLATFORM.ram_bytes or len(binary) % 4:
        raise ValueError('saved binary outside fixed RAM bounds')
    if header[4] != PLATFORM.entry or header[9] != 32 or not 1 <= header[10] <= 32:
        raise ValueError('saved executable entry/program headers differ')
    if header[5] + 32*header[10] > len(elf):
        raise ValueError('saved executable header bounds differ')
    expected, loaded = bytearray(len(binary)), False
    for index in range(header[10]):
        kind, offset, virtual, physical, size, memory, flags, alignment = struct.unpack_from('<8I', elf, header[5]+32*index)
        if kind in (2, 3):
            raise ValueError('dynamic saved executable unsupported')
        if kind != 1:
            continue
        start = physical - PLATFORM.ram_base
        if virtual != physical or start < 0 or size > memory or start+memory > PLATFORM.ram_bytes or offset+size > len(elf):
            raise ValueError('saved executable load segment outside fixed RAM')
        if size:
            if start+size > len(binary):
                raise ValueError('saved binary omits executable load bytes')
            expected[start:start+size] = elf[offset:offset+size]
            loaded = True
    if not loaded or bytes(expected) != binary:
        raise ValueError('saved binary differs from executable load bytes')


def validate_saved_program(directory, experiment):
    """Admit only a trusted completed control package, never an upload authority.

    Bounded immutable bytes are captured before tools; no original Python is
    loaded. File/hash consistency is not authentication of an untrusted package.
    """
    directory = Path(directory)
    if directory.is_symlink() or directory.is_junction() or not directory.is_dir():
        raise ValueError('saved control must be a regular trusted directory')
    directory = directory.resolve()
    def read(name, cap=16*1024*1024):
        relative = Path(name)
        path = directory / relative
        if relative.is_absolute() or '..' in relative.parts or not path.resolve().is_relative_to(directory):
            raise ValueError('saved artifact path outside trusted directory')
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents) if p != directory and p.is_relative_to(directory)):
            raise ValueError('linked saved artifact unsupported')
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > cap:
            raise ValueError('saved artifact is nonregular or oversized')
        data = _read_bounded(path,cap)
        if len(data) != info.st_size:
            raise ValueError('saved artifact changed during read')
        return data
    raw_result = read('result.json', 1024*1024)
    result = AttemptResult.model_validate_json(raw_result)
    if experiment.fixture_id != 'picorv32_rv32i' or result.experiment_id != experiment.experiment_id or result.execution_status != 'completed' or result.verdict != 'pass_within_scope' or result.identity is None or result.failure_signature is not None:
        raise ValueError('only completed passing saved control is eligible')
    if len(result.artifacts) > 128 or len({a.path for a in result.artifacts}) != len(result.artifacts):
        raise ValueError('saved artifact inventory outside bound')
    captured, total = {}, 0
    for artifact in result.artifacts:
        data = read(artifact.path)
        total += len(data)
        if total > 32*1024*1024 or len(data) != artifact.size_bytes or _hash(data) != artifact.sha256:
            raise ValueError('saved artifact identity/budget differs')
        captured[artifact.path] = data
    required = {'experiment.json', 'profile.json', 'source-manifest.json', 'checker-manifest.json', 'environment.json',
                'smoke.o', 'smoke.elf', 'smoke.bin', 'comparison.json', 'retirement.json', 'reference/commit.log', 'dut/rvfi.json'}
    if not required <= captured.keys():
        raise ValueError('saved control proof incomplete')
    if result.usage.artifact_bytes != total:
        raise ValueError('saved control artifact accounting differs')
    records = {name: json.loads(captured[name+'.json']) for name in ('experiment', 'profile', 'source-manifest', 'checker-manifest', 'environment')}
    for name, field in (('experiment','experiment_hash'), ('profile','contract_hash'), ('source-manifest','source_hash'),
                        ('checker-manifest','checker_hash'), ('environment','environment_hash')):
        if _hash(_json(records[name])) != getattr(result.identity, field):
            raise ValueError('saved control record identity differs')
    source = {name: _hash((ROOT/'targets/riscv'/name).read_bytes()) for name in ('upstream/picorv32.v','picorv32_top.sv','smoke.S','link.ld')}
    source.update(core_revision=CORE_REVISION, fixture_id=experiment.fixture_id, profile_code_hash=_hash(Path(__file__).with_name('profile.py').read_bytes()))
    checker = {name: _hash(Path(__file__).with_name(name).read_bytes()) for name in ('checker.py','trace.py','contracts.py')}
    if records['experiment'] != experiment.model_dump(mode='json') or records['profile'] != asdict(PLATFORM) or records['source-manifest'] != source or records['checker-manifest'] != checker:
        raise ValueError('saved control source/checker/profile/experiment changed')
    environment = records['environment']
    if 'program_mode' in environment:
        raise ValueError('saved original must be a freshly compiled control')
    if (environment.get('reference_revision') != REFERENCE_REVISION
            or any(not isinstance(environment.get(k),str) or re.fullmatch('[0-9a-f]{64}',environment[k]) is None
                   for k in ('spike_binary_hash','image_package_hash'))
            or not environment.get('assembly_command') or not environment.get('compiler_command')
            or environment.get('tools') != [t.model_dump(mode='json') for t in result.tool_versions]
            or not any(t.name == 'riscv-gcc' for t in result.tool_versions)):
        raise ValueError('saved compiled control runtime provenance incomplete')
    for key, name in (('driver_hash','sim_test.py'), ('memory_hash','memory.py'), ('job_hash','sim_job.py')):
        if environment[key] != _hash(Path(__file__).with_name(name).read_bytes()):
            raise ValueError('saved control runtime changed')
    for key, name in (('object_hash','smoke.o'), ('elf_hash','smoke.elf'), ('binary_hash','smoke.bin')):
        if environment[key] != _hash(captured[name]):
            raise ValueError('saved program identity changed')
    _program_layout(captured['smoke.o'], captured['smoke.elf'], captured['smoke.bin'])
    reference = parse_spike_commits(captured['reference/commit.log'].decode(), max_retired=experiment.max_retired)
    raw = _simulation_output(directory/'dut/rvfi.json', experiment, raw_bytes=captured['dut/rvfi.json'])
    if raw.termination != 'completion_store_retired' or result.usage.cycles != raw.cycles:
        raise ValueError('saved control did not complete within budget')
    observed = normalize_rvfi(raw.events, max_retired=experiment.max_retired)
    checked = compare_retirements(reference, observed)
    if checked.verdict != 'pass_within_scope' or checked.model_dump(mode='json') != json.loads(captured['comparison.json']):
        raise ValueError('saved control independent comparison differs')
    pair = {'schema_version':'riscv.retirement-pair.v1', 'reference':[e.model_dump(mode='json') for e in reference], 'observed':[e.model_dump(mode='json') for e in observed]}
    if pair != json.loads(captured['retirement.json']):
        raise ValueError('saved control normalized evidence differs')
    provenance = {'schema_version':'riscv.saved-program.v1', 'original_result_sha256':_hash(raw_result),
        'original_attempt_id':result.attempt_id, 'original_experiment_id':result.experiment_id,
        'original_identity':result.identity.model_dump(mode='json'), 'program_hashes':{name:_hash(captured[name]) for name in ('smoke.o','smoke.elf','smoke.bin')},
        'original_run_identity':None}
    if (directory/'saved-origin.json').exists():
        origin = json.loads(read('saved-origin.json', 16384))
        if (set(origin) != {'schema_version','source_commit','run_id','run_attempt','case','attempt_id','result_sha256'}
                or not isinstance(origin['source_commit'], str) or re.fullmatch('[0-9a-f]{40}', origin['source_commit']) is None
                or not isinstance(origin['run_id'], str) or re.fullmatch('[0-9]{1,20}', origin['run_id']) is None
                or not isinstance(origin['run_attempt'], str) or re.fullmatch('[0-9]{1,4}', origin['run_attempt']) is None
                or origin['schema_version'] != 'riscv.ci-origin.v1' or origin['result_sha256'] != _hash(raw_result)
                or origin['attempt_id'] != result.attempt_id or origin['case'] != 'control'):
            raise ValueError('saved original run attribution differs')
        provenance['original_run_identity'] = origin
    return provenance, {name:captured[name] for name in ('smoke.o','smoke.elf','smoke.bin')}, environment


def run_experiment(experiment: RiscvExperiment, output_dir: Path, *, cancel_event=None, saved_program=None) -> AttemptResult:
    experiment = RiscvExperiment.model_validate(experiment.model_dump() if isinstance(experiment, RiscvExperiment) else experiment)
    output = Path(output_dir).resolve()
    if saved_program is not None:
        saved_root = Path(saved_program).resolve()
        if output.is_relative_to(saved_root) or saved_root.is_relative_to(output):
            raise ValueError('saved control and output must be disjoint')
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('attempt directory must be empty')
    started, tools, identity, signature, uncertainties, cycles = time.monotonic(), [], None, None, [], 0
    status, verdict = 'completed', 'unsupported'
    (output / 'experiment.json').write_text(experiment.model_dump_json(indent=2))
    (output / 'profile.json').write_bytes(_json(asdict(PLATFORM)))
    try:
        saved = validate_saved_program(saved_program, experiment) if saved_program is not None else None
        if cancel_event is not None and cancel_event.is_set():
            status, verdict = 'cancelled', 'inconclusive'
            raise UnsupportedTrace('cancelled before native execution')
        if saved and time.monotonic()-started >= experiment.wall_time_seconds:
            verdict = 'timeout'
            raise UnsupportedTrace('total execution budget exhausted before tools')
        needed = ('verilator', 'spike') if saved else ('verilator', 'spike', 'riscv64-unknown-elf-gcc', 'riscv64-unknown-elf-objcopy')
        if platform.system() != 'Linux' or any(shutil.which(t) is None for t in needed):
            raise UnsupportedTrace('Linux pinned RISC-V toolchain image required; no CPU/reference execution performed')
        revision = Path('/opt/spike/UPSTREAM_REVISION').read_text().strip()
        if revision != REFERENCE_REVISION:
            raise UnsupportedTrace('reference binary is not bound to the selected Spike revision')
        env = _environment()
        for executable in (('verilator',) if saved else ('verilator', 'riscv64-unknown-elf-gcc')):
            version = subprocess.run([executable, '--version'], capture_output=True, text=True, check=True, timeout=5, env=env).stdout.splitlines()[0]
            tools.append(ToolVersion(name='riscv-gcc' if executable.endswith('gcc') else 'verilator', version=version[:256]))
        tools.extend((ToolVersion(name='spike-revision', version=revision),
            ToolVersion(name='cocotb', version=importlib.metadata.version('cocotb')),
            ToolVersion(name='python', version=platform.python_version())))
        if saved:
            if saved[2]['reference_revision'] != revision or saved[2]['spike_binary_hash'] != _hash(Path(shutil.which('spike')).read_bytes()) or saved[2]['image_package_hash'] != _hash(Path('/opt/veriforge/PACKAGE_VERSIONS').read_bytes()):
                raise ValueError('saved control reference/image runtime changed')
            for name, data in saved[1].items():
                (output/name).write_bytes(data)
            (output/'saved-program.json').write_bytes(_json(saved[0]))
        for name in (('reference','dut') if saved else ('assembler', 'compiler', 'objcopy', 'reference', 'dut')):
            (output / name).mkdir()
        with tempfile.TemporaryDirectory(prefix='veriforge-riscv-') as temporary:
            scratch = Path(temporary)
            env.update(HOME=str(scratch), TMPDIR=str(scratch), CCACHE_DIR='/tmp/veriforge-riscv-ccache', CCACHE_MAXSIZE='32M', PYTHONDONTWRITEBYTECODE='1')
            def execute(command, phase):
                remaining = experiment.wall_time_seconds - (time.monotonic()-started)
                if remaining <= 0:
                    return None, 'timeout'
                code, reason = _bounded(command, output / phase, env, remaining, cancel_event, 32*1024*1024)
                if reason is None and code == 0:
                    if cancel_event is not None and cancel_event.is_set():
                        reason = 'cancelled'
                    elif time.monotonic()-started >= experiment.wall_time_seconds:
                        reason = 'timeout'
                return code, reason
            code, reason = (0, None) if saved else execute(list(assemble_command(output)), 'assembler')
            if not saved and not reason and code == 0:
                code, reason = execute(list(compile_command(output)), 'compiler')
            if reason or code:
                status, verdict = ('cancelled', 'inconclusive') if reason == 'cancelled' else ('completed', reason or 'build_error')
            else:
                code, reason = (0, None) if saved else execute(['riscv64-unknown-elf-objcopy', '-O', 'binary', str(output/'smoke.elf'), str(output/'smoke.bin')], 'objcopy')
                if reason or code:
                    status, verdict = ('cancelled', 'inconclusive') if reason == 'cancelled' else ('completed', reason or 'build_error')
                else:
                    code, reason = execute(list(reference_command(output/'smoke.elf', output/'reference'/'commit.log', experiment.max_retired)), 'reference')
                    if reason or code:
                        status, verdict = ('cancelled', 'inconclusive') if reason == 'cancelled' else ('completed', reason or 'harness_error')
                    else:
                        job = dict(fixture_id=experiment.fixture_id, build=str(scratch/'build'), output=str(output/'dut'),
                            binary=str(output/'smoke.bin'), trace=str(output/'dut'/'rvfi.json'),
                            max_cycles=experiment.max_cycles, max_retired=experiment.max_retired)
                        (output/'job.json').write_bytes(_json(job))
                        code, reason = execute([sys.executable, '-m', 'veriforge.riscv.sim_job', str(output/'job.json')], 'dut')
                        if reason or code:
                            status, verdict = ('cancelled', 'inconclusive') if reason == 'cancelled' else ('completed', reason or ('harness_error' if (output/'dut'/'built.marker').exists() else 'build_error'))
                        else:
                            reference = parse_spike_commits((output/'reference'/'commit.log').read_text(), max_retired=experiment.max_retired)
                            raw = _simulation_output(output/'dut'/'rvfi.json', experiment)
                            observed = normalize_rvfi(raw.events, max_retired=experiment.max_retired)
                            checked = compare_retirements(reference, observed)
                            verdict, cycles = checked.verdict, raw.cycles
                            (output/'retirement.json').write_bytes(_json({'schema_version': 'riscv.retirement-pair.v1',
                                'reference': [e.model_dump(mode='json') for e in reference], 'observed': [e.model_dump(mode='json') for e in observed]}))
                            (output/'comparison.json').write_text(checked.model_dump_json(indent=2))
                            if raw.termination in ('cycle_limit', 'retirement_limit') and checked.verdict != 'design_mismatch':
                                verdict = 'resource_limit'
                            components = {name: _hash((ROOT/'src/veriforge/riscv'/name).read_bytes()) for name in ('checker.py', 'trace.py', 'contracts.py')}
                            source = {name: _hash((ROOT/'targets/riscv'/name).read_bytes()) for name in ('upstream/picorv32.v', 'picorv32_top.sv', 'smoke.S', 'link.ld')}
                            source.update(core_revision=CORE_REVISION, fixture_id=experiment.fixture_id,
                                profile_code_hash=_hash(Path(__file__).with_name('profile.py').read_bytes()))
                            (output/'source-manifest.json').write_bytes(_json(source))
                            (output/'checker-manifest.json').write_bytes(_json(components))
                            environment = {'tools': [t.model_dump(mode='json') for t in tools], 'reference_revision': revision,
                                'reference_submodules': Path('/opt/spike/UPSTREAM_SUBMODULES').read_text(),
                                'elf_hash': _hash((output/'smoke.elf').read_bytes()), 'binary_hash': _hash((output/'smoke.bin').read_bytes()),
                                'object_hash': _hash((output/'smoke.o').read_bytes()),
                                'assembly_command': [] if saved else list(assemble_command(output)),
                                'compiler_command': [] if saved else list(compile_command(output)),
                                'reference_command': list(reference_command(output/'smoke.elf', output/'reference'/'commit.log', experiment.max_retired)),
                                'spike_binary_hash': _hash(Path(shutil.which('spike')).read_bytes()),
                                'image_package_hash': _hash(Path('/opt/veriforge/PACKAGE_VERSIONS').read_bytes()),
                                'driver_hash': _hash(Path(__file__).with_name('sim_test.py').read_bytes()),
                                'memory_hash': _hash(Path(__file__).with_name('memory.py').read_bytes()),
                                'job_hash': _hash(Path(__file__).with_name('sim_job.py').read_bytes())}
                            if saved:
                                environment.update(program_mode='saved_program_no_compilation', saved_program_sha256=_hash((output/'saved-program.json').read_bytes()))
                            (output/'environment.json').write_bytes(_json(environment))
                            identity = EvidenceIdentity(source_hash=_hash(_json(source)), checker_hash=_hash(_json(components)),
                                contract_hash=_hash(_json(asdict(PLATFORM))), experiment_hash=canonical_hash(experiment), environment_hash=_hash(_json(environment)))
                            if checked.first_divergence:
                                difference = checked.first_divergence
                                if observed[difference.order].source_cycle is None:
                                    raise TraceFormatError('missing DUT timing metadata for failure location')
                                signature = FailureSignature(requirement_id=difference.requirement_id, failure_class='riscv.'+difference.field,
                                    first_cycle=observed[difference.order].source_cycle, expected=difference.expected, observed=difference.observed,
                                    signature_hash=_hash(_json({'checker': identity.checker_hash, 'contract': identity.contract_hash, 'field': difference.field})))
                            uncertainties.append('Experimental RV32I subset; comparison.json records retirement order; first_cycle is DUT diagnostic timing only.')
    except UnsupportedTrace as exc:
        uncertainties.append(str(exc))
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, importlib.metadata.PackageNotFoundError) as exc:
        status, verdict, signature = 'infrastructure_error', 'harness_error', None
        uncertainties.append(str(exc)[:2000])
    artifacts = tuple(ArtifactReference(artifact_id=f'artifact-{index}', path=p.relative_to(output).as_posix(),
        sha256=_hash(p.read_bytes()), size_bytes=p.stat().st_size, media_type='application/json' if p.suffix=='.json' else 'application/octet-stream')
        for index, p in enumerate(sorted(p for p in output.rglob('*') if p.is_file())))
    if status == 'completed' and verdict in ('pass_within_scope','design_mismatch'):
        if cancel_event is not None and cancel_event.is_set():
            status, verdict, signature = 'cancelled', 'inconclusive', None
        elif time.monotonic()-started >= experiment.wall_time_seconds:
            verdict, signature = 'timeout', None
    result = AttemptResult(attempt_id='attempt-'+uuid4().hex, experiment_id=experiment.experiment_id,
        execution_status=status, verdict=verdict, identity=identity, tool_versions=tuple(tools), failure_signature=signature,
        artifacts=artifacts, usage=ResourceUsage(cycles=cycles, wall_time_seconds=float(time.monotonic()-started),
            artifact_bytes=sum(a.size_bytes for a in artifacts)), uncertainties=tuple(uncertainties))
    (output/'result.json').write_text(result.model_dump_json(indent=2))
    return result
