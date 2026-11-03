"""In-process route inspection; no HTTP server/native/runtime/DB."""
import pytest
from fastapi import HTTPException
from veriforge.api.eda_repairs import create_router
from veriforge.core import eda_repair_runtime as runtime,eda_repair_jobs as jobs

def test_default_router_is_disabled_and_id_only():
    runtime.install_runtime(None)
    routes={r.path:r.endpoint for r in create_router().routes}
    with pytest.raises(HTTPException) as exc:routes['/eda-repairs/jobs/{job_id}']('unknown')
    assert exc.value.status_code==409
    with pytest.raises(ValueError):jobs.EdaRepairRequest(project_id='p',original_version_id='o',candidate_version_id='n',resource_grant='client')
