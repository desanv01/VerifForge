"""Corpus-input checks only; no simulator/provider/benchmark measurements."""
import copy
import json

import pytest
from pydantic import ValidationError

from veriforge.adapters import AdapterDescriptor, get_adapter
from veriforge.contracts import canonical_hash
from veriforge.evaluation import family_corpus as corpus


@pytest.fixture
def manifest():
    return corpus.build_corpus()


def test_frozen_manifest_enumeration_and_existing_typed_runner_inputs(manifest):
    assert canonical_hash(manifest) == '744145940177eee08401dab040f71e8a8b6e3a271cb03384c1bd214c4e284f80'
    assert corpus.build_corpus() == manifest
    assert corpus.validate_corpus(json.loads(manifest.model_dump_json())) == manifest
    assert len(manifest.cases) == 9
    assert {c.adapter_id for c in manifest.cases} == set(corpus.ADAPTERS)
    assert {(c.adapter_id, c.fixture_id) for c in manifest.cases} == {
        (adapter, fixture) for adapter in corpus.ADAPTERS for fixture in get_adapter(adapter).fixture_ids}
    assert sum(c.inventory_role == 'curated_control' for c in manifest.cases) == 3
    assert all(c.disposition == 'unclassified' for c in manifest.cases)
    for case in manifest.cases:
        descriptor = get_adapter(case.adapter_id)
        plans = corpus.execution_plans(manifest, case.case_id)
        assert {p.purpose for p in plans} == {'detection', 'replay', 'neighbor', 'regression'}
        assert len({canonical_hash(p) for p in plans}) == len(plans)
        for plan in plans:
            assert isinstance(plan.experiment, descriptor.experiment_model)
            assert descriptor.expected_identity(plan.experiment) == plan.identity.model_dump()
            assert descriptor.expand_stimulus(plan.experiment)
        replay = next(p for p in plans if p.purpose == 'replay')
        original = next(p for p in plans if p.record_id == replay.parent_plan_id)
        assert replay.experiment == original.experiment
        assert replay.identity == original.identity
        assert replay.stimulus_sha256 == original.stimulus_sha256
        assert canonical_hash(replay) != canonical_hash(original)
    with pytest.raises(ValueError, match='unknown_corpus_case'):
        corpus.execution_plans(manifest, 'case-9999')


def test_public_alias_projection_has_no_private_target_or_answer_fields(manifest):
    public = corpus.public_cases(manifest)
    assert [p.model_dump() for p in public] == [{'case_id': f'case-{i:04d}'} for i in range(1, 10)]
    raw = json.dumps([p.model_dump() for p in public])
    for private in ('fixture', 'source', 'checker', 'contract', 'inventory', 'seeded',
                    'control', 'bug', 'fifo', 'multiplier', 'axil', 'expected', 'disposition'):
        assert private not in raw
    with pytest.raises(ValidationError):
        corpus.PublicCase.model_validate(manifest.cases[0].model_dump())
    with pytest.raises(ValidationError):
        public[0].case_id = 'case-9999'
    with pytest.raises(ValidationError):
        manifest.cases[0].disposition = 'seeded_defect'


@pytest.mark.parametrize('change', ['duplicate_case', 'duplicate_plan', 'duplicate_stimulus',
    'wrong_fixture', 'wrong_inventory', 'checker', 'contract', 'source', 'experiment',
    'stimulus', 'malformed_plan', 'replay_parent', 'replay_stimulus', 'classification'])
def test_changed_or_malformed_frozen_inputs_rejected(manifest, change):
    data = manifest.model_dump(mode='json')
    case, plan = data['cases'][0], data['cases'][0]['plans'][0]
    if change == 'duplicate_case': data['cases'][1] = copy.deepcopy(case)
    elif change == 'duplicate_plan': case['plans'].append(copy.deepcopy(plan))
    elif change == 'duplicate_stimulus':
        duplicate = copy.deepcopy(plan); duplicate['record_id'] = 'other-record'; duplicate['purpose'] = 'neighbor'
        case['plans'].append(duplicate)
    elif change == 'wrong_fixture': case['fixture_id'] = 'fifo_correct'
    elif change == 'wrong_inventory': case['inventory_role'] = 'curated_control'
    elif change in {'checker', 'contract', 'source', 'experiment'}: plan['identity'][change + '_hash'] = 'f' * 64
    elif change == 'stimulus': plan['stimulus_sha256'] = 'f' * 64
    elif change == 'malformed_plan': plan['experiment']['operations'][0] = {'op': 'run_shell'}
    elif change == 'replay_parent': case['plans'][1]['parent_plan_id'] = 'missing-parent'
    elif change == 'replay_stimulus': case['plans'][1]['experiment']['seed'] = 1
    elif change == 'classification': case['disposition'] = 'seeded_defect'
    with pytest.raises((ValueError, ValidationError)):
        corpus.validate_corpus(data)


def test_installed_checker_change_rejects_retained_manifest(manifest, monkeypatch):
    original = AdapterDescriptor.expected_identity
    def changed(self, experiment):
        return {**original(self, experiment), 'checker_hash': 'f' * 64}
    monkeypatch.setattr(AdapterDescriptor, 'expected_identity', changed)
    with pytest.raises(ValueError, match='corpus_installed_identity_changed'):
        corpus.validate_corpus(manifest)


def test_installed_source_change_rejects_retained_manifest(manifest, monkeypatch):
    original = AdapterDescriptor.fixture_source
    def changed(self, fixture_id):
        return {**original(self, fixture_id), 'source_file_hash': 'f' * 64}
    monkeypatch.setattr(AdapterDescriptor, 'fixture_source', changed)
    with pytest.raises(ValueError, match='corpus_installed_identity_changed'):
        corpus.validate_corpus(manifest)


def test_unvalidated_model_copy_is_rechecked_at_export_boundary(manifest):
    broken = manifest.model_copy(update={'cases': manifest.cases[:-1]})
    with pytest.raises(ValueError): corpus.public_cases(broken)
    with pytest.raises(ValueError): corpus.execution_plans(broken, 'case-0001')
