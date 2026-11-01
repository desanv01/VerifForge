"""Temporary SQLite + synthetic observations; no worker/tool/provider is launched."""
from datetime import timedelta
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from veriforge.core import repair_jobs as jobs, service
from veriforge.core.catalog import seed_catalog
from veriforge.core.database import Base, Attempt, Campaign, Dispatch, make_engine, session_factory, now
from veriforge.core.repair_models import RepairCase, RepairJob

_source = Path(__file__).resolve().parents[1] / 'evidence' / 'test_families.py'
_spec = importlib.util.spec_from_file_location('repair_synthetic_fixtures', _source)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
_axi_spec = importlib.util.spec_from_file_location('repair_axi_synthetic_fixtures',
    Path(__file__).resolve().parents[1] / 'evidence' / 'test_axi_evidence.py')
_axi_fixture = importlib.util.module_from_spec(_axi_spec)
_axi_spec.loader.exec_module(_axi_fixture)


def make_state(tmp_path, family='arithmetic'):
    engine = make_engine('sqlite:///' + (tmp_path/'repair.db').as_posix())
    Base.metadata.create_all(engine)  # isolated synthetic schema, not live migration
    sessions = session_factory(engine)
    root = tmp_path/'artifacts'
    plan = _fixture.arithmetic_plan() if family == 'arithmetic' else _axi_fixture.plan()
    with sessions.begin() as session:
        seed_catalog(session)
        campaign = service.create_campaign(session, plan.fixture_id, plan.model_dump(mode='json'),
            'synthetic-original', adapter_id=plan.adapter)
        service.start(session, campaign.id)
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == campaign.id))
        lease, _, _ = service.claim(session, attempt.id)
        result = (_fixture.recorded_arithmetic if family == 'arithmetic' else _axi_fixture.scripted)(plan, root/attempt.id)
        assert service.finalize(session, attempt.id, lease, result.model_dump(mode='json'))
        original = attempt.id
    return engine, sessions, root, original


@pytest.fixture
def state(tmp_path):
    data = make_state(tmp_path)
    yield data
    data[0].dispose()


def policy(**updates):
    return jobs.RepairPolicy(enabled=True, runtime_configured=True, runtime_identity='a'*64, **updates)


def test_postgres_cleanup_status_has_room_for_the_actual_terminal_value():
    assert RepairJob.__table__.c.cleanup_status.type.length >= len('worker_finalized_external_cleanup_unverified')


def admit(data, request=None, key='candidate'):
    _, sessions, root, original = data
    with sessions.begin() as session:
        row = jobs.admit(session, original, key, request or {
            'kind':'candidate', 'candidate_fixture_id':'multiplier_correct'},
            policy=policy(), control=lambda:'RUNNING', root=root)
        return row.id


def complete_one(data, job_id, *, fail_callback=None, writer=None):
    _, sessions, root, _ = data
    with sessions.begin() as session:
        view = jobs.status(session, job_id)
        active = next(case for case in view['cases'] if case['case_id'] == view['active_case_id'])
        assert jobs.attempt_allowed(session, active['attempt_id'], policy=policy(), control=lambda:'RUNNING')
        claimed = service.claim(session, active['attempt_id'])
        assert claimed is not None
        lease, payload, _ = claimed
    plan = jobs.get_adapter(payload['adapter']).validate_experiment(payload)
    # No production worker/dispatcher: generate explicit synthetic test evidence.
    writer = writer or (_fixture.recorded_arithmetic if plan.target == 'arithmetic' else _axi_fixture.scripted)
    result = writer(plan, root/active['attempt_id'])
    if fail_callback is not None: fail_callback(data, active)
    with sessions.begin() as session:
        accepted = service.finalize(session, active['attempt_id'], lease, result.model_dump(mode='json'))
    return accepted


def test_candidate_persists_every_case_then_one_at_a_time_after_reload(state):
    job_id = admit(state)
    _, sessions, root, _ = state
    with sessions() as session:
        view = jobs.status(session, job_id)
        assert len(view['cases']) >= 4
        assert sum(case['attempt_id'] is not None for case in view['cases']) == 1
        assert all(case['experiment_id'] and case['plan_hash'] for case in view['cases'])
    observed = []
    for _ in range(12):
        with sessions() as session:
            view = jobs.status(session, job_id)
        if view['execution_status'] != 'active': break
        assert sum(case['status'] == 'queued' for case in view['cases']) == 1
        observed.append(view['active_case_id'])
        assert complete_one(state, job_id)
        with sessions.begin() as session:
            jobs.advance(session, job_id, policy=policy(), control=lambda:'RUNNING', root=root)
    with sessions() as session:
        view = jobs.status(session, job_id)
        assert view['execution_status'] == 'completed' and view['reason'] == 'validated_under_checks'
        assert len(observed) == len(set(observed)) == len(view['cases'])
        report = jobs.report(session, job_id)['report']
        proof = report['family_candidate_report']
        assert proof['suite_sha256'] == view['suite_hash']
        assert proof['validation']['source_review']['configuration_changed']
        assert report['provenance']['authorship_claim'] == 'curated_source_not_generated_patch'
        assert all(case['result_hash'] for case in report['cases'])
        assert not report['operational_acceptance'] and not report['physical_cleanup_verified']


def test_disabled_idempotency_and_mismatched_reuse(state):
    _, sessions, root, original = state
    with sessions.begin() as session, pytest.raises(jobs.RepairDisabled):
        jobs.admit(session, original, 'disabled', {'kind':'reduction'}, root=root)
    first = admit(state)
    assert admit(state) == first
    with pytest.raises(service.Conflict), sessions.begin() as session:
        jobs.admit(session, original, 'candidate', {'kind':'reduction'}, policy=policy(), control=lambda:'RUNNING', root=root)


@pytest.mark.parametrize('running', [False, True])
def test_cancel_fences_late_delivery_and_keeps_history(state, running):
    job_id = admit(state)
    _, sessions, _, _ = state
    with sessions.begin() as session:
        row = session.scalar(select(RepairCase).where(RepairCase.job_id == job_id, RepairCase.status == 'queued'))
        attempt_id, old_lease = row.attempt_id, row.execution_lease
        if running: assert service.claim(session, attempt_id)
        jobs.cancel(session, job_id)
    with sessions.begin() as session:
        assert jobs.attempt_allowed(session, attempt_id, policy=policy(), control=lambda:'RUNNING') is False
        assert service.claim(session, attempt_id) is None
        assert not service.finalize(session, attempt_id, old_lease, {})
        before = jobs.status(session, job_id)
        jobs.advance(session, job_id, policy=policy(), control=lambda:'RUNNING')
        assert jobs.status(session, job_id)['cases'] == before['cases']
        assert before['execution_status'] == 'cancelled'
        assert jobs.report(session, job_id)['report']['cleanup_status'] == 'external_settlement_required'


def test_interrupted_child_quarantined_without_retry(state):
    job_id = admit(state)
    _, sessions, root, _ = state
    with sessions.begin() as session:
        case = session.scalar(select(RepairCase).where(RepairCase.job_id == job_id, RepairCase.status == 'queued'))
        service.claim(session, case.attempt_id)
        attempt = session.get(Attempt, case.attempt_id)
        attempt.execution_status = 'interrupted'
    with sessions.begin() as session:
        jobs.advance(session, job_id, policy=policy(), control=lambda:'RUNNING', root=root)
    with sessions.begin() as session:
        count = len(list(session.scalars(select(Attempt))))
        jobs.advance(session, job_id, policy=policy(), control=lambda:'RUNNING', root=root)
        assert len(list(session.scalars(select(Attempt)))) == count
        assert jobs.status(session, job_id)['reason'] == 'child_effect_unknown_no_retry'


def test_reduction_persists_tried_inputs_progress_and_attempt_limit(state):
    job_id = admit(state, {'kind':'reduction', 'max_attempts':3}, key='reduction')
    _, sessions, root, _ = state
    for _ in range(6):
        with sessions() as session:
            if jobs.status(session, job_id)['execution_status'] != 'active': break
        assert complete_one(state, job_id)
        with sessions.begin() as session:
            jobs.advance(session, job_id, policy=policy(), control=lambda:'RUNNING', root=root)
    with sessions() as session:
        report = jobs.report(session, job_id)['report']
        tried = [case for case in report['cases'] if case['role'] == 'reduction']
        assert 1 <= len(tried) <= 3
        assert len({case['plan_hash'] for case in tried}) == len(tried)
        assert report['claim'] == 'bounded_search_no_minimality_guarantee'
        assert report['best_case_id'] is not None
        assert report['improved'] and report['reduced_experiment'] != report['original_plan']
        assert report['policy_hash'] and report['max_attempts'] == 3


@pytest.mark.parametrize('problem', ['deadline','control','policy'])
def test_expiry_revocation_policy_change_never_admits_successor(state, problem):
    job_id = admit(state)
    _, sessions, root, _ = state
    assert complete_one(state, job_id)
    with sessions.begin() as session:
        job = session.get(RepairJob, job_id)
        at = jobs._utc(job.deadline_at) + timedelta(seconds=1) if problem == 'deadline' else None
        jobs.advance(session, job_id, policy=policy(max_cases=15) if problem == 'policy' else policy(),
            control=lambda:'STOPPED' if problem == 'control' else 'RUNNING', root=root, at=at)
        assert jobs.status(session, job_id)['execution_status'] == 'interrupted'
        assert sum(case.attempt_id is not None for case in jobs._cases(session, job)) == 1


def test_original_artifact_drift_stops_before_next_admission(state):
    job_id = admit(state)
    _, sessions, root, original = state
    assert complete_one(state, job_id)
    (root/original/'checker.py').write_bytes(b'synthetic-corruption')
    with sessions.begin() as session:
        jobs.advance(session, job_id, policy=policy(), control=lambda:'RUNNING', root=root)
        assert jobs.status(session, job_id)['reason'] == 'original_evidence_changed'


def test_untracked_manual_start_of_planned_child_is_fenced(state):
    job_id = admit(state)
    _, sessions, _, _ = state
    with sessions.begin() as session:
        case = session.scalar(select(RepairCase).where(RepairCase.job_id == job_id, RepairCase.status == 'planned'))
        service.start(session, case.campaign_id)
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == case.campaign_id))
        assert jobs.attempt_allowed(session, attempt.id, policy=policy(), control=lambda:'RUNNING') is False


def test_database_uniqueness_enforces_one_active_case(state):
    job_id = admit(state)
    _, sessions, _, _ = state
    with pytest.raises(IntegrityError), sessions.begin() as session:
        case = session.scalar(select(RepairCase).where(RepairCase.job_id == job_id, RepairCase.status == 'planned'))
        service.start(session, case.campaign_id)
        attempt = session.scalar(select(Attempt).where(Attempt.campaign_id == case.campaign_id))
        case.attempt_id, case.execution_lease = attempt.id, attempt.lease
        case.status = 'queued'
        session.flush()


def test_partial_budget_admission_rolls_back_all_new_execution_state(state):
    _, sessions, root, original = state
    with pytest.raises(service.Conflict), sessions.begin() as session:
        jobs.admit(session, original, 'too-small', {'kind':'candidate','candidate_fixture_id':'multiplier_correct'},
            policy=policy(max_cases=3), control=lambda:'RUNNING', root=root)
    with sessions() as session:
        assert list(session.scalars(select(RepairJob))) == []
        assert len(list(session.scalars(select(Attempt)))) == 1
        assert len(list(session.scalars(select(Campaign)))) == 1


def test_revocation_after_preflight_rolls_back_before_dispatch(state):
    _, sessions, root, original = state
    controls = iter(['RUNNING','STOPPED'])
    with pytest.raises(jobs.RepairDisabled), sessions.begin() as session:
        jobs.admit(session, original, 'revoked', {'kind':'candidate','candidate_fixture_id':'multiplier_correct'},
            policy=policy(), control=lambda:next(controls), root=root)
    with sessions() as session:
        assert list(session.scalars(select(RepairJob))) == []
        assert len(list(session.scalars(select(Attempt)))) == 1


def test_axi_candidate_collects_failed_regressions_without_false_green(tmp_path):
    data = make_state(tmp_path, family='axi_lite')
    try:
        job_id = admit(data, {'kind':'candidate','candidate_fixture_id':'axil_registers_correct'})
        for _ in range(12):
            with data[1]() as session:
                if jobs.status(session, job_id)['execution_status'] != 'active': break
            assert complete_one(data, job_id)
            with data[1].begin() as session:
                jobs.advance(session, job_id, policy=policy(), control=lambda:'RUNNING', root=data[2])
        with data[1]() as session:
            report = jobs.report(session, job_id)['report']
            assert report['reason'] == 'failed_under_checks'
            assert report['family_candidate_report']['validation']['regressions']
            assert all(case['result_hash'] for case in report['cases'])
    finally: data[0].dispose()


def test_axi_reduction_is_durable_and_bounded(tmp_path):
    data = make_state(tmp_path, family='axi_lite')
    try:
        job_id = admit(data, {'kind':'reduction','max_attempts':2})
        for _ in range(5):
            with data[1]() as session:
                if jobs.status(session, job_id)['execution_status'] != 'active': break
            assert complete_one(data, job_id)
            with data[1].begin() as session:
                jobs.advance(session, job_id, policy=policy(), control=lambda:'RUNNING', root=data[2])
        with data[1]() as session:
            report = jobs.report(session, job_id)['report']
            assert report['execution_status'] == 'completed'
            assert sum(c['role'] == 'reduction' for c in report['cases']) <= 2
    finally: data[0].dispose()


def test_original_not_reproduced_does_not_execute_candidate(state):
    from veriforge.contracts import AttemptResult
    def passing_observations(plan, output):
        # Fabricate matching pass observations for a mutant input, without
        # changing the checker/expectation. No actual DUT reproduction claimed.
        alternate = plan.model_copy(update={'fixture_id':'multiplier_correct'})
        result = _fixture.recorded_arithmetic(alternate, output)
        raw = plan.model_dump_json().encode()
        (output/'experiment.json').write_bytes(raw)
        values = result.model_dump(mode='json')
        values['identity'].update(jobs.get_adapter(plan.adapter).expected_identity(plan))
        for ref in values['artifacts']:
            if ref['path'] == 'experiment.json':
                ref.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
        result = AttemptResult.model_validate(values)
        (output/'result.json').write_text(result.model_dump_json())
        return result
    job_id = admit(state)
    assert complete_one(state, job_id, writer=passing_observations)
    with state[1].begin() as session:
        jobs.advance(session, job_id, policy=policy(), control=lambda:'RUNNING', root=state[2])
        view = jobs.status(session, job_id)
        assert view['reason'] == 'original_not_reproduced'
        assert sum(c['attempt_id'] is not None for c in view['cases']) == 1


def test_control_loss_during_evidence_check_blocks_result_adoption(state, monkeypatch):
    job_id = admit(state)
    assert complete_one(state, job_id)
    held = [True]
    original = jobs._checked_case
    def check(*args):
        result = original(*args)
        held[0] = False
        return result
    monkeypatch.setattr(jobs, '_checked_case', check)
    with state[1].begin() as session:
        jobs.advance(session, job_id, policy=policy(), control=lambda:'RUNNING' if held[0] else 'STOPPED', root=state[2])
        view = jobs.status(session, job_id)
        assert view['reason'] == 'terminal_admission_fenced'
        assert sum(c['attempt_id'] is not None for c in view['cases']) == 1


def test_engine_reload_retains_exact_operation_and_stale_effect_is_not_retried(state):
    job_id = admit(state)
    engine, sessions, root, original = state
    with sessions() as session:
        before = jobs.status(session, job_id)
    url = str(engine.url)
    engine.dispose()
    reopened = make_engine(url)
    again = session_factory(reopened)
    try:
        with again.begin() as session:
            assert jobs.status(session, job_id)['cases'] == before['cases']
            assert jobs.admit(session, original, 'candidate', {'kind':'candidate','candidate_fixture_id':'multiplier_correct'},
                policy=policy(), control=lambda:'RUNNING', root=root).id == job_id
            case = session.get(RepairCase, before['active_case_id'])
            service.claim(session, case.attempt_id)
            session.get(Attempt, case.attempt_id).heartbeat_at = now() - timedelta(days=1)
        with again.begin() as session:
            assert service.recover_stale(session) >= 1
            jobs.advance(session, job_id, policy=policy(), control=lambda:'RUNNING', root=root)
            result = jobs.status(session, job_id)
            assert result['execution_status'] == 'interrupted'
            assert sum(c['attempt_id'] is not None for c in result['cases']) == 1
    finally: reopened.dispose()
