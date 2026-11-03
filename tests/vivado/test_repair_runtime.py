"""Bounded filesystem and fake-controller checks; no native/vendor execution."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from veriforge.vivado import repair_execution as execution
from veriforge.vivado import repair_runtime
from veriforge.vivado.repair_execution import ControllerInstallation, PrivateFixedBatchAdapter
from veriforge.vivado.repair_runtime import VivadoRepairRuntime
from veriforge.vivado import storage
from test_repair_execution import FakeTool, setup, synthetic_xdc_admission_for_fake_tool_tests


def runtime_for(tmp_path,prepared,hooks):
    document=tmp_path/'requirements.txt';document.write_bytes(b'Explicit synthetic test-only requirements')
    installation=ControllerInstallation(tmp_path,tmp_path/'private'/'installed-synthesis.py')
    runtime=VivadoRepairRuntime(tmp_path/'artifacts',installation,'U6-FAKE-RESERVATION',lambda job:hooks,
                                requirements_document=document)
    pins=replace(prepared.pins,io_requirements_sha256=sha256(document.read_bytes()).hexdigest())
    return runtime,pins


def test_prepare_blocked_profile_reload_in_new_worker_and_duplicate_delivery(tmp_path):
    prepared,baseline,hooks=setup(tmp_path,missing=12)
    runtime,pins=runtime_for(tmp_path,prepared,hooks)
    hooks=replace(hooks,available_physical_bytes=lambda:pytest.fail('blocked profile queried memory'))
    runtime.hooks_factory=lambda job:hooks
    ticket=runtime.prepare(tmp_path/'source',tmp_path/'proposal',baseline,pins)
    assert ticket.blockers==('missing_io_requirements',)
    projection=runtime.inspect(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)
    assert projection['execution_status']=='prepared'
    assert str(tmp_path)not in json.dumps(projection)
    other=VivadoRepairRuntime(runtime.root,runtime.installation,'U6-OTHER-WORKER',runtime.hooks_factory,
                              requirements_document=runtime.requirements_document)
    result=other.execute(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)
    assert result['execution_status']=='blocked'and result['result']['blocker']=='missing_io_requirements'
    assert other.execute(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)==result


def test_worker_fake_boundary_connects_saved_candidate_and_comparison(tmp_path):
    prepared,baseline,hooks=setup(tmp_path)
    runtime,pins=runtime_for(tmp_path,prepared,hooks)
    ticket=runtime.prepare(tmp_path/'source',tmp_path/'proposal',baseline,pins)
    tool=FakeTool();runtime.runner=tool  # Explicit test-only replacement; production constructor has no runner argument.
    result=runtime.execute(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)
    assert result['execution_status']=='completed'and tool.calls==1
    record=result['result']['candidate_projection']
    assert record['investigation']['parent_investigation_sha256']==baseline.investigation_sha256
    assert record['candidate_comparison']['status']=='reported_internal_timing_improvement'
    assert result['repair_status']=='not_validated'and result['result_sha256']
    assert runtime.execute(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)==result and tool.calls==1
    (runtime.root/ticket.job_id/'native-receipt.json').write_bytes(b'corrupted')
    with pytest.raises(ValueError,match='native receipt'):runtime.inspect(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)


def test_uncertain_effect_is_quarantined_and_never_called_again(tmp_path):
    prepared,baseline,hooks=setup(tmp_path)
    runtime,pins=runtime_for(tmp_path,prepared,hooks)
    ticket=runtime.prepare(tmp_path/'source',tmp_path/'proposal',baseline,pins)
    (runtime.root/ticket.job_id/'operation-claim.json').write_bytes(b'claimed before interruption')
    runtime.hooks_factory=lambda job:pytest.fail('uncertain effect was retried')
    assert runtime.execute(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)['execution_status']=='quarantined'


@pytest.mark.parametrize('change',('installation','requirements','baseline','source'))
def test_worker_rejects_changed_protected_binding(tmp_path,change):
    prepared,baseline,hooks=setup(tmp_path)
    runtime,pins=runtime_for(tmp_path,prepared,hooks)
    ticket=runtime.prepare(tmp_path/'source',tmp_path/'proposal',baseline,pins)
    if change=='installation':runtime.installation=replace(runtime.installation,synthesis_controller=tmp_path/'other.py')
    elif change=='requirements':runtime.requirements_document.write_bytes(b'changed')
    elif change=='baseline':baseline=replace(baseline,investigation_sha256='e'*64)
    else:(runtime.root/ticket.job_id/'candidate/exercise3_core.v').write_bytes(b'changed')
    runtime.hooks_factory=lambda job:pytest.fail('invalid job reached hooks')
    with pytest.raises(ValueError):runtime.execute(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)


def test_runtime_rejects_path_ids_and_source_overlap(tmp_path):
    prepared,baseline,hooks=setup(tmp_path)
    runtime,pins=runtime_for(tmp_path,prepared,hooks)
    with pytest.raises(ValueError):runtime.inspect('../source',baseline,expected_candidate_plan_sha256='a'*64)
    runtime.root=tmp_path/'source'/'nested-artifacts'
    with pytest.raises(ValueError,match='separate'):runtime.prepare(tmp_path/'source',tmp_path/'proposal',baseline,pins)


def install_fake_controller(tmp_path,monkeypatch):
    # Admission override is isolated to synthetic controller bytes in this test.
    # This fixture has no process API, shell, ctypes, simulator or vendor call.
    path=tmp_path/'installed'/'deep'/'synthesis.py';path.parent.mkdir(parents=True)
    storage_hash=sha256(Path(storage.__file__).read_bytes()).hexdigest()
    raw=("from pathlib import Path\nimport veriforge.vivado.storage as storage_module\n"
         f"STORAGE_HASH='{storage_hash}'\nROOT=Path('/old-private-root')\n"
         "ATTEMPT=None\nOWNER=None\nPLAN_HASH=None\nLAUNCHER='old-launcher'\n").encode()
    path.write_bytes(raw)
    profile=execution.PROFILES['synthesis']
    monkeypatch.setitem(execution.PROFILES,'synthesis',(profile[0],sha256(raw).hexdigest(),*profile[2:]))
    launcher=tmp_path/'tool'/'vivado.bat';launcher.parent.mkdir();launcher.write_bytes(b'rem inert test launcher')
    return ControllerInstallation(tmp_path,path,launcher=launcher,launcher_sha256=sha256(launcher.read_bytes()).hexdigest())


def test_portable_fixed_binding_relocates_globals_without_launch(tmp_path,monkeypatch):
    prepared,baseline,hooks=setup(tmp_path)
    installation=install_fake_controller(tmp_path,monkeypatch)
    adapter=PrivateFixedBatchAdapter(tmp_path,'U6-FAKE-RESERVATION',installation=installation)
    module=adapter._load_controller(prepared)
    assert module.ROOT==tmp_path and module.ATTEMPT==prepared.directory/'candidate'
    assert module.PLAN_HASH==prepared.candidate_plan_sha256 and module.OWNER=='U6-FAKE-RESERVATION'
    assert module.LAUNCHER==str(installation.launcher)
    installation.launcher.write_bytes(b'rem changed')
    with pytest.raises(ValueError,match='launcher identity'):adapter._load_controller(prepared)


def test_controller_hash_rejects_code_before_loading(tmp_path,monkeypatch):
    prepared,baseline,hooks=setup(tmp_path)
    installation=install_fake_controller(tmp_path,monkeypatch)
    installation.synthesis_controller.write_text("raise AssertionError('must not load unapproved code')")
    with pytest.raises(ValueError,match='installation unavailable'):
        PrivateFixedBatchAdapter(tmp_path,'U6-FAKE-RESERVATION',installation=installation)._load_controller(prepared)


def test_launcher_rejects_unpinned_or_shell_expansion_configuration(tmp_path):
    with pytest.raises(ValueError):ControllerInstallation(tmp_path,tmp_path/'controller.py',launcher=tmp_path/'vivado.bat')
    with pytest.raises(ValueError):ControllerInstallation(tmp_path,tmp_path/'controller.py',launcher=tmp_path/'%x%'/'vivado.bat',launcher_sha256='a'*64)


def test_saved_native_receipt_must_match_full_returned_status(tmp_path,monkeypatch):
    prepared,baseline,hooks=setup(tmp_path)
    control=tmp_path/'.coordination';control.mkdir()
    (control/'current-state.json').write_text(json.dumps({'control':'RUNNING','resource_reservations':{
        'engineering_tool':{'owner':'U6-FAKE-RESERVATION'}}}))
    adapter=PrivateFixedBatchAdapter(tmp_path,'U6-FAKE-RESERVATION')
    def fake_protocol(**kwargs):
        assert kwargs['control_probe']()is True
        assert kwargs['validate']()==prepared.pins.candidate
        native={'success':False,'limit_reason':'timeout','receipt_persisted':True}
        saved={**native,'limit_reason':'result_directory_limit'}
        (prepared.directory/'candidate/synthesis-receipt.json').write_text(json.dumps(saved))
        return native
    # Fake fixed-protocol boundary only; no controller or process is loaded.
    monkeypatch.setattr(adapter,'_load_controller',lambda job:SimpleNamespace(
        ATTEMPT=prepared.directory/'candidate',run_attempt=fake_protocol))
    result=adapter.execute(prepared,hooks,300)
    assert result.infrastructure_error=='native_receipt_persistence_mismatch'
    assert json.loads(result.receipt)['limit_reason']=='result_directory_limit'


def test_routed_recipe_is_explicit_hash_bound_installation(tmp_path,monkeypatch):
    recipe=tmp_path/'routed.tcl';recipe.write_bytes(b'# fixed synthetic recipe, never executed\n')
    profile=execution.PROFILES['routed']
    monkeypatch.setitem(execution.PROFILES,'routed',(*profile[:2],sha256(recipe.read_bytes()).hexdigest(),profile[3]))
    installation=ControllerInstallation(tmp_path,tmp_path/'synth.py',routed_recipe=recipe)
    assert installation.recipe('routed')==recipe.read_bytes()
    recipe.write_bytes(b'# changed')
    with pytest.raises(ValueError,match='recipe identity'):installation.recipe('routed')


def test_runtime_persistence_failure_remains_uncertain(tmp_path,monkeypatch):
    prepared,baseline,hooks=setup(tmp_path)
    runtime,pins=runtime_for(tmp_path,prepared,hooks)
    ticket=runtime.prepare(tmp_path/'source',tmp_path/'proposal',baseline,pins)
    def interrupted(prepared,*args):
        (prepared.directory/'operation-claim.json').write_bytes(b'uncertain effect')
        return {'receipt_persisted':False,'execution_status':'infrastructure_error','repair_status':'not_validated'}
    monkeypatch.setattr(repair_runtime,'execute_repair',interrupted)
    result=runtime.execute(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)
    assert result['execution_status']=='quarantined'and result['result']is None
    assert result['blockers']==['receipt_persistence_failed']
    runtime.hooks_factory=lambda job:pytest.fail('uncertain persistence retried')
    assert runtime.execute(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)['execution_status']=='quarantined'


@pytest.mark.parametrize('profile',('synthesis','routed'))
def test_installed_original_controller_import_only_when_available(tmp_path,profile):
    # Optional local import of retained exact accepted controller bytes. No
    # run_attempt/NativeJob function is called. Public CI has no private files.
    installed_root=Path(__file__).resolve().parents[2]
    original=installed_root/execution.PROFILES[profile][0]
    if not original.is_file():pytest.skip('private accepted controller is not distributed')
    raw=original.read_bytes()
    assert sha256(raw).hexdigest()==execution.PROFILES[profile][1]
    prepared,baseline,hooks=setup(tmp_path)
    prepared=replace(prepared,pins=replace(prepared.pins,profile=profile))
    copy=tmp_path/'relocated'/'controllers'/f'{profile}.py';copy.parent.mkdir(parents=True);copy.write_bytes(raw)
    installation=ControllerInstallation(tmp_path,copy,routed_controller=copy)
    module=PrivateFixedBatchAdapter(tmp_path,'U6-IMPORT-ONLY',installation=installation)._load_controller(prepared)
    assert callable(module.run_attempt)and module.ROOT==tmp_path
    assert module.ATTEMPT==prepared.directory/'candidate'and module.OWNER=='U6-IMPORT-ONLY'


def test_retained_candidate_ticket_rejects_self_consistent_store_rewrite(tmp_path):
    prepared,baseline,hooks=setup(tmp_path,missing=12)
    runtime,pins=runtime_for(tmp_path,prepared,hooks)
    ticket=runtime.prepare(tmp_path/'source',tmp_path/'proposal',baseline,pins)
    directory=runtime.root/ticket.job_id
    manifest=json.loads((directory/'job.json').read_bytes())
    replacement=b'self-consistent replacement after admission'
    (directory/'candidate/exercise3_core.v').write_bytes(replacement)
    for item in manifest['pins']['candidate']:
        if item['name']=='exercise3_core.v':item.update(sha256=sha256(replacement).hexdigest(),size_bytes=len(replacement))
    for label in ('original','candidate'):
        path=directory/label/'plan.json';plan=json.loads(path.read_bytes());plan['pins']=manifest['pins']
        if label=='candidate':plan['sources']=manifest['pins']['candidate']
        raw=execution._json(plan);path.write_bytes(raw);manifest[label+'_plan_sha256']=sha256(raw).hexdigest()
    (directory/'job.json').write_bytes(execution._json(manifest))
    runtime.hooks_factory=lambda job:pytest.fail('rewritten candidate reached authority/effects')
    with pytest.raises(ValueError,match='binding/lineage'):
        runtime.inspect(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)
    with pytest.raises(ValueError,match='binding/lineage'):
        runtime.execute(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)


@pytest.mark.parametrize('change',('extra','status','status_type','blocker','projection','native','exit_type','nested','reports'))
def test_closed_result_projection_rejects_forgery_without_forwarding_text(tmp_path,change):
    prepared,baseline,hooks=setup(tmp_path)
    runtime,pins=runtime_for(tmp_path,prepared,hooks)
    ticket=runtime.prepare(tmp_path/'source',tmp_path/'proposal',baseline,pins)
    runtime.runner=FakeTool()
    runtime.execute(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)
    directory=runtime.root/ticket.job_id;path=directory/'repair-execution.json';data=json.loads(path.read_bytes())
    if change=='extra':data['untrusted_extra']='C:/private/path and arbitrary prose'
    elif change=='status':data['execution_status']='C:/private/status'
    elif change=='status_type':data['execution_status']=[]
    elif change=='blocker':data['blocker']='missing_io_requirements'
    elif change=='projection':data['candidate_projection']=None
    elif change=='native':data['native_receipt_sha256']=None
    elif change=='exit_type':data['native_exit_code']=False
    elif change=='nested':data['candidate_projection']['investigation']['extra']='C:/private/path'
    else:(directory/'received-reports/timing_summary.rpt').write_bytes(b'changed report')
    path.write_bytes(execution._json(data))
    runtime.hooks_factory=lambda job:pytest.fail('invalid receipt reached effects')
    with pytest.raises(ValueError):
        runtime.execute(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)


def test_missing_requirements_cannot_claim_completed_without_native_evidence(tmp_path):
    prepared,baseline,hooks=setup(tmp_path,missing=12)
    runtime,pins=runtime_for(tmp_path,prepared,hooks)
    ticket=runtime.prepare(tmp_path/'source',tmp_path/'proposal',baseline,pins)
    result={'schema':'vivado.repair-execution.v1','baseline_sha256':ticket.baseline_sha256,
            'candidate_plan_sha256':ticket.candidate_plan_sha256,'runner_kind':runtime.runner.kind,
            'receipt_persisted':True,'repair_status':'not_validated','execution_status':'completed',
            'blocker':None,'native_receipt_sha256':None,'candidate_projection':None}
    directory=runtime.root/ticket.job_id
    (directory/'operation-claim.json').write_bytes(execution._json({'baseline':ticket.baseline_sha256,
                                                                  'candidate_plan':ticket.candidate_plan_sha256}))
    (directory/'repair-execution.json').write_bytes(execution._json(result))
    with pytest.raises(ValueError,match='contradictory completed'):
        runtime.inspect(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)


@pytest.mark.parametrize('binding',('valid','plan','report_manifest'))
def test_native_shaped_fake_completion_requires_exact_plan_and_report_manifest(tmp_path,binding):
    # Protocol-shaped test double only. No installed controller is loaded and
    # no native process is started; this tests validation of the native schema.
    prepared,baseline,hooks=setup(tmp_path)
    runtime,pins=runtime_for(tmp_path,prepared,hooks)
    ticket=runtime.prepare(tmp_path/'source',tmp_path/'proposal',baseline,pins)
    fake=FakeTool()
    class ProtocolDouble(PrivateFixedBatchAdapter):
        def execute(self,prepared,hooks,deadline):
            output=fake.execute(prepared,hooks,deadline);native=json.loads(output.receipt)
            native.update(receipt_persisted=True,plan_sha256=prepared.candidate_plan_sha256,
                          script_sha256=execution.PROFILES['synthesis'][2],artifacts={
                              name:{'bytes':len(data),'sha256':sha256(data).hexdigest()}for name,data in output.reports.items()})
            if binding=='plan':native['plan_sha256']='e'*64
            if binding=='report_manifest':native['artifacts']['clocks.rpt']['sha256']='e'*64
            return execution.FixedToolOutput(json.dumps(native).encode(),output.reports)
    runtime.runner=ProtocolDouble(tmp_path,'U6-FAKE-NATIVE-SCHEMA')
    if binding=='valid':
        assert runtime.execute(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)['execution_status']=='completed'
    else:
        with pytest.raises(ValueError,match='native completion'):
            runtime.execute(ticket.job_id,baseline,expected_candidate_plan_sha256=ticket.candidate_plan_sha256)
    assert fake.calls==1
