"""Read-only views of server-admitted recorded evidence. No execution routes."""
from fastapi import APIRouter, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from veriforge.core import investigations


def investigations_router(sessions):
    router = APIRouter(prefix='/api')

    @router.get('/projects/{project_id}/investigations')
    def project_investigations(project_id: str, after: str = Query('', max_length=128), limit: int = Query(20, ge=1, le=50)):
        with sessions() as session:
            return investigations.list_investigations(session, project_id, after=after, limit=limit)

    @router.get('/investigations/{investigation_id}')
    def investigation(investigation_id: str):
        with sessions() as session:
            return investigations.investigation_view(session, investigation_id)

    @router.get('/investigations/{investigation_id}/versions')
    def versions(investigation_id: str, after: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50)):
        with sessions() as session:
            return investigations.list_versions(session, investigation_id, after=after, limit=limit)

    @router.get('/investigations/{investigation_id}/versions/{version_id}')
    def version(investigation_id: str, version_id: str, request: Request):
        with sessions() as session:
            data = investigations.version_view(session, investigation_id, version_id)
        etag = '"' + data['content_hash'] + '"'
        headers = {'ETag': etag, 'Cache-Control': 'private, no-cache', 'X-Content-Type-Options': 'nosniff'}
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(jsonable_encoder(data), headers=headers)

    @router.get('/investigations/{investigation_id}/versions/{version_id}/artifacts')
    def artifacts(investigation_id: str, version_id: str):
        with sessions() as session:
            return investigations.artifact_list(session, investigation_id, version_id)

    @router.get('/investigations/{investigation_id}/versions/{version_id}/artifacts/{artifact_id}/download')
    def download(investigation_id: str, version_id: str, artifact_id: str):
        with sessions() as session:
            data, digest = investigations.artifact_download(session, investigation_id, version_id, artifact_id)
        # The filename comes from a digest of admitted bytes, never route/input text.
        return Response(data, media_type='application/octet-stream', headers={
            'Content-Disposition': f'attachment; filename="{digest}.bin"',
            'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})

    return router
