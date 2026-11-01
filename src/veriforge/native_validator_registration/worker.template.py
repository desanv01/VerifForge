"""No-site bootstrap. Validate fixed roots before importing any third-party code."""
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import sys
import time

ROOT = Path(__file__).resolve().parent
MANIFEST_SHA = '@MANIFEST_SHA@'
MAX_BYTES, MAX_RECORDS = 17 * 1048576, 4

@TREE_GUARD@


def require(value):
    if not value:
        raise ValueError('pinned legacy validation mismatch')


def main():
    require(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode)
    require('site' not in sys.modules and 'sitecustomize' not in sys.modules and 'usercustomize' not in sys.modules)
    raw = (ROOT / 'manifest.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == MANIFEST_SHA)
    manifest = json.loads(raw)
    require(platform.python_version() == manifest['runtime']['python'])
    executable = Path(sys.executable).resolve()
    require(str(executable) == manifest['runtime']['native_interpreter']['path'])
    require(hashlib.sha256(executable.read_bytes()).hexdigest() == manifest['runtime']['native_interpreter']['sha256'])
    _check_tree(ROOT/'src', {name.removeprefix('src/'):pin for name,pin in manifest['files'].items() if name.startswith('src/')}, exact=True)
    _check_tree(ROOT, {name:pin for name,pin in manifest['files'].items() if not name.startswith('src/')})
    dependencies = ROOT / 'dependencies'
    _check_tree(dependencies, manifest['dependency_files'], exact=True, limit=32*1048576)
    # Append only verified roots; do not invoke site/addsitedir or process .pth.
    sys.path.extend([str(ROOT / 'src'), str(dependencies)])
    from veriforge.system_investigation import records, native_tool_records as native
    from pydantic import TypeAdapter
    from veriforge.contracts.native_tools import RecordedToolReport
    import typing_inspection.introspection
    require(Path(native.__file__).resolve() == ROOT / 'src/veriforge/system_investigation/native_tool_records.py')
    require(Path(typing_inspection.introspection.__file__).resolve().is_relative_to(dependencies))
    reader = sys.stdin.buffer
    consumed = 0
    def exact(size):
        nonlocal consumed
        consumed += size
        require(consumed <= MAX_BYTES)
        result = reader.read(size)
        require(len(result) == size)
        return result
    count, budget_ms = struct.unpack('!II', exact(8))
    require(1 <= count <= MAX_RECORDS and 1 <= budget_ms <= 7500)
    deadline = time.monotonic() + budget_ms / 1000
    results = []
    for _ in range(count):
        require(time.monotonic() < deadline)
        size = struct.unpack('!I', exact(4))[0]
        require(0 < size <= records.MAX_ENVELOPE_BYTES)
        envelope_bytes = exact(size)
        envelope = records.decode_admission_envelope(envelope_bytes)
        require(type(envelope) is native.AdmissionEnvelopeV2 and envelope.validation.version == '1')
        require(envelope.validation.implementation_hash == manifest['files']['src/veriforge/system_investigation/native_tool_records.py'])
        artifacts, total = [], 0
        for meta in envelope.artifacts:
            total += meta.size_bytes
            require(total <= records.MAX_TOTAL_BYTES)
            artifacts.append(records.ArtifactBytes(reference=meta.reference, data=exact(meta.size_bytes)))
        report = TypeAdapter(RecordedToolReport).validate_json(next(x.data for x in artifacts if x.reference == envelope.record.report_ref))
        record = envelope.record
        authority = native.create_external_admission_authority(project_id=record.project_id, revision_id=record.revision_id,
            project_revision_ref=record.project_revision_ref, manifest_ref=record.manifest_ref,
            report_kind=record.report_kind, report_ref=record.report_ref, locators=tuple(x.locator for x in report.cases),
            validator_implementation_hash=manifest['files']['src/veriforge/system_investigation/native_tool_records.py'])
        admitted = records.read_investigation(envelope_bytes, tuple(artifacts), authority=authority)
        require(records.serialize_investigation(admitted) == envelope_bytes and time.monotonic() < deadline)
        results.append(hashlib.sha256(envelope_bytes).hexdigest())
    require(not reader.read(1))
    print(json.dumps({'validated_envelope_sha256s': results, 'isolated_no_site': True, 'verified_dependency_roots': True}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.stderr.write('Pinned legacy byte validation failed\n')
        raise SystemExit(1)
