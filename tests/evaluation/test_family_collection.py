"""A1: real local files with explicit fake three-family effects; never a benchmark."""
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import subprocess

import pytest
from pydantic import ValidationError

from veriforge.adapters import get_adapter
from veriforge.contracts import canonical_hash
from veriforge.evaluation import collection as c, family_corpus, family_harness as f, harness as h
from veriforge.evaluation.cli import collect_main, reproduce_main

spec = importlib.util.spec_from_file_location('a1_family_test_boundaries', Path(__file__).with_name('test_family_harness.py'))
fake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fake)


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch):
    def denied(*args, **kwargs): pytest.fail('A1 has no native/model/database/network authorization')
    monkeypatch.setattr(subprocess, 'run', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(sqlite3, 'connect', denied)


@pytest.fixture(scope='module')
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp('three-family-synthetic')
    corpus = family_corpus.build_corpus()
    study = fake.study(corpus, corpus.cases[0], 'meta_feedback').model_copy(update={
        'cohort_id': 'study-20261103-0001', 'cases': family_corpus.public_cases(corpus)})
    aliases = {next(case.case_id for case in corpus.cases if case.adapter_id == adapter)
               for adapter in family_corpus.ADAPTERS}
    captures, objects = [], []
    for trial in h.matrix(study):
        if trial.case_id not in aliases: continue
        name = 'trial-' + trial.case_id
        directory = root / name; directory.mkdir()
        obj = f.FamilyHarness(directory, study, trial, corpus, now=lambda: fake.datetime.now(fake.timezone.utc),
                              control=lambda: 'RUNNING', model_control=lambda: 'RUNNING')
        obj.create()
        runtime, provider = fake.Runtime(), fake.Provider()
        for _ in range(6):
            outcome = obj.advance(expected_head=obj.read()[1], runtime=runtime, provider=provider)
            if outcome != 'progress': break
        assert outcome == 'budget_exhausted'
        assert len(provider.calls) == 2 and len(runtime.calls) == 2
        captures.append(c.Capture(trial=trial, directory=name, expected_head=obj.log.snapshot().current_head))
        objects.append(obj)
    bindings = tuple(c.FamilyCaseBinding(case_id=case.case_id, profile=case.adapter_id,
        source_sha256=case.plans[0].identity.source_hash, checker_sha256=case.plans[0].identity.checker_hash,
        contract_sha256=case.plans[0].identity.contract_hash) for case in corpus.cases)
    request = c.FamilyCollectionRequest(study=study, corpus=corpus, cases=bindings,
        release=c.ReleaseIdentity(source_commit='0'*40, source_state='unknown', dirty_patch_sha256=None,
            versions=tuple(c.Version(name=name, version=None) for name in
                           ('python', 'verilator', 'cocotb', 'gcc', 'runtime_image', 'model'))),
        captures=tuple(captures), exclusions=tuple(c.Exclusion(trial=trial, reason='not_run',
            explanation='Explicit synthetic developer test exclusion; not an actual run/review', review_sha256='a'*64)
            for trial in h.matrix(study) if trial.case_id not in aliases))
    return root, request, objects


def test_nine_case_three_family_private_export_and_reproduction(dataset, tmp_path):
    root, request, objects = dataset
    assert len(request.cases) == 9 and len(request.captures) == 3 and len(request.exclusions) == 6
    assert all(case.classification == 'unresolved' and case.ground_truth_review_sha256 is None for case in request.cases)
    assert c.parse_request(request.model_dump(mode='json')) == request
    output = tmp_path / 'export'
    manifest, digest = c.export_collection(request, root, output)
    result = c.validate_reproduction(output, expected_manifest_sha256=digest)
    assert result['status'] == 'manifest_validation_passed'
    assert result['execution'] == 'not_run' and result['clean_tool_reproduction'] == 'not_established'
    assert result['planned_trials'] == 9
    assert isinstance(manifest.request, c.FamilyCollectionRequest)
    assert canonical_hash(manifest.request.corpus) == request.study.registry_sha256
    report = json.loads((output / 'collection.json').read_bytes())
    assert report['status'] == 'collected_not_scored' and report['captured_trials'] == 3
    assert all(row['evidence_kinds'] == ['mock'] for row in report['trials'])
    for index, obj in enumerate(objects):
        target = output / 'trials' / f'{index:03d}'
        assert (target / 'measurements.jsonl').read_bytes() == obj.log.path.read_bytes()
        for blob in obj.root.glob('blob-*.bin'):
            assert (target / blob.name).read_bytes() == blob.read_bytes()
    with pytest.raises(FileExistsError): c.export_collection(request, root, output)


@pytest.mark.parametrize('field', ['registry', 'profile', 'source', 'checker', 'contract', 'truth',
                                  'missing_case', 'duplicate_case', 'missing_trial', 'classification'])
def test_family_frozen_identity_matrix_and_unclassified_disposition_reject_drift(dataset, field):
    _, request, _ = dataset
    value = request.model_dump(mode='json')
    if field == 'registry': value['study']['registry_sha256'] = 'f'*64
    elif field == 'profile': value['cases'][0]['profile'] = 'rtl.fifo.v1'
    elif field in {'source', 'checker', 'contract'}: value['cases'][0][field + '_sha256'] = 'f'*64
    elif field == 'truth': value['cases'][0]['ground_truth_review_sha256'] = 'f'*64
    elif field == 'missing_case': value['cases'].pop()
    elif field == 'duplicate_case': value['cases'][1] = value['cases'][0]
    elif field == 'missing_trial': value['exclusions'].pop()
    elif field == 'classification': value['cases'][0]['classification'] = 'seeded_defect'
    with pytest.raises((ValueError, ValidationError)): c.parse_request(value)


def test_legacy_request_does_not_silently_accept_family_schema(dataset):
    _, request, _ = dataset
    value = request.model_dump(mode='json')
    value['schema_version'] = 'evaluation.collection-request.v1'
    with pytest.raises(ValidationError): c.parse_request(value)
    with pytest.raises(ValidationError):
        c.CaseBinding(case_id='case-0001', source_sha256='a'*64, checker_sha256='a'*64,
                      contract_sha256='a'*64, classification='unresolved', ground_truth_review_sha256=None)


def test_rehashed_alternative_corpus_cannot_replace_the_frozen_recipe(dataset):
    _, request, _ = dataset
    value = request.model_dump(mode='json')
    case = value['corpus']['cases'][0]
    descriptor = get_adapter(case['adapter_id'])
    for plan in case['plans'][:2]:  # Detection plus its exact replay.
        plan['experiment']['seed'] = 1
        typed = descriptor.validate_experiment(plan['experiment'])
        plan['identity']['experiment_hash'] = canonical_hash(typed)
    alternative = family_corpus.validate_corpus(value['corpus'])
    value['study']['registry_sha256'] = canonical_hash(alternative)
    assert value['study']['registry_sha256'] != request.study.registry_sha256
    with pytest.raises(ValueError, match='frozen_corpus_recipe_differs'):
        c.parse_request(value)
    with pytest.raises(ValueError, match='corpus_recipe_changed'):
        f.FamilyHarness(request.captures[0].directory, value['study'], request.captures[0].trial, alternative,
                        now=lambda: fake.datetime.now(fake.timezone.utc))


def test_existing_cli_collects_family_then_validates_without_execution(dataset, tmp_path, capsys):
    root, request, _ = dataset
    path = tmp_path / 'request.json'; path.write_text(request.model_dump_json(), encoding='utf-8')
    output = tmp_path / 'cli-export'
    assert collect_main(['--request', str(path), '--root', str(root), '--output', str(output)]) == 0
    message = json.loads(capsys.readouterr().out)
    assert message['status'] == 'collected_not_scored'
    assert reproduce_main(['--root', str(output), '--expected-manifest-sha256', message['manifest_sha256']]) == 0
    assert json.loads(capsys.readouterr().out)['execution'] == 'not_run'
    assert collect_main(['--request', str(path), '--root', str(root), '--output', str(output)]) == 1
    assert json.loads(capsys.readouterr().out)['diagnostic'] == 'invalid_or_changed_input_or_output'
