"""EDA adaptation for the existing immutable investigation tables, no new store."""
from dataclasses import dataclass

from sqlalchemy import select

from veriforge.contracts import canonical_hash
from veriforge.contracts.eda_tools import EdaAdmission
from veriforge.system_investigation import eda_records as eda, records
from . import eda_policy
from .database import InvestigationExecutionLink


@dataclass(frozen=True)
class AdmittedEda:
    envelope: EdaAdmission
    artifacts: tuple


def decode(raw):
    return records._decode(raw, EdaAdmission, records.MAX_ENVELOPE_BYTES)


def associations(envelope):
    record = envelope.record
    revision = record.candidate.project
    return revision.project_id, revision.revision_id, canonical_hash(revision), record.requirements.sha256


def admit(record, artifacts, authority):
    configured = eda_policy.authority_for(record)
    # Only the installed host review can be used, not a caller-created identity.
    if authority is not configured:
        raise ValueError('EDA authority is not installed by the host')
    envelope = eda.admit_eda(record, artifacts, authority=configured)
    raw = eda.encode(envelope)
    if len(raw) > records.MAX_ENVELOPE_BYTES:
        raise ValueError('EDA envelope exceeds storage bound')
    return AdmittedEda(envelope, artifacts), raw


def load(session, row, envelope, blobs, artifacts):
    from .engineering_catalog import get_engineering_project
    project = get_engineering_project(session, row.project_id)
    if envelope.record.domain not in project.data['domains']:
        raise ValueError('EDA domain differs from owned project')
    authority = eda_policy.authority_for(envelope.record)
    checked = eda.readback_eda(row.content, artifacts, authority=authority)
    if any(blob.role != 'eda_evidence' or blob.media_type != 'application/octet-stream' for blob in blobs):
        raise ValueError('Stored EDA artifact role differs')
    if session.scalar(select(InvestigationExecutionLink.id).where(
            InvestigationExecutionLink.version_id == row.id).limit(1)) is not None:
        raise ValueError('Recorded EDA cannot acquire local execution links')
    return AdmittedEda(checked, artifacts), blobs


def project(admitted):
    result = eda.project_eda(admitted.envelope, admitted.artifacts,
        authority=eda_policy.authority_for(admitted.envelope.record))
    domain = result['domain']
    return dict(domain=domain,
        schema_version='vivado.projection.v1' if domain == 'vivado' else 'kicad.investigation.v1',
        data=result['projection']), result['limitations']
