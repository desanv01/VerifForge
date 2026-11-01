"""DB-authorized evidence access. Client identifiers never become filesystem paths."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path

from sqlalchemy import or_, select, update

from veriforge.contracts import AttemptResult
from veriforge.adapters import get_adapter
from .families import experiment_adapter
from .planner_experiments import resolve_attempt_experiment
from veriforge.evidence import ExportLimits, export_bundle, load_attempt
from . import service
from .database import Attempt, Campaign, EvidenceExport, ReplayLink, identifier, now

LIMITS = ExportLimits()
EVIDENCE_ADAPTERS = frozenset({"rtl.fifo.v1", "rtl.arithmetic.v1", "rtl.axi_lite.v1"})


def storage_root(root=None) -> Path:
    return Path(root or os.getenv("VERIFORGE_ARTIFACT_ROOT", "artifacts")).absolute()


def no_links(path: Path):
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError("Linked evidence paths are not allowed")


def contained(root: Path, *parts: str) -> Path:
    no_links(root)
    path = root.joinpath(*parts)
    no_links(path)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Evidence escaped storage boundary")
    return path


def snapshot_bytes(path: Path, limit: int, *, sha256=None, size=None) -> bytes:
    no_links(path)
    if not path.is_file() or (size is not None and path.stat().st_size != size):
        raise ValueError("Evidence missing or size differs")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit or (size is not None and len(data) != size):
        raise ValueError("Evidence size exceeds bound")
    if sha256 is not None and hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("Evidence hash differs")
    return data


def owned_attempt(session, attempt_id):
    attempt = session.get(Attempt, attempt_id)
    if attempt is None:
        raise service.NotFound(attempt_id)
    campaign = service.owned(session, attempt.campaign_id)
    if campaign.adapter_id not in EVIDENCE_ADAPTERS:
        raise service.Conflict("Evidence operations unsupported for this adapter")
    return attempt, campaign


def verify_replay_output(directory, experiment, result):
    """Validate fresh replay files before accepting a returned runner record."""
    directory = Path(directory).absolute()
    if AttemptResult.model_validate_json(snapshot_bytes(contained(directory, "result.json"), LIMITS.max_bytes)) != result:
        raise ValueError("Fresh replay result differs from runner record")
    descriptor = experiment_adapter(experiment)
    if descriptor.validate_experiment(json.loads(snapshot_bytes(contained(directory, "experiment.json"), LIMITS.max_bytes))) != experiment:
        raise ValueError("Fresh replay experiment differs from submitted plan")
    if len(result.artifacts) > LIMITS.max_files or sum(ref.size_bytes for ref in result.artifacts) > LIMITS.max_bytes:
        raise ValueError("Fresh replay evidence exceeds bound")
    if len({a.path for a in result.artifacts}) != len(result.artifacts) or len({a.artifact_id for a in result.artifacts}) != len(result.artifacts):
        raise ValueError("Ambiguous replay artifact identity")
    for ref in result.artifacts:
        snapshot_bytes(contained(directory, ref.path), LIMITS.max_bytes, sha256=ref.sha256, size=ref.size_bytes)
    if result.identity is not None:
        load_attempt(directory, limits=LIMITS)
        verify_stimulus(directory, experiment, result)


def verify_stimulus(directory, experiment, result):
    descriptor = experiment_adapter(experiment)
    ref = next((item for item in result.artifacts if item.path == "stimulus.json"), None)
    if ref is None:
        raise ValueError("Exact replay requires verified stored stimulus")
    raw = snapshot_bytes(contained(directory, ref.path), LIMITS.max_bytes, sha256=ref.sha256, size=ref.size_bytes)
    # Canonical JSON preserves bool/int/float distinctions that Python container
    # equality would otherwise erase (True == 1). Exact replay must not coerce.
    if service.digest(json.loads(raw)) != service.digest(descriptor.expand_stimulus(experiment)):
        raise ValueError("Stored stimulus differs from exact typed experiment")


def attempt_snapshot(session, attempt_id: str, *, root=None, checked_required=False):
    attempt, campaign = owned_attempt(session, attempt_id)
    if attempt.execution_status != "completed" or attempt.result is None:
        raise service.Conflict("Only finalized completed evidence is available")
    try:
        record = resolve_attempt_experiment(session, attempt)
        directory = contained(storage_root(root), attempt.id)  # DB ID, never runner result ID
        result = AttemptResult.model_validate(attempt.result)
        on_disk = AttemptResult.model_validate_json(snapshot_bytes(contained(directory, "result.json"), LIMITS.max_bytes))
        experiment = get_adapter(record.adapter_id).validate_experiment(json.loads(snapshot_bytes(contained(directory, "experiment.json"), LIMITS.max_bytes)))
        if result != on_disk or experiment.model_dump(mode="json") != get_adapter(record.adapter_id).validate_experiment(record.plan).model_dump(mode="json"):
            raise ValueError("Disk records differ from durable execution record")
        if result.experiment_id != experiment.experiment_id:
            raise ValueError("Experiment association differs")
        if len(result.artifacts) > LIMITS.max_files or sum(ref.size_bytes for ref in result.artifacts) > LIMITS.max_bytes:
            raise ValueError("Artifact limits exceeded")
        if len({a.path for a in result.artifacts}) != len(result.artifacts) or len({a.artifact_id for a in result.artifacts}) != len(result.artifacts):
            raise ValueError("Ambiguous artifact identity")
        for ref in result.artifacts:
            snapshot_bytes(contained(directory, ref.path), LIMITS.max_bytes, sha256=ref.sha256, size=ref.size_bytes)
        if result.identity is not None:
            if any(getattr(result.identity, key) != value for key, value in record.identity.items()):
                raise ValueError("Result provenance differs from immutable campaign")
            load_attempt(directory, limits=LIMITS)
        elif checked_required:
            raise ValueError("No checked evidence identity")
        return directory, experiment, result, campaign
    except (ValueError, OSError) as error:
        raise service.Conflict("Evidence is unavailable, unsafe, changed, or exceeds bounds") from error


def artifact_list(session, attempt_id, *, root=None):
    _, experiment, result, _ = attempt_snapshot(session, attempt_id, root=root)
    portable = experiment_adapter(experiment).portable_artifacts
    return {"items": [{**ref.model_dump(mode="json"), "downloadable": result.identity is not None and ref.path in portable} for ref in result.artifacts]}


def artifact_download(session, attempt_id, artifact_id, *, root=None):
    directory, experiment, result, _ = attempt_snapshot(session, attempt_id, root=root, checked_required=True)
    ref = next((item for item in result.artifacts if item.artifact_id == artifact_id), None)
    if ref is None:
        raise service.NotFound(artifact_id)
    if ref.path not in experiment_adapter(experiment).portable_artifacts:
        raise service.NotFound(artifact_id)
    from veriforge.evidence import portable_artifact_bytes
    try:
        data = portable_artifact_bytes(directory, artifact_id, limits=LIMITS)
        # Verify the actual response bytes against immutable DB metadata, too.
        if len(data) != ref.size_bytes or hashlib.sha256(data).hexdigest() != ref.sha256:
            raise ValueError("Snapshot mismatch")
        return data, ref
    except (ValueError, OSError) as error:
        raise service.Conflict("Artifact unavailable or unsafe for download") from error


def replay_campaign(session, attempt_id, key, *, root=None):
    owned_attempt(session, attempt_id)
    prior = session.scalar(select(ReplayLink).where(ReplayLink.owner == "local", ReplayLink.request_key == key))
    if prior:
        if prior.source_attempt_id != attempt_id:
            raise service.Conflict("Replay key already used for another attempt")
        return service.owned(session, prior.campaign_id)
    directory, experiment, result, original = attempt_snapshot(session, attempt_id, root=root, checked_required=True)
    source_record = resolve_attempt_experiment(session, session.get(Attempt, attempt_id))
    from .worker import installed_identity
    try:
        verify_stimulus(directory, experiment, result)
        if installed_identity(experiment) != source_record.identity:
            raise ValueError("Installed execution provenance differs")
    except (ValueError, OSError, KeyError) as error:
        raise service.Conflict("Installed source/checker/contract changed; exact replay unavailable") from error
    # ReplayLink owns public replay idempotency. Never derive a campaign-create
    # key from client input: that namespace is also accepted by POST /campaigns.
    # require_new is an explicit guard even against an internal key collision.
    row = service.create_campaign(session, original.project_id, source_record.plan, "replay:" + identifier(), require_new=True, adapter_id=source_record.adapter_id, experiment_origin="replay")
    if row.identity != source_record.identity:
        raise service.Conflict("Current catalog provenance differs from original evidence")
    session.add(ReplayLink(campaign_id=row.id, source_attempt_id=attempt_id, request_key=key))
    service.emit(session, row, "campaign.replay_requested", {"source_attempt_id": attempt_id})
    session.flush()
    return service.start(session, row.id)


def request_export(session, attempt_id, key):
    attempt, _ = owned_attempt(session, attempt_id)
    prior = session.scalar(select(EvidenceExport).where(EvidenceExport.owner == "local", EvidenceExport.request_key == key))
    if prior:
        if prior.attempt_id != attempt_id:
            raise service.Conflict("Export key already used for another attempt")
        return prior
    if attempt.execution_status != "completed" or not attempt.result or not attempt.result.get("identity"):
        raise service.Conflict("Export requires finalized checked evidence")
    row = EvidenceExport(attempt_id=attempt_id, request_key=key)
    session.add(row)
    session.flush()
    return row


def owned_export(session, export_id):
    row = session.get(EvidenceExport, export_id)
    if not row or row.owner != "local":
        raise service.NotFound(export_id)
    owned_attempt(session, row.attempt_id)
    return row


def export_view(row):
    return {"id": row.id, "attempt_id": row.attempt_id, "status": row.status, "error_type": row.error_type, "sha256": row.sha256, "size_bytes": row.size_bytes}


def export_path(row, root=None):
    return contained(storage_root(root), "exports", row.id, row.lease + ".zip")


def export_download(session, export_id, *, root=None):
    row = owned_export(session, export_id)
    if row.status != "completed" or not row.sha256 or row.size_bytes is None:
        raise service.Conflict("Export is not complete")
    try:
        return snapshot_bytes(export_path(row, root), LIMITS.max_bytes, sha256=row.sha256, size=row.size_bytes)
    except (ValueError, OSError) as error:
        raise service.Conflict("Export unavailable or changed") from error


def cancel_export(session, export_id):
    row = owned_export(session, export_id)
    session.execute(update(EvidenceExport).where(EvidenceExport.id == row.id, EvidenceExport.status.in_(["queued", "running"])).values(status="cancelled", lease=identifier(), finished_at=now()))
    session.refresh(row)
    return row


def dispatch_exports(sessions, dispatcher):
    sent = failed = 0
    with sessions() as session:
        ids = list(session.scalars(select(EvidenceExport.id).where(EvidenceExport.status == "queued", or_(EvidenceExport.sent_at.is_(None), EvidenceExport.sent_at < now() - timedelta(seconds=60))).limit(100)))
    for export_id in ids:
        try:
            dispatcher.enqueue_export(export_id)
        except Exception as error:
            with sessions.begin() as session:
                owned_export(session, export_id).error_type = type(error).__name__
            failed += 1
        else:
            with sessions.begin() as session:
                row = owned_export(session, export_id)
                row.sent_at, row.error_type = now(), None
            sent += 1
    return {"sent": sent, "failed": failed}


def recover_exports(session):
    # Export is bounded to 60 seconds by RQ. Revocation prevents late publication.
    return session.execute(update(EvidenceExport).where(EvidenceExport.status == "running", EvidenceExport.started_at < now() - timedelta(seconds=120)).values(status="failed", lease=identifier(), error_type="WorkerInterrupted", finished_at=now())).rowcount


def execute_export(export_id, *, sessions=None, root=None):
    from .database import make_engine, session_factory
    sessions = sessions or session_factory(make_engine())
    with sessions.begin() as session:
        # Atomic claim independently guards duplicate RQ delivery.
        claimed = session.execute(update(EvidenceExport).where(EvidenceExport.id == export_id, EvidenceExport.status == "queued").values(status="running", started_at=now()))
        if not claimed.rowcount:
            return {"accepted": False}
        row = owned_export(session, export_id)
        attempt_id, lease = row.attempt_id, row.lease
    destination = None
    staged = None
    files = {}
    try:
        destination = export_path(row, root)
        with sessions() as session:
            directory, experiment, result, _ = attempt_snapshot(session, attempt_id, root=root, checked_required=True)
        # Export only from a private verified byte snapshot. An evidence-directory
        # swap after validation must not replace DB-authorized bytes in the bundle.
        staged = destination.parent / (lease + "-input")
        no_links(staged)
        staged.mkdir(parents=True, exist_ok=False)
        experiment_bytes = snapshot_bytes(contained(directory, "experiment.json"), LIMITS.max_bytes)
        descriptor = experiment_adapter(experiment)
        if descriptor.validate_experiment(json.loads(experiment_bytes)) != experiment:
            raise ValueError("Experiment changed during snapshot")
        files = {"experiment.json": experiment_bytes, "result.json": result.model_dump_json().encode()}
        for ref in result.artifacts:
            if ref.path == "result.json":
                raise ValueError("Self-referential evidence record")
            files[ref.path] = snapshot_bytes(contained(directory, ref.path), LIMITS.max_bytes, sha256=ref.sha256, size=ref.size_bytes)
        if descriptor.validate_experiment(json.loads(files["experiment.json"])) != experiment:
            raise ValueError("Experiment snapshot differs")
        for relative, data in files.items():
            target = contained(staged, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        destination.parent.mkdir(parents=True, exist_ok=True)
        no_links(destination)
        export_bundle(staged, destination, limits=LIMITS)
        data = snapshot_bytes(destination, LIMITS.max_bytes)
        with sessions.begin() as session:
            published = session.execute(update(EvidenceExport).where(EvidenceExport.id == export_id, EvidenceExport.lease == lease, EvidenceExport.status == "running").values(status="completed", sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), error_type=None, finished_at=now()))
            if not published.rowcount:
                no_links(destination)
                destination.unlink(missing_ok=True)
            return {"accepted": bool(published.rowcount)}
    except Exception as error:
        if destination is not None:
            try:
                no_links(destination)
                destination.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass  # unpublishable partial bytes remain private; no unsafe cleanup
        with sessions.begin() as session:
            session.execute(update(EvidenceExport).where(EvidenceExport.id == export_id, EvidenceExport.lease == lease, EvidenceExport.status == "running").values(status="failed", error_type=type(error).__name__, finished_at=now()))
        return {"accepted": True, "failed": True}
    finally:
        # Remove only known files from this job's server-chosen private snapshot.
        # Never recursively traverse an evidence tree or follow replacement links.
        if staged is not None:
            try:
                no_links(staged)
                directories = {staged}
                for relative in files:
                    path = contained(staged, relative)
                    path.unlink(missing_ok=True)
                    directories.update(p for p in path.parents if p == staged or p.is_relative_to(staged))
                for path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
                    path.rmdir()
            except (OSError, ValueError):
                pass  # storage interference leaves private data, never public bytes
