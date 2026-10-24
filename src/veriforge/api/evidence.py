"""Evidence URLs accept database identifiers only, never local paths."""
from fastapi import APIRouter, Header
from fastapi.responses import Response
from typing import Annotated
from sqlalchemy.exc import IntegrityError

from veriforge.core import artifacts, service

Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def evidence_router(sessions, root=None):
    router = APIRouter(prefix="/api")

    @router.get("/attempts/{attempt_id}/artifacts")
    def artifact_list(attempt_id: str):
        with sessions() as session:
            return artifacts.artifact_list(session, attempt_id, root=root)

    @router.get("/attempts/{attempt_id}/artifacts/{artifact_id}/download")
    def artifact_download(attempt_id: str, artifact_id: str):
        with sessions() as session:
            data, ref = artifacts.artifact_download(session, attempt_id, artifact_id, root=root)
        # Bytes are already bounded and hashed; never reopen a path after checking.
        return Response(data, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{artifact_id}.bin"', "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @router.post("/attempts/{attempt_id}/replay", status_code=202)
    def replay(attempt_id: str, idempotency_key: Key):
        for retry in range(2):
            try:
                with sessions.begin() as session:
                    row = artifacts.replay_campaign(session, attempt_id, idempotency_key, root=root)
                    session.flush()
                    return service.campaign_view(session, row)
            except IntegrityError:
                if retry:
                    raise

    @router.post("/attempts/{attempt_id}/exports", status_code=202)
    def request_export(attempt_id: str, idempotency_key: Key):
        for retry in range(2):
            try:
                with sessions.begin() as session:
                    return artifacts.export_view(artifacts.request_export(session, attempt_id, idempotency_key))
            except IntegrityError:
                if retry:
                    raise

    @router.get("/exports/{export_id}")
    def export_status(export_id: str):
        with sessions() as session:
            return artifacts.export_view(artifacts.owned_export(session, export_id))

    @router.post("/exports/{export_id}/cancel")
    def export_cancel(export_id: str, idempotency_key: Key):
        with sessions.begin() as session:
            return artifacts.export_view(artifacts.cancel_export(session, export_id))

    @router.get("/exports/{export_id}/download")
    def export_download(export_id: str):
        with sessions() as session:
            data = artifacts.export_download(session, export_id, root=root)
        return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="veriforge-{export_id}.zip"', "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    return router
