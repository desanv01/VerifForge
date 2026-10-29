"""Independent final receipt correction review; synthetic files, no native jobs."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[2]


def require(value,message):
    if not value:raise AssertionError(message)


def sha(data):return hashlib.sha256(data).hexdigest()


def decode(data):return json.loads(data)


@pytest.fixture
def controller(tmp_path,monkeypatch):
    import platform
    monkeypatch.setattr(platform,'machine',lambda:'AMD64')
    def forbidden(*a,**kw):pytest.fail('review cannot launch an external operation')
    for name in ('run','Popen','check_output'):monkeypatch.setattr(subprocess,name,forbidden)
    path=ROOT/'scripts/ci_axi_application.py'
    assert sha(path.read_bytes())=='459bde8bfec12b40c3ffd856ba825c7896f62d2b5b0031c5d853dfedccb43987'
    spec=importlib.util.spec_from_file_location('independent_final_receipt_review',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'OUT',tmp_path)
    monkeypatch.setattr(module,'cleanup',lambda *a:None)
    return module


def assert_exact_files(controller,summary):
    raw=(controller.OUT/'summary.json').read_bytes()
    assert json.loads(raw)==summary and raw.endswith(b'\n') and b'\r\n' not in raw
    actual=sum(p.stat().st_size for p in controller.OUT.rglob('*') if p.is_file())
    assert summary['evidence_bytes']==actual
    return actual


def test_actual_61_byte_failure_is_preserved_as_data_and_corrected_by_finalization(controller):
    summary=decode(ACTUAL_FAILED_SUMMARY)
    missing={k:v for k,v in summary.items() if k!='error'}
    assert len(ACTUAL_FAILED_SUMMARY)==885
    assert len(ACTUAL_FAILED_SUMMARY)-len((json.dumps(missing,indent=2,sort_keys=True)+'\n').encode())==61
    assert summary['evidence_bytes']==58282
    retained=58343-len(ACTUAL_FAILED_SUMMARY)
    evidence=b'SYNTHETIC-OTHER-FILES'+b'x'*(retained-len(b'SYNTHETIC-OTHER-FILES'))
    (controller.OUT/'evidence.dat').write_bytes(evidence)
    (controller.OUT/'summary.json').write_bytes(ACTUAL_FAILED_SUMMARY)
    primary=RuntimeError('failed stage: qualify-migrations')
    returned=controller.finish_owned(summary['project'],controller.OUT,summary,primary)
    assert returned is primary and summary['status']=='failed_or_cancelled'
    assert summary['error']=='RuntimeError: failed stage: qualify-migrations'
    assert summary['evidence_budget_status']=='within_budget'
    assert_exact_files(controller,summary)
    assert (controller.OUT/'evidence.dat').read_bytes()==evidence
    # The original actual fixture remains inaccurate and unchanged, not repaired.
    assert decode(ACTUAL_FAILED_SUMMARY)['evidence_bytes']==58282


def test_cancellation_prevents_success_and_is_in_final_count(controller,monkeypatch):
    monkeypatch.setattr(controller,'STOP',True)
    native=b'SYNTHETIC ORIGINAL';(controller.OUT/'native.bin').write_bytes(native)
    summary={'status':'application_checks_passed_pending_cleanup'}
    returned=controller.finish_owned('synthetic',controller.OUT,summary)
    assert returned is not None and 'cancelled' in str(returned)
    assert summary['status']=='failed_or_cancelled' and 'cancelled' in summary['error']
    assert_exact_files(controller,summary)
    assert (controller.OUT/'native.bin').read_bytes()==native


def test_primary_survives_cleanup_and_budget_failures_with_all_metadata_counted(controller,monkeypatch):
    primary=ValueError('first synthetic requirement failure')
    def cleanup(*a):raise OSError('secondary cleanup error')
    monkeypatch.setattr(controller,'cleanup',cleanup);monkeypatch.setattr(controller,'CAP',1)
    native=b'RETAINED SYNTHETIC EVIDENCE';(controller.OUT/'native.bin').write_bytes(native)
    summary={'status':'application_checks_passed_pending_cleanup'}
    returned=controller.finish_owned('synthetic',controller.OUT,summary,primary)
    assert returned is primary and summary['error']=='ValueError: first synthetic requirement failure'
    assert summary['owned_cleanup']['status']=='failed' and summary['evidence_budget_status']=='exceeded'
    assert summary['status']=='failed_or_cancelled' and len(primary.__notes__)==2
    assert assert_exact_files(controller,summary)>controller.CAP
    assert (controller.OUT/'native.bin').read_bytes()==native


@pytest.mark.parametrize('adjustment',[0,-1])
def test_exact_cap_vs_one_byte_below_cannot_hide_diagnostic_growth(controller,monkeypatch,adjustment):
    native=b'x'*9750;(controller.OUT/'native.bin').write_bytes(native)
    # Independently solve the documented success receipt byte boundary, without
    # calling the production fixed-point helper to generate expected counts.
    cap=len(native)
    for _ in range(10):
        expected={'status':'actual_hosted_gate_passed','owned_cleanup':{'status':'passed'},
            'evidence_bytes':cap,'evidence_budget_bytes':cap,'evidence_budget_status':'within_budget'}
        total=len(native)+len((json.dumps(expected,indent=2,sort_keys=True)+'\n').encode())
        if total==cap:break
        cap=total
    else:pytest.fail('independent boundary calculation did not converge')
    monkeypatch.setattr(controller,'CAP',cap+adjustment)
    summary={'status':'application_checks_passed_pending_cleanup'}
    returned=controller.finish_owned('synthetic',controller.OUT,summary)
    actual=assert_exact_files(controller,summary)
    if adjustment==0:
        assert returned is None and actual==cap and summary['status']=='actual_hosted_gate_passed'
    else:
        assert returned is not None and actual>controller.CAP
        assert summary['status']=='failed_or_cancelled' and summary['evidence_budget_status']=='exceeded'
        assert 'evidence_budget_error' in summary
    assert (controller.OUT/'native.bin').read_bytes()==native


@pytest.mark.parametrize('case',['success','unicode-primary','cleanup-failure','primary-over-budget','measurement-failure'])
def test_synthetic_receipt_sourcefix_counts_final_bytes_without_hiding_failure(tmp_path,monkeypatch,case):
    """Review the assigned finalizer correction; only synthetic temp files exist."""
    import platform
    monkeypatch.setattr(platform,'machine',lambda:'AMD64')
    def forbidden(*a,**kw):pytest.fail('no external operation during finalizer review')
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    monkeypatch.setattr(subprocess,'run',forbidden)
    monkeypatch.setattr(subprocess,'check_output',forbidden)
    path=ROOT/'scripts/ci_axi_application.py'
    require(sha(path.read_bytes())=='459bde8bfec12b40c3ffd856ba825c7896f62d2b5b0031c5d853dfedccb43987','reviewed correction source freeze')
    spec=importlib.util.spec_from_file_location('receipt_correction_independent_review',path)
    controller=importlib.util.module_from_spec(spec);spec.loader.exec_module(controller)
    monkeypatch.setattr(controller,'OUT',tmp_path)
    native=b'SYNTHETIC HELPER DATA; NO NATIVE EXECUTION\n'
    (tmp_path/'evidence.dat').write_bytes(native);(tmp_path/'summary.json').write_bytes(b'old receipt must be replaced, not double-counted')
    primary=RuntimeError('synthetic \u00e9 \U0001f41f primary '+'x'*80) if case in ('unicode-primary','primary-over-budget') else None
    secondary=RuntimeError('synthetic cleanup failure')
    def cleanup(*args):
        if case=='cleanup-failure':raise secondary
    monkeypatch.setattr(controller,'cleanup',cleanup)
    if case=='primary-over-budget':monkeypatch.setattr(controller,'CAP',len(native)+1)
    measurement=OSError('synthetic unavailable measurement')
    if case=='measurement-failure':
        def unavailable(*args):raise measurement
        monkeypatch.setattr(controller,'evidence_size',unavailable)
    summary={'status':'application_checks_passed_pending_cleanup','claims':'synthetic finalizer unit only'}
    returned=controller.finish_owned('synthetic-review',tmp_path,summary,primary)
    raw=(tmp_path/'summary.json').read_bytes();saved=decode(raw)
    assert saved==summary and (tmp_path/'evidence.dat').read_bytes()==native
    actual=sum(p.stat().st_size for p in tmp_path.rglob('*') if p.is_file())
    if case=='measurement-failure':
        assert returned is measurement and saved['evidence_bytes'] is None
        assert saved['evidence_budget_status']=='unavailable'
    else:
        assert saved['evidence_bytes']==actual and len(raw)+len(native)==actual
    if case=='success':assert returned is None and saved['status']=='actual_hosted_gate_passed'
    else:assert returned is not None and saved['status']=='failed_or_cancelled'
    if primary is not None:assert returned is primary
    if case=='cleanup-failure':assert returned is secondary and saved['owned_cleanup']['status']=='failed'
    if case=='primary-over-budget':assert saved['evidence_budget_status']=='exceeded' and actual>controller.CAP


# Exact producer JSON retained as inert data; never a fabricated successful run.
ACTUAL_FAILED_SUMMARY = b'{\n  "application_recipe_sha256": "3c36eceee3c954fc6bf446bfe7d8ae05c8648d7fc55d5f7aa3e2658292521a2d",\n  "builder_caps": {\n    "cpu_quota": 200000,\n    "memory_bytes": 4294967296\n  },\n  "commit": "726770e7b4a68f3c84c50d2963c95fb222581495",\n  "error": "RuntimeError: failed stage: qualify-migrations",\n  "evidence_budget_bytes": 67108864,\n  "evidence_bytes": 58282,\n  "image_id": "sha256:cd59b4d02a01233a86706688f1eac667d1a9a3dc05596081e639713efb1642d3",\n  "local_adoption": false,\n  "maximum_attempts": 7,\n  "migration_target": "13engineeringprojects",\n  "owned_cleanup": {\n    "status": "passed"\n  },\n  "project": "vf-axi-ci-40934406068-1",\n  "status": "failed_or_cancelled",\n  "storage_image_ids": {\n    "postgres": "sha256:6a2e324ebe59af6031c4c1cd96c1d5072c2ebc36563d2196365e43b8297c062d",\n    "valkey": "sha256:e2b92b9c82613081f4e7cc8d18c8f2a30df348e906d4628e4dc54d537e886978"\n  }\n}\n'
