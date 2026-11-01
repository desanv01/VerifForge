"""Closed no-site legacy reader; bounded operation-local byte-keyed batches."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import time

from pydantic import TypeAdapter
from veriforge.contracts.engineering import SystemManifest
from veriforge.contracts.native_tools import RecordedToolReport
from veriforge.system_investigation import records, native_tool_records as native

LEGACY_HASH = '@LEGACY_HASH@'
CURRENT_HASH = '@CURRENT_HASH@'
LEGACY_MANIFEST_SHA = '@LEGACY_MANIFEST_SHA@'
LEGACY_WORKER_SHA = '@LEGACY_WORKER_SHA@'
ACTIVE_MANIFEST_SHA = '@ACTIVE_MANIFEST_SHA@'
REPORT_KINDS = ('riscv.recorded-report.v1', 'firmware.recorded-report.v1', 'riscv.recorded-report.v2')
HERE = Path(__file__).resolve().parent
MAX_NATIVE_PAGE = 4
MAX_OPERATION_BYTES = 17 * 1048576
OPERATION_SECONDS = 8.0
_CURRENT = ContextVar('native_validation_operation', default=None)

@TREE_GUARD@


class PolicyUnavailable(ValueError):
    pass


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _pinned(path, expected, limit=1048576):
    if path.is_symlink() or not path.resolve().is_relative_to(HERE.parent):
        raise PolicyUnavailable('Native validator path differs')
    with path.open('rb') as source:
        raw = source.read(limit + 1)
    if len(raw) > limit or _sha(raw) != expected:
        raise PolicyUnavailable('Native validator source differs')
    return raw


def _runtime(manifest):
    runtime = manifest['runtime']
    if platform.python_version() != runtime['python']:
        raise PolicyUnavailable('Native validator Python runtime differs')
    roots = {}
    for name, files in runtime['distribution_files'].items():
        distribution = importlib.metadata.distribution(name)
        if distribution.version != runtime[name]:
            raise PolicyUnavailable('Native validator dependency runtime differs')
        root = Path(distribution.locate_file('')).absolute()
        pins = roots.setdefault(root, {})
        for relative, expected in files.items():
            path = Path(distribution.locate_file(relative)).absolute()
            if path != root / relative or (relative in pins and pins[relative] != expected):
                raise PolicyUnavailable('Native validator dependency bytes differ')
            pins[relative] = expected
    for root, pins in roots.items():
        try:
            _check_tree(root, pins, limit=32*1048576, error_type=PolicyUnavailable)
        except PolicyUnavailable:
            raise PolicyUnavailable('Native validator dependency bytes differ') from None
    interpreter = runtime['native_interpreter']
    path = Path(interpreter['path'])
    actual = Path(getattr(sys, '_base_executable', sys.executable)).resolve()
    if path.resolve() != actual or path.is_symlink() or _sha(path.read_bytes()) != interpreter['sha256']:
        raise PolicyUnavailable('Native validator interpreter differs')


def _verify_sources_inner():
    active = json.loads(_pinned(HERE / 'native-validator-dependencies.json', ACTIVE_MANIFEST_SHA))
    _runtime(active)
    _check_tree(HERE.parent, active['files'], error_type=PolicyUnavailable)
    root = HERE / '_native_validator_v1'
    legacy = json.loads(_pinned(root / 'manifest.json', LEGACY_MANIFEST_SHA))
    if legacy['runtime'] != active['runtime']:
        raise PolicyUnavailable('Native validator runtime vectors differ')
    _pinned(root / 'worker.py', LEGACY_WORKER_SHA)
    _check_tree(root/'src', {name.removeprefix('src/'):pin for name,pin in legacy['files'].items() if name.startswith('src/')}, exact=True, error_type=PolicyUnavailable)
    _check_tree(root, {name:pin for name,pin in legacy['files'].items() if not name.startswith('src/')}, error_type=PolicyUnavailable)
    dependencies = root / 'dependencies'
    _check_tree(dependencies, legacy['dependency_files'], exact=True, limit=32*1048576, error_type=PolicyUnavailable)
    return legacy


def _verify_sources():
    try:
        return _verify_sources_inner()
    except (OSError, importlib.metadata.PackageNotFoundError):
        raise PolicyUnavailable('Native validator source or dependency unavailable') from None


def _active():
    operation = _CURRENT.get()
    if operation is None:
        return _verify_sources()
    operation.used = True
    operation.check()
    if operation.manifest is None:
        operation.manifest = _verify_sources()
    operation.check()
    return operation.manifest


def select_route(envelope):
    _active()
    proof, kind = envelope.validation, envelope.record.report_kind
    if envelope.schema_version != 'engineering.admission.v2' or proof.validator_id != 'veriforge.native-tool-admission':
        raise ValueError('Unsupported native validator schema/identity')
    if kind in REPORT_KINDS[:2] and proof.version == '1':
        if proof.implementation_hash == LEGACY_HASH:
            return 'legacy'
        if proof.implementation_hash == CURRENT_HASH:
            return 'current'
    if kind == REPORT_KINDS[2] and proof.version == '2' and proof.implementation_hash == CURRENT_HASH:
        return 'current'
    raise ValueError('Unsupported native validator proof/report combination')


@dataclass(frozen=True)
class LegacyReadView:
    envelope: native.AdmissionEnvelopeV2
    project_revision: records.EngineeringProjectRevision
    manifest: SystemManifest
    native_report: RecordedToolReport


def _request(envelope_bytes, artifacts):
    envelope = records.decode_admission_envelope(envelope_bytes)
    if select_route(envelope) != 'legacy':
        raise ValueError('Legacy read route required')
    supplied = {}
    for item in artifacts:
        key = records._key(item.reference)
        if key in supplied or type(item.data) is not bytes or _sha(item.data) != item.reference.sha256:
            raise ValueError('Legacy artifact bytes differ')
        supplied[key] = item
    if len(supplied) != len(envelope.artifacts):
        raise ValueError('Legacy artifact closure differs')
    size = 4 + len(envelope_bytes) + sum(meta.size_bytes for meta in envelope.artifacts)
    if size + 8 > MAX_OPERATION_BYTES:
        raise PolicyUnavailable('Legacy request exceeds operation byte bound')
    chunks = [struct.pack('!I', len(envelope_bytes)), envelope_bytes]
    identities = []
    for meta in envelope.artifacts:
        item = supplied.get(records._key(meta.reference))
        if item is None or item.reference != meta.reference or len(item.data) != meta.size_bytes:
            raise ValueError('Legacy artifact metadata differs')
        chunks.append(item.data)
        identities.append((records._key(meta.reference), _sha(item.data), len(item.data)))
    return (_sha(envelope_bytes), tuple(identities)), (envelope, supplied, b''.join(chunks))


@dataclass
class _Operation:
    deadline: float = field(default_factory=lambda: time.monotonic() + OPERATION_SECONDS)
    entries: dict = field(default_factory=dict)
    validated: dict = field(default_factory=dict)
    manifest: dict | None = None
    used: bool = False
    child_runs: int = 0

    def check(self):
        if time.monotonic() >= self.deadline:
            raise PolicyUnavailable('Native validation operation deadline exceeded')

    def queue(self, envelope_bytes, artifacts, *, page=False):
        self.check()
        key, entry = _request(envelope_bytes, artifacts)  # Always rehash caller bytes, even for a hit.
        if key in self.entries:
            return key
        if self.child_runs or len(self.entries) >= MAX_NATIVE_PAGE or 8 + sum(len(x[2]) for x in self.entries.values()) + len(entry[2]) > MAX_OPERATION_BYTES:
            if page and self.entries:
                return None
            raise PolicyUnavailable('Native validation operation capacity exceeded')
        self.entries[key] = entry
        return key

    def flush(self):
        if not self.entries or self.child_runs:
            return
        manifest = _active()
        self.check()
        remaining = min(7.5, self.deadline - time.monotonic() - 0.5)
        if remaining <= 0:
            raise PolicyUnavailable('Native validation operation deadline exceeded')
        self.child_runs += 1
        worker = HERE / '_native_validator_v1/worker.py'
        executable = manifest['runtime']['native_interpreter']['path']
        payload = struct.pack('!II', len(self.entries), max(1, int(remaining * 1000))) + b''.join(x[2] for x in self.entries.values())
        env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR') if key in os.environ}
        try:
            result = subprocess.run([executable, '-I', '-S', '-B', str(worker)], input=payload, capture_output=True,
                timeout=remaining, env=env, cwd=worker.parent, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.SubprocessError):
            raise PolicyUnavailable('Pinned legacy validation unavailable') from None
        self.check()
        expected = {'validated_envelope_sha256s':[key[0] for key in self.entries],
                    'isolated_no_site':True, 'verified_dependency_roots':True}
        if result.returncode or len(result.stdout) > 1024 or len(result.stderr) > 256:
            raise ValueError('Pinned legacy validation failed')
        if json.loads(result.stdout) != expected:
            raise ValueError('Pinned legacy validation response differs')
        for key, (envelope, supplied, _) in self.entries.items():
            def blob(ref):
                return supplied[records._key(ref)].data
            record = envelope.record
            self.validated[key] = LegacyReadView(envelope,
                records.EngineeringProjectRevision.model_validate_json(blob(record.project_revision_ref)),
                SystemManifest.model_validate_json(blob(record.manifest_ref)),
                TypeAdapter(RecordedToolReport).validate_json(blob(record.report_ref)))


@contextmanager
def validation_operation():
    existing = _CURRENT.get()
    if existing is not None:
        yield existing
        return
    operation = _Operation()
    token = _CURRENT.set(operation)
    first_failure = None
    try:
        yield operation
    except BaseException as error:
        first_failure = error
        raise
    finally:
        try:
            if operation.used:
                _verify_sources()  # End-of-operation source freshness, no persistent cache.
                operation.check()
        except BaseException as error:
            if first_failure is None:
                raise
            first_failure.add_note('Native validation final guard also failed: ' + type(error).__name__ + ': ' + str(error)[:300])
        finally:
            _CURRENT.reset(token)


def queue_legacy(envelope_bytes, artifacts, *, page=False):
    operation = _CURRENT.get()
    if operation is None:
        raise PolicyUnavailable('Native validation operation required')
    return operation.queue(envelope_bytes, artifacts, page=page)


def flush_legacy():
    operation = _CURRENT.get()
    if operation is not None:
        operation.flush()


def read_legacy(envelope_bytes, artifacts):
    with validation_operation() as operation:
        key = operation.queue(envelope_bytes, artifacts)
        operation.flush()
        return operation.validated[key]
