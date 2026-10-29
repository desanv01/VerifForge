"""Owned immutable recorded imports. Never dispatches or publishes a DUT verdict.

Only trusted service code supplies admission authority; there is no public write
route. Every detail/download re-admits the exact stored envelope and blob graph.
"""
from datetime import datetime, timezone
import hashlib
import json

from sqlalchemy import func, select, update

from veriforge.contracts import AttemptResult, canonical_hash
from veriforge.contracts.engineering import EvidenceReference
from veriforge.system_investigation import records
from . import service
from .database import (Attempt, Investigation, InvestigationArtifact, InvestigationExecutionLink,
    InvestigationVersion, Project)
from .planner_experiments import resolve_attempt_experiment


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _key(reference):
    return reference.artifact_id, reference.revision_id, reference.attempt_id


def _identity(reference):
    return _digest(json.dumps(_key(reference), separators=(',', ':')).encode())


def _at(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('Investigation service time must be timezone-aware')
    return value.astimezone(timezone.utc)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def _db_context(session, project_id, binding, native):
    campaign = service.owned(session, binding.campaign_id)
    attempt = session.get(Attempt, binding.db_attempt_id)
    if campaign.project_id != project_id or attempt is None or attempt.campaign_id != campaign.id:
        raise ValueError('DB campaign/attempt/project association differs')
    experiment = resolve_attempt_experiment(session, attempt)
    if (experiment.id != binding.experiment_record_id or experiment.wire_id != binding.wire_experiment_id or
        experiment.plan['experiment_id'] != binding.wire_experiment_id or attempt.result is None):
        raise ValueError('DB immutable experiment association differs')
    result = AttemptResult.model_validate(attempt.result)
    if (result != native or result.attempt_id != binding.native_attempt_id or
        result.experiment_id != binding.wire_experiment_id or attempt.execution_status != result.execution_status or
        attempt.verdict != result.verdict or attempt.execution_status not in service.TERMINAL):
        raise ValueError('DB finalized result differs from native evidence')
    if result.identity is not None and any(getattr(result.identity, key) != value for key, value in experiment.identity.items()):
        raise ValueError('DB result immutable provenance differs')
    return experiment, canonical_hash(result)


def _origin(session, binding, native, *, artifact_root):
    """A narrow admission-time observation; lack of retained bytes stays context."""
    from . import artifacts as execution_artifacts
    try:
        directory, _, result, _ = execution_artifacts.attempt_snapshot(session, binding.db_attempt_id,
            root=artifact_root, checked_required=True)
        raw = execution_artifacts.snapshot_bytes(execution_artifacts.contained(directory, 'result.json'),
            records.MAX_ARTIFACT_BYTES, sha256=binding.result_ref.sha256)
        if AttemptResult.model_validate_json(raw) != native or result != native:
            return 'context_only'
        for reference in native.artifacts:
            execution_artifacts.snapshot_bytes(execution_artifacts.contained(directory, reference.path),
                records.MAX_ARTIFACT_BYTES, sha256=reference.sha256, size=reference.size_bytes)
        return 'retained_execution_bytes'
    except (service.Conflict, ValueError, OSError):
        return 'context_only'


def _project(session, project_id):
    row = session.get(Project, project_id)
    if row is None or row.owner != 'local':
        raise service.NotFound(project_id)
    return row


def owned(session, investigation_id):
    row = session.get(Investigation, investigation_id)
    if row is None or row.owner != 'local':
        raise service.NotFound(investigation_id)
    _project(session, row.project_id)
    return row


def _version(session, investigation_id, version_id):
    parent = owned(session, investigation_id)
    row = session.get(InvestigationVersion, version_id)
    if row is None or row.investigation_id != parent.id or row.project_id != parent.project_id:
        raise service.NotFound(version_id)
    return row


def _envelope(row):
    if _digest(row.content) != row.content_hash or len(row.content) > records.MAX_ENVELOPE_BYTES:
        raise ValueError('Stored envelope hash differs')
    envelope = records.AdmissionEnvelope.model_validate_json(row.content)
    record = envelope.record
    if (row.schema_version != envelope.schema_version or row.project_id != record.project_id or
        row.revision_id != record.revision_id or row.revision_hash != record.project_revision_ref.sha256 or
        row.manifest_hash != record.manifest_ref.sha256 or row.scope != 'recorded_import' or row.execution_status != 'not_run'):
        raise ValueError('Stored envelope association differs')
    return envelope


def _summary(row):
    try:
        envelope = _envelope(row)
    except (ValueError, TypeError):
        raise service.Conflict('Investigation evidence is unavailable or changed') from None
    return dict(id=row.id, investigation_id=row.investigation_id, project_id=row.project_id,
        ordinal=row.ordinal, content_hash=row.content_hash, revision_id=row.revision_id,
        revision_hash=row.revision_hash, manifest_hash=row.manifest_hash, schema_version=row.schema_version,
        scope=row.scope, execution_status=row.execution_status, created_at=_utc(row.created_at),
        domains=sorted({item.domain for item in envelope.record.native_records}),
        native_investigation_id=envelope.record.investigation_id)


def _load(session, row):
    try:
        envelope = _envelope(row)
        blobs = list(session.scalars(select(InvestigationArtifact).where(InvestigationArtifact.version_id == row.id).order_by(InvestigationArtifact.id)))
        if len(blobs) > records.MAX_ARTIFACTS or sum(blob.size_bytes for blob in blobs) > records.MAX_TOTAL_BYTES:
            raise ValueError('Stored artifact quota differs')
        artifacts = []
        for blob in blobs:
            reference = EvidenceReference(artifact_id=blob.artifact_id, revision_id=blob.native_revision_id,
                attempt_id=blob.native_attempt_id, sha256=blob.sha256)
            if len(blob.data) != blob.size_bytes or blob.identity_key != _identity(reference):
                raise ValueError('Stored artifact metadata differs')
            artifacts.append(records.ArtifactBytes(reference=reference, data=blob.data))
        record = envelope.record
        authority = records.create_admission_authority(project_id=record.project_id, revision_id=record.revision_id,
            project_revision_ref=record.project_revision_ref, manifest_ref=record.manifest_ref,
            bridge_report_ref=record.bridge_report_ref, attempts=record.attempts)
        admitted = records.read_investigation(row.content, tuple(artifacts), authority=authority)
        verified = {_key(item.reference): item for item in admitted.envelope.artifacts}
        for blob in blobs:
            item = verified[(blob.artifact_id, blob.native_revision_id, blob.native_attempt_id)]
            if blob.role != ','.join(item.roles) or blob.media_type != 'application/octet-stream':
                raise ValueError('Stored artifact role differs')
        links = list(session.scalars(select(InvestigationExecutionLink).where(InvestigationExecutionLink.version_id == row.id)))
        if len(links) != len(record.attempts):
            raise ValueError('Stored DB evidence link coverage differs')
        links = {item.attempt_id: item for item in links}
        for binding, native in zip(record.attempts, admitted.attempt_results):
            experiment, result_hash = _db_context(session, row.project_id, binding, native)
            link = links.get(binding.db_attempt_id)
            if (link is None or link.project_id != row.project_id or link.campaign_id != binding.campaign_id or
                link.experiment_id != experiment.id or link.result_hash != result_hash or _digest(link.content) != link.association_hash):
                raise ValueError('Stored DB evidence link differs')
            context = json.loads(link.content)
            if context != dict(binding=binding.model_dump(mode='json'), origin_at_admission=context.get('origin_at_admission')) or context['origin_at_admission'] not in {'context_only', 'retained_execution_bytes'}:
                raise ValueError('Stored DB evidence context differs')
        return admitted, blobs
    except (ValueError, TypeError, KeyError):
        raise service.Conflict('Investigation evidence is unavailable or changed') from None


def persist_investigation(session, record, artifacts, *, authority, idempotency_key, at, investigation_id=None, artifact_root=None):
    """Re-admit raw inputs under host authority in one caller-owned transaction.

    Existing curated project only. Canonical envelope binds every exact blob.
    Passing a precomputed AdmittedInvestigation is not a supported shortcut.
    """
    at = _at(at)
    if type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 128:
        raise ValueError('Bounded idempotency key required')
    admitted = records.admit_investigation(record, artifacts, authority=authority)
    record = admitted.envelope.record
    content = records.serialize_investigation(admitted)
    digest = _digest(content)
    project_id = admitted.envelope.record.project_id
    _project(session, project_id)
    # Serializes append/idempotency writers without mutating immutable history.
    session.execute(update(Project).where(Project.id == project_id, Project.owner == 'local').values(id=Project.id))
    session.expire_all()
    contexts = []
    try:
        for binding, native in zip(admitted.envelope.record.attempts, admitted.attempt_results):
            service.lock(session, binding.campaign_id)
            experiment, result_hash = _db_context(session, project_id, binding, native)
            contexts.append((binding, experiment.id, result_hash))
    except (ValueError, TypeError):
        raise service.Conflict('DB evidence association is unavailable or differs') from None
    if investigation_id is None:
        parent = session.scalar(select(Investigation).where(Investigation.owner == 'local', Investigation.create_key == idempotency_key))
        if parent is not None:
            if parent.project_id != project_id or parent.request_hash != digest:
                raise service.Conflict('Idempotency key already identifies different investigation content')
            row = session.scalar(select(InvestigationVersion).where(InvestigationVersion.investigation_id == parent.id, InvestigationVersion.ordinal == 1))
            _load(session, row)
            return _summary(row)
    else:
        parent = owned(session, investigation_id)
        if parent.project_id != project_id:
            raise service.Conflict('Investigation belongs to another project')
        first = session.scalar(select(InvestigationVersion).where(InvestigationVersion.investigation_id == parent.id, InvestigationVersion.ordinal == 1))
        if _envelope(first).record.investigation_id != admitted.envelope.record.investigation_id:
            raise service.Conflict('Native investigation identity changed')
        prior = session.scalar(select(InvestigationVersion).where(InvestigationVersion.investigation_id == parent.id, InvestigationVersion.admission_key == idempotency_key))
        if prior is not None:
            if prior.content_hash != digest:
                raise service.Conflict('Idempotency key already identifies different version content')
            _load(session, prior)
            return _summary(prior)
    if parent is None:
        parent = Investigation(project_id=project_id, create_key=idempotency_key, request_hash=digest, created_at=at)
        session.add(parent)
        session.flush()
    ordinal = (session.scalar(select(func.max(InvestigationVersion.ordinal)).where(InvestigationVersion.investigation_id == parent.id)) or 0) + 1
    row = InvestigationVersion(investigation_id=parent.id, project_id=project_id, ordinal=ordinal,
        admission_key=idempotency_key, schema_version=admitted.envelope.schema_version,
        revision_id=record.revision_id, revision_hash=record.project_revision_ref.sha256,
        manifest_hash=record.manifest_ref.sha256, content_hash=digest, content=content, created_at=at)
    session.add(row)
    session.flush()
    for (binding, experiment_id, result_hash), native in zip(contexts, admitted.attempt_results):
        context = _encode(dict(binding=binding.model_dump(mode='json'),
            origin_at_admission=_origin(session, binding, native, artifact_root=artifact_root)))
        session.add(InvestigationExecutionLink(version_id=row.id, project_id=project_id, campaign_id=binding.campaign_id,
            attempt_id=binding.db_attempt_id, experiment_id=experiment_id, result_hash=result_hash,
            association_hash=_digest(context), content=context))
    metadata = {_key(item.reference): item for item in admitted.envelope.artifacts}
    for artifact in admitted.artifacts:
        reference = artifact.reference
        session.add(InvestigationArtifact(version_id=row.id, artifact_id=reference.artifact_id,
            native_revision_id=reference.revision_id, native_attempt_id=reference.attempt_id,
            identity_key=_identity(reference), sha256=reference.sha256, size_bytes=len(artifact.data),
            media_type='application/octet-stream', role=','.join(metadata[_key(reference)].roles), data=artifact.data))
    session.flush()
    return _summary(row)


def list_investigations(session, project_id, *, after='', limit=20):
    _project(session, project_id)
    rows = session.scalars(select(Investigation).where(Investigation.owner == 'local', Investigation.project_id == project_id,
        Investigation.id > after).order_by(Investigation.id).limit(limit + 1)).all()
    items = [investigation_view(session, row.id) for row in rows[:limit]]
    return dict(items=items, next_cursor=rows[limit - 1].id if len(rows) > limit else None)


def investigation_view(session, investigation_id):
    parent = owned(session, investigation_id)
    latest = session.scalar(select(InvestigationVersion).where(InvestigationVersion.investigation_id == parent.id).order_by(InvestigationVersion.ordinal.desc()).limit(1))
    if latest is None:
        raise service.Conflict('Investigation has no admitted version')
    return dict(id=parent.id, project_id=parent.project_id, created_at=_utc(parent.created_at), latest_version=_summary(latest))


def list_versions(session, investigation_id, *, after=0, limit=20):
    owned(session, investigation_id)
    rows = session.scalars(select(InvestigationVersion).where(InvestigationVersion.investigation_id == investigation_id,
        InvestigationVersion.ordinal > after).order_by(InvestigationVersion.ordinal).limit(limit + 1)).all()
    return dict(items=[_summary(row) for row in rows[:limit]], next_cursor=rows[limit - 1].ordinal if len(rows) > limit else None)


def version_view(session, investigation_id, version_id):
    row = _version(session, investigation_id, version_id)
    admitted, _ = _load(session, row)
    links = session.scalars(select(InvestigationExecutionLink).where(InvestigationExecutionLink.version_id == row.id)).all()
    return {**_summary(row), 'envelope': admitted.envelope.model_dump(mode='json'),
        'execution_contexts': [json.loads(link.content) for link in links],
        'project_revision': admitted.project_revision.model_dump(mode='json'),
        'manifest': admitted.manifest.model_dump(mode='json'), 'bridge_report': admitted.bridge_report.model_dump(mode='json')}


def artifact_list(session, investigation_id, version_id):
    _, blobs = _load(session, _version(session, investigation_id, version_id))
    return dict(items=[dict(id=blob.id, artifact_id=blob.artifact_id, revision_id=blob.native_revision_id,
        attempt_id=blob.native_attempt_id, sha256=blob.sha256, size_bytes=blob.size_bytes,
        media_type=blob.media_type, roles=blob.role.split(',')) for blob in blobs])


def artifact_download(session, investigation_id, version_id, artifact_id):
    _, blobs = _load(session, _version(session, investigation_id, version_id))
    blob = next((item for item in blobs if item.id == artifact_id), None)
    if blob is None:
        raise service.NotFound(artifact_id)
    return blob.data, blob.sha256
