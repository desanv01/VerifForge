"""Read-only domain views built from the existing admitted evidence boundary."""
import hashlib
import json

from . import investigations, service

MAX_PROJECTION_BYTES = 4 * 1024 * 1024


def _reference_key(reference):
    return (reference.artifact_id, reference.revision_id, reference.attempt_id, reference.sha256)


def build_projection(summary, admitted):
    """Project a service-admitted object; never admit caller-supplied evidence.

    Builder resolution uses the immutable buffers returned by admission. No
    filesystem path, tool invocation or untrusted review assertion is accepted.
    """
    from veriforge.system_investigation import native_tool_records, records
    from . import eda_investigations

    if type(admitted) not in (native_tool_records.AdmittedNativeInvestigation, records.AdmittedInvestigation, eda_investigations.AdmittedEda):
        raise ValueError('An admitted investigation is required')
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
    covered = {item['domain'] for item in projections}
    for domain in summary['domains']:
        if domain not in covered:
            gaps.append(f'No dedicated {domain} projection is bound to this admitted version; original facts remain available.')
    return dict(schema_version='engineering.projection.v1',
        project_id=summary['project_id'], investigation_id=summary['investigation_id'],
        version_id=summary['id'], content_hash=summary['content_hash'],
        scope='recorded_import', execution_status='not_run', projections=projections, gaps=gaps)


def projection_response(session, investigation_id, version_id):
    # One owned, bounded, policy-aware read validates the entire immutable graph.
    summary, admitted, _ = investigations.projection_inputs(session, investigation_id, version_id)
    try:
        data = build_projection(summary, admitted)
        encoded = json.dumps(data, sort_keys=True, separators=(',', ':'),
            ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_PROJECTION_BYTES:
            raise ValueError('Projection exceeds response bound')
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise service.Conflict('Investigation projection is unavailable or changed') from None
    # The ETag covers this representation, including builder output; the stored
    # version hash alone would not invalidate caches after a projection change.
    return encoded, hashlib.sha256(encoded).hexdigest()
