"""Injected fake effects only. No provider/native/service measurements."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from veriforge.adapters import get_adapter
from veriforge.contracts import ArtifactReference, AttemptResult, canonical_hash
from veriforge.evaluation import family_corpus as c, family_harness as f, harness as h, records as r

H = 'a' * 64


@pytest.fixture(scope='module')
def manifest(): return c.build_corpus()


def study(manifest, case, strategy):
    return f.FamilyStudy(cohort_id='study-20261101-0001', cases=(c.PublicCase(case_id=case.case_id),),
        registry_sha256=canonical_hash(manifest), information_policy_sha256=H, environment_sha256=H,
        strategy_sha256=h.digest(Path(f.__file__).read_bytes()), seeds=(7,), repeats=1, strategies=(strategy,),
        limits=h.Limits(attempts=2, cycles=512, tool_seconds=20.0, artifact_bytes=3*1024*1024, model_tokens=30000,
            per_attempt_cycles=256, per_attempt_seconds=5.0, per_attempt_artifact_bytes=128*1024,
            per_model_seconds=3.0, response_bytes=16000, feedback_output_tokens=512, end_to_end_seconds=30.0),
        requested_model='fake-typed-model', model_identity_sha256=H, generation_parameters_sha256=H,
        tool_evidence_kind='mock', model_evidence_kind='mock', visibility='disclosed_development')


def make(tmp_path, manifest, family, strategy='directed_manual', **kwargs):
    case = next(case for case in manifest.cases if case.adapter_id == family)
    profile = study(manifest, case, strategy)
    trial = h.matrix(profile)[0]
    root = tmp_path / (family + '-' + strategy); root.mkdir()
    obj = f.FamilyHarness(root, profile, trial, manifest, now=lambda: datetime.now(timezone.utc),
        control=kwargs.pop('control', lambda: 'RUNNING'), model_control=kwargs.pop('model_control', lambda: 'RUNNING'), **kwargs)
    obj.create()
    return obj


class Runtime:
    evidence_kind = 'mock'
    def __init__(self, callback=None): self.calls, self.callback = [], callback
    def execute(self, operation_id, experiment, *, timeout_seconds, admitted):
        assert admitted()
        self.calls.append(experiment)
        descriptor = get_adapter(family(experiment))
        payload = b'\xff\x00binary-synthetic'
        ref = ArtifactReference(artifact_id='raw-test', path='observations.bin', sha256=h.digest(payload),
                                size_bytes=len(payload), media_type='application/octet-stream')
        result = AttemptResult(attempt_id='host-synthetic', experiment_id=experiment.experiment_id,
            execution_status='completed', verdict='pass_within_scope',
            identity={**descriptor.expected_identity(experiment), 'environment_hash': H},
            usage={'cycles': len(descriptor.expand_stimulus(experiment)), 'wall_time_seconds': 0.1},
            artifacts=(ref,), uncertainties=('Explicit fake; no native tool executed',))
        if self.callback: self.callback()
        return f.RuntimeReceipt(operation_id=operation_id, experiment_sha256=canonical_hash(experiment),
            evidence_kind='mock', result=result, artifacts=(f.CapturedArtifact(reference=ref, data=payload),))


def family(experiment): return 'rtl.fifo.v1' if experiment.target == 'fifo' else experiment.adapter


class Provider:
    evidence_kind = 'mock'
    requested_model = 'fake-typed-model'
    model_identity_sha256 = generation_parameters_sha256 = H
    def __init__(self, callback=None): self.calls, self.callback = [], callback
    def propose(self, operation_id, request, *, timeout_seconds, admitted):
        assert admitted()
        self.calls.append(request)
        if self.callback: self.callback()
        if request.capability == 'queue':
            data = {'operations': [{'op': 'reset'}, {'op': 'write', 'value': 42}, {'op': 'read'}]}
        elif request.capability == 'unsigned_product':
            data = {'operations': [{'op': 'reset'}, {'op': 'multiply', 'a': 7, 'b': 9}, {'op': 'idle', 'cycles': 2}]}
        else:
            data = {'cycles': [{'reset_n': False}, {}, {'ar_valid': True}, {}, {}, {}]}
        response = h.encoded({'capability': request.capability, **data})
        return h.ProposalReceipt(operation_id=operation_id, request_sha256=h.digest(h.encoded(request)),
            evidence_kind='mock', state='completed', response=response, returned_model=self.requested_model,
            model_version='synthetic-only', usage=r.ModelUsage(state='known', input_tokens=20, output_tokens=30, billed_usd=None), elapsed_seconds=0.1)


def step(obj, **kwargs): return obj.advance(expected_head=obj.read()[1], **kwargs)


@pytest.mark.parametrize('family_id', c.ADAPTERS)
@pytest.mark.parametrize('strategy', h.STRATEGIES)
def test_three_family_strategies_produce_existing_raw_records(tmp_path, manifest, family_id, strategy):
    obj = make(tmp_path, manifest, family_id, strategy)
    runtime, provider = Runtime(), Provider()
    for _ in range(7):
        outcome = step(obj, runtime=runtime, provider=provider)
        if outcome != 'progress': break
    assert outcome in {'completed', 'budget_exhausted'}
    entries = obj.log.snapshot().entries
    assert isinstance(entries[-1].record, r.CampaignRecord)
    attempts = [e for e in entries if isinstance(e.record, r.AttemptRecord)]
    assert len(attempts) == (1 if strategy == 'meta_one_pass' else 2)
    assert all(e.record.evidence_kind == 'mock' for e in attempts)
    for entry in attempts:
        assert obj.blobs.read(entry.record.attempt.artifacts[0]) == b'\xff\x00binary-synthetic'
    for request in provider.calls:
        encoded = h.encoded(request)
        assert not any(case.fixture_id.encode() in encoded for case in manifest.cases)
        assert b'expected_repair' not in encoded and b'checker_hash' not in encoded
    if strategy == 'meta_feedback':
        assert len(provider.calls) == 2
        assert provider.calls[1].feedback[0].attempt == h.reference(attempts[0])
    if strategy == 'meta_one_pass': assert not provider.calls[0].feedback
    # Reopen the same journal/ledger; no second effect after terminal.
    reopened = f.FamilyHarness(obj.root, obj.study, obj.trial, manifest, now=obj.now,
        control=lambda: 'RUNNING', model_control=lambda: 'RUNNING')
    assert step(reopened) == outcome


@pytest.mark.parametrize('family_id', c.ADAPTERS)
def test_exact_replay_parent_is_preserved(tmp_path, manifest, family_id):
    obj = make(tmp_path, manifest, family_id)
    runtime = Runtime()
    assert step(obj, runtime=runtime) == 'progress'
    parent = obj.log.snapshot().entries[0]
    assert step(obj, runtime=runtime, replay_parent_id=parent.record.record_id) == 'progress'
    replay = obj.log.snapshot().entries[-1].record
    assert replay.purpose == 'replay' and replay.parent_attempt == h.reference(parent)
    assert runtime.calls[0] == runtime.calls[1]


def test_ambiguous_effect_cannot_be_resent_after_reopen(tmp_path, manifest):
    obj = make(tmp_path, manifest, c.ADAPTERS[0])
    class Lost(Runtime):
        def execute(self, *args, **kwargs):
            self.calls.append('unknown')
            raise RuntimeError('fake lost effect')
    runtime = Lost()
    with pytest.raises(h.HarnessError, match='effect_unsettled'):
        step(obj, runtime=runtime)
    reopened = f.FamilyHarness(obj.root, obj.study, obj.trial, manifest, now=obj.now, control=lambda: 'RUNNING')
    assert reopened.read()[2] is not None
    with pytest.raises(h.HarnessError, match='no_automatic_redispatch'):
        step(reopened, runtime=runtime)
    assert len(runtime.calls) == 1


def test_model_hold_and_post_effect_revocation_do_not_apply_late_completion(tmp_path, manifest):
    held = [True]
    obj = make(tmp_path, manifest, c.ADAPTERS[1], 'meta_feedback', model_control=lambda: 'STOPPED' if held[0] else 'RUNNING')
    provider = Provider(callback=lambda: held.__setitem__(0, True))
    with pytest.raises(h.HarnessError, match='control_held'): step(obj, provider=provider)
    assert not provider.calls and obj.read()[2] is None
    held[0] = False
    with pytest.raises(h.HarnessError, match='effect_unsettled'): step(obj, provider=provider)
    assert obj.read()[2] is not None and not obj.log.snapshot().entries


def test_seeded_random_is_repeatable_and_equal_limits_do_not_depend_on_strategy(tmp_path, manifest):
    for family_id in c.ADAPTERS:
        obj = make(tmp_path, manifest, family_id, 'constrained_random')
        a = obj._random(0, 'neutral-operation')
        b = obj._random(0, 'neutral-operation')
        assert a == b
        manual = study(manifest, obj.case, 'directed_manual')
        assert manual.limits == obj.study.limits


@pytest.mark.parametrize('fault', ['identity', 'bytes', 'cycles'])
def test_bad_host_receipt_retains_pending_effect_without_retry(tmp_path, manifest, fault):
    obj = make(tmp_path, manifest, c.ADAPTERS[0])
    class Bad(Runtime):
        def execute(self, *args, **kwargs):
            receipt = super().execute(*args, **kwargs)
            if fault == 'identity':
                result = receipt.result.model_copy(update={'identity': receipt.result.identity.model_copy(update={'source_hash': 'b'*64})})
                return receipt.model_copy(update={'result': result})
            if fault == 'bytes':
                return receipt.model_copy(update={'artifacts': (receipt.artifacts[0].model_copy(update={'data': b'changed'}),)})
            return receipt.model_copy(update={'result': receipt.result.model_copy(update={
                'usage': receipt.result.usage.model_copy(update={'cycles': 257})})})
    runtime = Bad()
    with pytest.raises(h.HarnessError, match='effect_unsettled'):
        step(obj, runtime=runtime)
    assert obj.read()[2] is not None
    with pytest.raises(h.HarnessError, match='no_automatic_redispatch'):
        step(obj, runtime=runtime)
    assert len(runtime.calls) == 1


@pytest.mark.parametrize('outcome', ['unknown', 'invalid'])
def test_unknown_or_invalid_model_round_blocks_followup_without_retry(tmp_path, manifest, outcome):
    obj = make(tmp_path, manifest, c.ADAPTERS[1], 'meta_feedback')
    class Bad(Provider):
        def propose(self, *args, **kwargs):
            receipt = super().propose(*args, **kwargs)
            if outcome == 'unknown':
                return receipt.model_copy(update={'state': 'unknown', 'response': None,
                    'usage': r.ModelUsage(state='unknown', input_tokens=None, output_tokens=None, billed_usd=None)})
            return receipt.model_copy(update={'response': b'{"capability":"unsigned_product","fixture_id":"private"}'})
    provider = Bad()
    assert step(obj, provider=provider) == 'progress'
    assert step(obj, provider=provider, runtime=Runtime()) == 'blocked'
    assert len(provider.calls) == 1
    record = obj.log.snapshot().entries[0].record
    assert record.status == ('unknown' if outcome == 'unknown' else 'rejected')
    assert not record.accepted_plan


def test_cancelled_effect_and_changed_saved_blob_are_not_dispatched(tmp_path, manifest):
    cancelled = [False]
    obj = make(tmp_path, manifest, c.ADAPTERS[2], cancelled=lambda: cancelled[0])
    runtime = Runtime()
    cancelled[0] = True
    with pytest.raises(h.HarnessError, match='control_held'):
        step(obj, runtime=runtime)
    assert not runtime.calls and obj.read()[2] is None
    cancelled[0] = False
    assert step(obj, runtime=runtime) == 'progress'
    artifact = obj.log.snapshot().entries[0].record.attempt.artifacts[0]
    (obj.root / artifact.path).write_bytes(b'corrupted')
    with pytest.raises(h.HarnessError, match='blob_conflict'):
        step(obj, runtime=runtime)
    assert len(runtime.calls) == 1


def test_runtime_artifact_budget_preflight_precedes_blob_writes(tmp_path, manifest):
    obj = make(tmp_path, manifest, c.ADAPTERS[0])
    class Oversize(Runtime):
        def execute(self, *args, **kwargs):
            receipt = super().execute(*args, **kwargs)
            data = b'x' * (obj.study.limits.per_attempt_artifact_bytes + 1)
            reference = ArtifactReference(artifact_id='oversize', path='observations.bin', sha256=h.digest(data),
                size_bytes=len(data), media_type='application/octet-stream')
            return receipt.model_copy(update={'result': receipt.result.model_copy(update={'artifacts': (reference,)}),
                'artifacts': (f.CapturedArtifact(reference=reference, data=data),)})
    runtime = Oversize()
    with pytest.raises(h.HarnessError, match='effect_unsettled'):
        step(obj, runtime=runtime)
    assert not list(obj.root.glob('blob-*.bin'))
    assert obj.read()[2] is not None and len(runtime.calls) == 1


def test_optional_context_audit_survives_frozen_scope_and_collection(tmp_path, manifest, monkeypatch):
    from veriforge.evaluation import collection
    audit_bytes = b'{"synthetic_context_audit":true}'
    audit = h.BlobStore(tmp_path).put(audit_bytes)
    case = manifest.cases[0]
    profile = study(manifest, case, 'directed_manual').model_copy(update={'context_audit': audit})
    trial = h.matrix(profile)[0]
    obj = f.FamilyHarness(tmp_path, profile, trial, manifest,
        now=lambda: datetime.now(timezone.utc), control=lambda: 'RUNNING')
    assert obj.scope.context_audit == obj.study.context_audit == audit
    freeze = f.Freeze(study=obj.study, trial=trial, scope=obj.scope, admitted_at=obj.clock())
    record = r.CampaignRecord(**obj.common((freeze,), 'synthetic-terminal', 'mock'),
        attempts=(), model_rounds=(), stop_reason='blocked', right_censored=False,
        review=r.ReviewDisposition(status='pending', reason='synthetic field propagation regression'))
    entries = (SimpleNamespace(record=record),)
    monkeypatch.setattr(r, 'EvaluationLog', lambda *a, **kw: SimpleNamespace(read=lambda **kw: entries))
    identity = case.plans[0].identity
    binding = collection.FamilyCaseBinding(case_id=case.case_id, profile=case.adapter_id,
        source_sha256=identity.source_hash, checker_sha256=identity.checker_hash,
        contract_sha256=identity.contract_hash)
    request = SimpleNamespace(study=obj.study, cases=(binding,))
    capture = collection.Capture(trial=trial, directory='capture', expected_head=H)
    directory = tmp_path / 'capture'; directory.mkdir()
    assert h.BlobStore(directory).put(audit_bytes) == audit
    loaded, blobs = collection.trial_records(request, capture, tmp_path)
    assert loaded == entries and blobs[audit.path] == audit_bytes
