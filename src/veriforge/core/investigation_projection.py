"""Read-only domain views built from the existing admitted evidence boundary."""
import hashlib
import json

from . import investigations, native_validator_policy, service

MAX_PROJECTION_BYTES = 4 * 1024 * 1024
_MAX_LEGACY_ARTIFACTS = 256
_MAX_LEGACY_BYTES = 16 * 1024 * 1024


def _reference_key(reference):
    return (reference.artifact_id, reference.revision_id, reference.attempt_id, reference.sha256)


def _projection_document(summary, projections, gaps):
    covered = {item['domain'] for item in projections}
    for domain in summary['domains']:
        if domain not in covered:
            gaps.append(f'No dedicated {domain} projection is bound to this admitted version; original facts remain available.')
    return dict(schema_version='engineering.projection.v1',
        project_id=summary['project_id'], investigation_id=summary['investigation_id'],
        version_id=summary['id'], content_hash=summary['content_hash'],
        scope='recorded_import', execution_status='not_run', projections=projections, gaps=gaps)


def _walk_references(value):
    """Yield every typed evidence reference reachable from a bounded report."""
    from pydantic import BaseModel
    from veriforge.contracts.engineering import EvidenceReference
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, EvidenceReference):
            yield item
        elif isinstance(item, BaseModel):
            pending.extend(getattr(item, name) for name in type(item).model_fields)
        elif isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, (tuple, list)):
            pending.extend(item)


class _LegacyProjectionAdapter:
    """Private adapter over one registered legacy read view and its stored blobs.

    The caller supplies the already verified version-scoped blob rows returned by
    ``investigations.projection_inputs``. Bytes are accepted only when their full
    reference identity, length, digest and stored roles match the authenticated
    envelope metadata; report references must stay inside that same envelope.
    """

    def __init__(self, summary, view, blobs):
        from veriforge.contracts.native_tools import FirmwareRecordedReport, RiscvRecordedReport
        from veriforge.system_investigation import native_tool_records

        envelope = view.envelope
        report = view.native_report
        if type(envelope) is not native_tool_records.AdmissionEnvelopeV2:
            raise ValueError('Authenticated legacy admission envelope required')
        if type(report) not in (RiscvRecordedReport, FirmwareRecordedReport):
            raise ValueError('Unsupported legacy native report')
        record = envelope.record
        if type(record) is not native_tool_records.EngineeringInvestigationV2:
            raise ValueError('Authenticated legacy native record required')
        if record.project_id != summary['project_id'] or record.revision_id != summary['revision_id']:
            raise ValueError('Legacy view differs from the owned version identity')
        if report.schema_version != record.report_kind or report.manifest_ref != record.manifest_ref:
            raise ValueError('Legacy report differs from the admitted envelope')
        revision = view.project_revision
        if revision.project_id != record.project_id or revision.revision_id != record.revision_id:
            raise ValueError('Legacy project revision differs from the admitted envelope')
        if view.manifest.revision_id != record.revision_id:
            raise ValueError('Legacy manifest differs from the admitted envelope')

        declared, total = {}, 0
        for item in envelope.artifacts:
            reference = item.reference
            key = (reference.artifact_id, reference.revision_id, reference.attempt_id)
            if key in declared:
                raise ValueError('Duplicate legacy envelope artifact identity')
            declared[key] = item
            total += item.size_bytes
        if total != envelope.total_artifact_bytes:
            raise ValueError('Legacy envelope artifact size total differs')
        if len(declared) > _MAX_LEGACY_ARTIFACTS or total > _MAX_LEGACY_BYTES:
            raise ValueError('Legacy envelope exceeds the supported bound')
        admitted = {_reference_key(item.reference) for item in declared.values()}
        for reference in _walk_references(report):
            if _reference_key(reference) not in admitted:
                raise ValueError('Legacy report reference is outside the version envelope')

        resolved, stored = {}, 0
        if type(blobs) is not tuple:
            raise ValueError('Verified legacy blob tuple required')
        for blob in blobs:
            key = (blob.artifact_id, blob.native_revision_id, blob.native_attempt_id)
            item = declared.get(key)
            if item is None:
                raise ValueError('Legacy blob is outside the admitted envelope')
            reference = item.reference
            data = blob.data
            if type(data) is not bytes or len(data) > _MAX_LEGACY_BYTES:
                raise ValueError('Legacy blob must be bounded bytes')
            if blob.media_type != 'application/octet-stream':
                raise ValueError('Legacy blob media type differs from the admitted envelope')
            if blob.sha256 != reference.sha256 or hashlib.sha256(data).hexdigest() != reference.sha256:
                raise ValueError('Legacy blob hash differs from the admitted envelope')
            if blob.size_bytes != item.size_bytes or len(data) != item.size_bytes:
                raise ValueError('Legacy blob size differs from the admitted envelope')
            if blob.role != ','.join(item.roles):
                raise ValueError('Legacy blob roles differ from the admitted envelope')
            if _reference_key(reference) in resolved:
                raise ValueError('Duplicate legacy blob identity')
            resolved[_reference_key(reference)] = data
            stored += len(data)
        if stored > total or stored > _MAX_LEGACY_BYTES:
            raise ValueError('Legacy blob bytes exceed the supported bound')
        self._declared = admitted
        self._resolved = resolved

    def resolve(self, reference):
        key = _reference_key(reference)
        if key not in self._declared:
            raise ValueError('Legacy projection reference is outside the admitted envelope')
        return self._resolved.get(key)


def _legacy_riscv_projection(report, resolve):
    from veriforge.riscv.investigation import build_riscv_investigation
    recompiled = report.recompiled_control
    cases = []
    for case in report.cases:
        original_locator = getattr(case, 'replay_of', None)
        if case.locator == recompiled.repeated:
            original_locator = recompiled.original
        original = next((other for other in report.cases
            if original_locator is not None and original_locator == other.locator), None)
        cases.append(build_riscv_investigation(case, resolve, original=original))
    return dict(domain='riscv', schema_version='riscv.investigation-set.v1',
        data=dict(cases=cases, report_kind=report.schema_version))


def _legacy_firmware_projection(report, resolve):
    from veriforge.firmware.investigation import build_firmware_investigation
    cases = [build_firmware_investigation(case, resolve, report=report) for case in report.cases]
    return dict(domain='firmware', schema_version='firmware.investigation-set.v1',
        data=dict(cases=cases, report_kind=report.schema_version))


def build_projection(summary, admitted, blobs=()):
    """Project a service-admitted object; never admit caller-supplied evidence.

    Builder resolution uses the immutable buffers returned by admission. For an
    authenticated legacy version the caller must also pass the exact verified
    version blobs returned by the same policy-aware read; a registered legacy
    view without those blobs yields only the builders' explicit partial result.
    No filesystem path, tool invocation or untrusted review assertion is accepted.
    """
    from veriforge.system_investigation import native_tool_records, records
    from . import eda_investigations

    if type(admitted) not in (native_tool_records.AdmittedNativeInvestigation,
            records.AdmittedInvestigation, eda_investigations.AdmittedEda):
        # Only the exact class captured from the registered legacy policy is
        # accepted here, and registration/source pins are rechecked for the
        # whole legacy build. A look-alike dataclass, stale policy or changed
        # source fails closed instead of becoming an alternate admission.
        if not native_validator_policy.is_native_view(admitted):
            raise ValueError('An admitted investigation is required')
        with native_validator_policy.validation_operation():
            if not native_validator_policy.is_native_view(admitted):
                raise ValueError('Registered legacy policy changed during projection')
            if native_validator_policy.select_route(admitted.envelope) != 'legacy':
                raise ValueError('Registered legacy route differs')
            adapter = _LegacyProjectionAdapter(summary, admitted, blobs)
            report = admitted.native_report
            if report.schema_version == 'riscv.recorded-report.v1':
                projection = _legacy_riscv_projection(report, adapter.resolve)
            elif report.schema_version == 'firmware.recorded-report.v1':
                projection = _legacy_firmware_projection(report, adapter.resolve)
            else:
                raise ValueError('Unsupported admitted native report')
            return _projection_document(summary, [projection], [])

    artifacts = {_reference_key(item.reference): item.data for item in admitted.artifacts}

    def resolve(reference):
        return artifacts.get(_reference_key(reference))

    projections, gaps = [], []
    if type(admitted) is eda_investigations.AdmittedEda:
        projection, limitations = eda_investigations.project(admitted)
        projections.append(projection)
        gaps.extend(limitations)
    elif type(admitted) is native_tool_records.AdmittedNativeInvestigation:
        report = admitted.native_report
        if report.schema_version in ('riscv.recorded-report.v1', 'riscv.recorded-report.v2'):
            from veriforge.riscv.investigation import build_riscv_investigation
            cases = []
            for case in admitted.native_records:
                original_locator = getattr(case, 'replay_of', None)
                recompiled = report.recompiled_control
                if case.locator == recompiled.repeated:
                    original_locator = recompiled.original
                original = next((other for other in admitted.native_records
                    if original_locator == other.locator), None)
                cases.append(build_riscv_investigation(case, resolve, original=original))
            projections.append(dict(domain='riscv', schema_version='riscv.investigation-set.v1',
                data=dict(cases=cases, report_kind=report.schema_version)))
        elif report.schema_version == 'firmware.recorded-report.v1':
            from veriforge.firmware.investigation import build_firmware_investigation
            cases = [build_firmware_investigation(case, resolve, report=report)
                for case in admitted.native_records]
            projections.append(dict(domain='firmware', schema_version='firmware.investigation-set.v1',
                data=dict(cases=cases, report_kind=report.schema_version)))
        else:
            raise ValueError('Unsupported admitted native report')
    else:
        from veriforge.system_investigation.bridge import check_register_manifest
        bridge = admitted.bridge_report
        comparisons = [row.model_dump(mode='json') for row in bridge.comparisons]
        checks = check_register_manifest(bridge) if bridge.family == 'firmware_decode' else None
        projections.append(dict(domain='system', schema_version='system.investigation.v1', data=dict(
            family=bridge.family, manifest_hash=summary['manifest_hash'],
            checker_hash=bridge.checker_hash, comparisons=comparisons,
            manifest_checks=checks, limitations=list(bridge.limitations),
            correction_status='no_candidate_rerun_evidence')))
        gaps.append('No candidate correction and affected-check rerun receipt is bound to this projection.')
        if bridge.family == 'board_connector':
            gaps.append('Board connectivity comparison does not establish physical or electrical correctness.')
    return _projection_document(summary, projections, gaps)


def projection_response(session, investigation_id, version_id):
    # One captured policy operation spans the authoritative read, the projection
    # build and response encoding; nested store/build operations reuse it, so the
    # accepted deadline and registration cannot be reset or recaptured mid-response.
    try:
        with native_validator_policy.validation_operation():
            summary, admitted, blobs = investigations.projection_inputs(session, investigation_id, version_id)
            data = build_projection(summary, admitted, blobs)
            encoded = json.dumps(data, sort_keys=True, separators=(',', ':'),
                ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(encoded) > MAX_PROJECTION_BYTES:
                raise ValueError('Projection exceeds response bound')
            # The ETag covers this representation, including builder output; the
            # stored version hash alone would not invalidate caches after a
            # projection change.
            digest = hashlib.sha256(encoded).hexdigest()
    except (ValueError, TypeError, KeyError, UnicodeError):
        # Includes the captured operation's final source/deadline PolicyUnavailable.
        raise service.Conflict('Investigation projection is unavailable or changed') from None
    return encoded, digest
