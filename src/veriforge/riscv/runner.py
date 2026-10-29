"""Prepared bounded real-tool path. Missing tools never become a synthetic pass."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
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


def _simulation_output(path, experiment):
    if path.stat().st_size > 16*1024*1024:
        raise TraceFormatError('DUT output exceeds byte bound')
    result = SimulationOutput.model_validate_json(path.read_text())
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


def run_experiment(experiment: RiscvExperiment, output_dir: Path, *, cancel_event=None) -> AttemptResult:
    experiment = RiscvExperiment.model_validate(experiment.model_dump() if isinstance(experiment, RiscvExperiment) else experiment)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('attempt directory must be empty')
    started, tools, identity, signature, uncertainties, cycles = time.monotonic(), [], None, None, [], 0
    status, verdict = 'completed', 'unsupported'
    (output / 'experiment.json').write_text(experiment.model_dump_json(indent=2))
    (output / 'profile.json').write_bytes(_json(asdict(PLATFORM)))
    try:
        if platform.system() != 'Linux' or any(shutil.which(t) is None for t in ('verilator', 'spike', 'riscv64-unknown-elf-gcc', 'riscv64-unknown-elf-objcopy')):
            raise UnsupportedTrace('Linux pinned RISC-V toolchain image required; no CPU/reference execution performed')
        revision = Path('/opt/spike/UPSTREAM_REVISION').read_text().strip()
        if revision != REFERENCE_REVISION:
            raise UnsupportedTrace('reference binary is not bound to the selected Spike revision')
        env = _environment()
        for executable in ('verilator', 'riscv64-unknown-elf-gcc'):
            version = subprocess.run([executable, '--version'], capture_output=True, text=True, check=True, timeout=5, env=env).stdout.splitlines()[0]
            tools.append(ToolVersion(name='riscv-gcc' if executable.endswith('gcc') else 'verilator', version=version[:256]))
        tools.extend((ToolVersion(name='spike-revision', version=revision),
            ToolVersion(name='cocotb', version=importlib.metadata.version('cocotb')),
            ToolVersion(name='python', version=platform.python_version())))
        for name in ('assembler', 'compiler', 'objcopy', 'reference', 'dut'):
            (output / name).mkdir()
        with tempfile.TemporaryDirectory(prefix='veriforge-riscv-') as temporary:
            scratch = Path(temporary)
            env.update(HOME=str(scratch), TMPDIR=str(scratch), CCACHE_DIR='/tmp/veriforge-riscv-ccache', CCACHE_MAXSIZE='32M', PYTHONDONTWRITEBYTECODE='1')
            def execute(command, phase):
                remaining = experiment.wall_time_seconds - (time.monotonic()-started)
                if remaining <= 0:
                    return None, 'timeout'
                return _bounded(command, output / phase, env, remaining, cancel_event, 32*1024*1024)
            code, reason = execute(list(assemble_command(output)), 'assembler')
            if not reason and code == 0:
                code, reason = execute(list(compile_command(output)), 'compiler')
            if reason or code:
                status, verdict = ('cancelled', 'inconclusive') if reason == 'cancelled' else ('completed', reason or 'build_error')
            else:
                code, reason = execute(['riscv64-unknown-elf-objcopy', '-O', 'binary', str(output/'smoke.elf'), str(output/'smoke.bin')], 'objcopy')
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
                                'assembly_command': list(assemble_command(output)),
                                'compiler_command': list(compile_command(output)),
                                'reference_command': list(reference_command(output/'smoke.elf', output/'reference'/'commit.log', experiment.max_retired)),
                                'spike_binary_hash': _hash(Path(shutil.which('spike')).read_bytes()),
                                'image_package_hash': _hash(Path('/opt/veriforge/PACKAGE_VERSIONS').read_bytes()),
                                'driver_hash': _hash(Path(__file__).with_name('sim_test.py').read_bytes()),
                                'memory_hash': _hash(Path(__file__).with_name('memory.py').read_bytes()),
                                'job_hash': _hash(Path(__file__).with_name('sim_job.py').read_bytes())}
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
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, importlib.metadata.PackageNotFoundError) as exc:
        status, verdict, signature = 'infrastructure_error', 'harness_error', None
        uncertainties.append(str(exc)[:2000])
    artifacts = tuple(ArtifactReference(artifact_id=f'artifact-{index}', path=p.relative_to(output).as_posix(),
        sha256=_hash(p.read_bytes()), size_bytes=p.stat().st_size, media_type='application/json' if p.suffix=='.json' else 'application/octet-stream')
        for index, p in enumerate(sorted(p for p in output.rglob('*') if p.is_file())))
    result = AttemptResult(attempt_id='attempt-'+uuid4().hex, experiment_id=experiment.experiment_id,
        execution_status=status, verdict=verdict, identity=identity, tool_versions=tuple(tools), failure_signature=signature,
        artifacts=artifacts, usage=ResourceUsage(cycles=cycles, wall_time_seconds=float(time.monotonic()-started),
            artifact_bytes=sum(a.size_bytes for a in artifacts)), uncertainties=tuple(uncertainties))
    (output/'result.json').write_text(result.model_dump_json(indent=2))
    return result
