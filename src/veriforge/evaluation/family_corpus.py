"""Frozen three-family corpus INPUT, never an evaluation result or truth oracle.

Collector interface: build_corpus() -> private CorpusManifest; retain its JSON
and canonical_hash(manifest). validate_corpus(saved) rejects malformed/drifted
inputs. public_cases(manifest) returns only neutral aliases, safe for a planner
request envelope. execution_plans(manifest, alias) returns CorpusPlan records:
pass .experiment to the existing adapter runner and canonical_hash(plan) as the
frozen plan-record identity. Replay records point to their original plan ID and
carry the exact same experiment/stimulus; actual AttemptRecord parent references
must be supplied by the collector after execution. No environment/tool/model
identity, timing, result, ground truth or held-out claim is fabricated here.

The manifest and CorpusCase are TRUSTED PRIVATE data: never serialize them into
planner context. Aliases are not derived from source, fixture or answer hashes.
All cases are disclosed-development/unclassified. Curated inventory role is not
an independently established verdict. Existing FIFO harness/ledger unchanged;
this module does not extend its strategies or claim all-family planner support.
Selected configurations are FIFO width8/depth4, arithmetic width8/latency2,
and the existing fixed32-bit/4-register bounded AXI profile. Other legal parameter
combinations are not claimed covered. Equivalent/invalid/confirmed truth requires
separately reviewed existing GroundTruthRecord evidence, not inventory inference.
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from veriforge.adapters import ValidatedExperiment, get_adapter
from veriforge.contracts import Experiment, Identifier, Sha256, canonical_hash
from veriforge.evaluation.records import RecordModel

ADAPTERS = ('rtl.fifo.v1', 'rtl.arithmetic.v1', 'rtl.axi_lite.v1')
Alias = Annotated[str, Field(pattern=r'^case-[0-9]{4}$')]


def _stimulus(experiment, adapter_id):
    rows = get_adapter(adapter_id).expand_stimulus(experiment)
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


class PublicCase(RecordModel):
    """Only the neutral lookup alias; reviewed capability context is separate."""
    case_id: Alias


class PlanIdentity(RecordModel):
    source_hash: Sha256
    checker_hash: Sha256
    contract_hash: Sha256
    experiment_hash: Sha256


class CorpusPlan(RecordModel):
    record_id: Identifier
    purpose: Literal['detection', 'replay', 'neighbor', 'regression']
    parent_plan_id: Identifier | None = None
    experiment: Annotated[ValidatedExperiment, Field(discriminator='target')]
    identity: PlanIdentity
    stimulus_sha256: Sha256


class CorpusCase(RecordModel):
    case_id: Alias
    adapter_id: Literal['rtl.fifo.v1', 'rtl.arithmetic.v1', 'rtl.axi_lite.v1']
    fixture_id: Identifier
    inventory_role: Literal['curated_control', 'seeded_candidate']
    # No classification is inferred from a fixture's name/variant or a witness.
    disposition: Literal['unclassified'] = 'unclassified'
    plans: Annotated[tuple[CorpusPlan, ...], Field(min_length=4, max_length=32)]

    @model_validator(mode='after')
    def installed_binding(self):
        descriptor = get_adapter(self.adapter_id)
        source = descriptor.fixture_source(self.fixture_id)
        role = 'curated_control' if source['variant'] == 0 else 'seeded_candidate'
        if self.inventory_role != role:
            raise ValueError('corpus_inventory_binding')
        ids, stimuli = set(), set()
        by_id = {}
        for plan in self.plans:
            if plan.record_id in ids:
                raise ValueError('duplicate_corpus_plan')
            ids.add(plan.record_id)
            experiment = descriptor.validate_experiment(plan.experiment)
            if experiment.fixture_id != self.fixture_id:
                raise ValueError('corpus_fixture_binding')
            if descriptor.expected_identity(experiment) != plan.identity.model_dump():
                raise ValueError('corpus_installed_identity_changed')
            if _stimulus(experiment, self.adapter_id) != plan.stimulus_sha256:
                raise ValueError('corpus_stimulus_changed')
            if plan.purpose == 'replay':
                parent = by_id.get(plan.parent_plan_id)
                if parent is None or parent.purpose != 'detection' or plan.experiment != parent.experiment:
                    raise ValueError('corpus_replay_binding')
            else:
                if plan.parent_plan_id is not None or plan.stimulus_sha256 in stimuli:
                    raise ValueError('duplicate_or_parented_corpus_stimulus')
                stimuli.add(plan.stimulus_sha256)
            by_id[plan.record_id] = plan
        if {p.purpose for p in self.plans} != {'detection', 'replay', 'neighbor', 'regression'}:
            raise ValueError('corpus_missing_plan_role')
        return self


class CorpusManifest(RecordModel):
    schema_version: Literal['evaluation.family-corpus.v1'] = 'evaluation.family-corpus.v1'
    recipe_version: Literal['curated-three-family-1'] = 'curated-three-family-1'
    visibility: Literal['disclosed_development'] = 'disclosed_development'
    cases: Annotated[tuple[CorpusCase, ...], Field(min_length=9, max_length=9)]

    @model_validator(mode='after')
    def complete_inventory(self):
        aliases = tuple(c.case_id for c in self.cases)
        if aliases != tuple(f'case-{i:04d}' for i in range(1, 10)):
            raise ValueError('corpus_alias_order_or_duplicate')
        installed = {(adapter, fixture) for adapter in ADAPTERS for fixture in get_adapter(adapter).fixture_ids}
        actual = {(c.adapter_id, c.fixture_id) for c in self.cases}
        if actual != installed or len(actual) != len(self.cases):
            raise ValueError('corpus_inventory_incomplete_or_duplicate')
        ids = [p.record_id for c in self.cases for p in c.plans]
        if len(set(ids)) != len(ids):
            raise ValueError('duplicate_corpus_record_id')
        return self


def _plans(adapter_id, fixture_id):
    if adapter_id == 'rtl.fifo.v1':
        base = Experiment(experiment_id='stimulus-0001', fixture_id=fixture_id,
            objective_ids=('FIFO_BEHAVIOR',), seed=20261101,
            operations=({'op': 'reset'}, {'op': 'write', 'value': 37}, {'op': 'read'},
                        {'op': 'reset'}, {'op': 'read'}),
            budget={'max_cycles': 512, 'wall_time_seconds': 60, 'max_artifact_bytes': 10485760})
        neighbor = base.model_dump(mode='json')
        neighbor['operations'][1]['value'] = 38
        regression = base.model_dump(mode='json')
        regression['operations'] = ([{'op': 'reset'}, {'op': 'read'}] +
            [{'op': 'write', 'value': i} for i in range(5)] + [{'op': 'read'} for _ in range(5)])
        return [('detection', base), ('neighbor', Experiment.model_validate(neighbor)),
                ('regression', Experiment.model_validate(regression))]
    from veriforge.evidence.candidate_sources import build_family_validation_suite
    if adapter_id == 'rtl.arithmetic.v1':
        from veriforge.arithmetic.runner import directed_experiment
    else:
        from veriforge.axi_lite.runner import directed_experiment
    base = directed_experiment(fixture_id)
    suite = build_family_validation_suite(base)
    return [('detection' if case.role == 'witness' else case.role, case.experiment) for case in suite.cases]


def build_corpus() -> CorpusManifest:
    """Deterministic frozen installed inventory; reads source/checker, runs nothing."""
    fixtures = [(adapter, fixture) for adapter in ADAPTERS for fixture in get_adapter(adapter).fixture_ids]
    # Explicit neutral slot recipe; no alias contains/derives a private name/hash.
    if len(fixtures) != 9:
        raise ValueError('corpus_recipe_inventory_changed')
    cases = []
    for index, slot in enumerate((4, 0, 7, 2, 8, 1, 5, 3, 6), 1):
        adapter_id, fixture_id = fixtures[slot]
        descriptor = get_adapter(adapter_id)
        alias = f'case-{index:04d}'
        plans = []
        for ordinal, (purpose, experiment) in enumerate(_plans(adapter_id, fixture_id), 1):
            # Fixture/source remain private; stimulus IDs carry only neutral slots.
            payload = experiment.model_dump(mode='json')
            payload['experiment_id'] = f'{alias}-plan-{ordinal:02d}'
            experiment = descriptor.validate_experiment(payload)
            record = CorpusPlan(record_id=f'{alias}-record-{ordinal:02d}', purpose=purpose,
                experiment=experiment, identity=PlanIdentity(**descriptor.expected_identity(experiment)),
                stimulus_sha256=_stimulus(experiment, adapter_id))
            plans.append(record)
            if purpose == 'detection':
                plans.append(CorpusPlan(record_id=f'{alias}-replay', purpose='replay',
                    parent_plan_id=record.record_id, experiment=experiment, identity=record.identity,
                    stimulus_sha256=record.stimulus_sha256))
        cases.append(CorpusCase(case_id=alias, adapter_id=adapter_id, fixture_id=fixture_id,
            inventory_role='curated_control' if descriptor.fixture_source(fixture_id)['variant'] == 0 else 'seeded_candidate',
            plans=tuple(plans)))
    return CorpusManifest(cases=tuple(cases))


def validate_corpus(value) -> CorpusManifest:
    """Revalidate saved/model_copy data against active installed immutable checks."""
    if isinstance(value, CorpusManifest):
        value = value.model_dump(mode='json')
    return CorpusManifest.model_validate(value)


def public_cases(manifest: CorpusManifest) -> tuple[PublicCase, ...]:
    manifest = validate_corpus(manifest)
    return tuple(PublicCase(case_id=case.case_id) for case in manifest.cases)


def execution_plans(manifest: CorpusManifest, case_id: str) -> tuple[CorpusPlan, ...]:
    """Trusted collector only: each .experiment is an existing typed runner input."""
    manifest = validate_corpus(manifest)
    for case in manifest.cases:
        if case.case_id == case_id:
            return case.plans
    raise ValueError('unknown_corpus_case')
