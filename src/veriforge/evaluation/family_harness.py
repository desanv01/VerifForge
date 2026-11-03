"""Bounded disclosed-development three-family collection, not a durable engine.

FamilyStudy/Trial + frozen CorpusManifest -> FamilyHarness(root,...). create()
creates the existing journal/measurement ledger. read() returns events/head/
pending. advance(expected_head=...,runtime=...,provider=...) performs at most one
host-injected effect. Pending intents are never resent; reopening may project
committed receipts into the ledger, never infer remote settlement. The inherited
Journal/BlobStore/EvaluationLog and accounting are reused, not cloned.

Providers receive FamilyInput only; fixture/source/truth/corpus plans stay private.
Runtime returns a host-independently-checked AttemptResult and exact artifact
bytes. This controller validates identities/bytes/bounds, not native authenticity
or a new oracle. Host must enforce process cancellation/limits/settlement; no
HTTP, SDK, simulator, credentials or service client is constructed here.
"""
from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, TypeAdapter

from veriforge.adapters import ValidatedExperiment, get_adapter
from veriforge.arithmetic.contracts import Operation as ProductOperation
from veriforge.axi_lite.contracts import AxiLiteCycle
from veriforge.contracts import AttemptResult, FifoOperation, canonical_hash
from . import family_corpus as corpus, harness as h, records as r

encoded, digest, identity, require = h.encoded, h.digest, h.identity, h.require
CAPABILITIES = {'rtl.fifo.v1': 'queue', 'rtl.arithmetic.v1': 'unsigned_product',
                'rtl.axi_lite.v1': 'bounded_register_bus'}


class FamilyStudy(h.Study):
    schema_version: Literal['evaluation.family-study.v1'] = 'evaluation.family-study.v1'
    cases: Annotated[tuple[corpus.PublicCase, ...], Field(min_length=1, max_length=9)]
    visibility: Literal['disclosed_development'] = 'disclosed_development'


class QueueProposal(r.RecordModel):
    capability: Literal['queue']
    operations: Annotated[tuple[FifoOperation, ...], Field(min_length=1, max_length=1000)]


class ProductProposal(r.RecordModel):
    capability: Literal['unsigned_product']
    operations: Annotated[tuple[ProductOperation, ...], Field(min_length=1, max_length=1000)]


class BusProposal(r.RecordModel):
    capability: Literal['bounded_register_bus']
    cycles: Annotated[tuple[AxiLiteCycle, ...], Field(min_length=1, max_length=1000)]


PROPOSAL = TypeAdapter(Annotated[QueueProposal | ProductProposal | BusProposal, Field(discriminator='capability')])


class Feedback(r.RecordModel):
    attempt: r.RecordReference
    execution_status: str
    verdict: str | None
    first_cycle: h.N | None


class FamilyInput(r.RecordModel):
    schema_version: Literal['evaluation.neutral-family-input.v1'] = 'evaluation.neutral-family-input.v1'
    case: corpus.PublicCase
    capability: Literal['queue', 'unsigned_product', 'bounded_register_bus']
    parameters: Annotated[str, Field(max_length=4096)]  # Canonical public parameter JSON only.
    requirements: tuple[str, ...]
    semantics: tuple[str, ...]
    max_cycles: h.N
    max_output_tokens: h.N
    seed: h.Seed
    feedback: Annotated[tuple[Feedback, ...], Field(max_length=16)] = ()


class CapturedArtifact(r.RecordModel):
    model_config = ConfigDict(ser_json_bytes='base64', val_json_bytes='base64')
    reference: r.ArtifactReference
    data: Annotated[bytes, Field(max_length=256 * 1024)]


class RuntimeReceipt(r.RecordModel):
    operation_id: r.Identifier
    experiment_sha256: r.Sha256
    evidence_kind: h.Evidence
    result: AttemptResult
    artifacts: Annotated[tuple[CapturedArtifact, ...], Field(max_length=32)] = ()


class Freeze(h.Freeze):
    study: FamilyStudy


class Intent(h.Intent):
    proposal: Annotated[ValidatedExperiment, Field(discriminator='target')] | None = None
    request: FamilyInput | None = None


class Receipt(h.Receipt):
    proposal: Annotated[ValidatedExperiment, Field(discriminator='target')] | None = None


EVENT = TypeAdapter(Annotated[Freeze | Intent | Receipt | h.Terminal, Field(discriminator='kind')])


class FamilyHarness(h.Harness):
    def __init__(self, root, study, trial, manifest, *, now, control=None, model_control=None, cancelled=None):
        self.study = FamilyStudy.model_validate_json(encoded(study))
        self.trial = h.Trial.model_validate_json(encoded(trial))
        self.manifest = corpus.validate_corpus(manifest)
        require(canonical_hash(self.manifest) == self.study.registry_sha256, 'corpus_changed')
        require(self.study.registry_sha256 == canonical_hash(corpus.build_corpus()), 'corpus_recipe_changed')
        require(self.trial in h.matrix(self.study), 'trial_not_in_matrix')
        self.case = next(c for c in self.manifest.cases if c.case_id == self.trial.case_id)
        self.descriptor = get_adapter(self.case.adapter_id)
        self.root, self.now = Path(root), now
        self.control, self.model_control, self.cancelled = control, model_control, cancelled
        self.campaign_id = identity(canonical_hash(self.study), self.trial.model_dump(mode='json'))
        self.journal = h.Journal(root, event_adapter=EVENT)
        self.log = r.EvaluationLog(root, 'measurements.jsonl')
        self.blobs = h.BlobStore(root)
        self.registry = self  # Existing terminal projector calls metadata_safe.
        source = self.case.plans[0].identity
        self.scope = r.Scope(evaluation_revision=canonical_hash(self.study), campaign_id=self.campaign_id,
            case_id=self.trial.case_id, domain='rtl', profile=self.case.adapter_id,
            strategy=self.trial.strategy, strategy_sha256=self.study.strategy_sha256,
            replicate=self.trial.replicate, seed=self.trial.seed, source_sha256=source.source_hash,
            checker_sha256=source.checker_hash, contract_sha256=source.contract_hash,
            environment_sha256=self.study.environment_sha256, information_policy_sha256=self.study.information_policy_sha256,
            visibility='disclosed_development', context_audit=self.study.context_audit, cache_state='unknown')

    def metadata_safe(self, raw):
        require(not any(c.fixture_id.encode() in raw for c in self.manifest.cases), 'private_context_blocked')

    def admitted(self, *, model=False):
        try:
            okay = callable(self.control) and self.control() == 'RUNNING'
            okay = okay and (self.cancelled is None or not self.cancelled())
            if model:
                okay = okay and callable(self.model_control) and self.model_control() == 'RUNNING'
            return bool(okay)
        except Exception:
            return False

    def create(self):
        require(self.admitted(), 'control_held')
        self.journal.create()
        self.log.create()
        with self.journal.locked() as journal:
            journal.append(Freeze(study=self.study, trial=self.trial, scope=self.scope, admitted_at=self.clock()))

    def _read_state(self, journal):
        events, head = journal.read()
        require(events and isinstance(events[0], Freeze) and events[0].study == self.study and
                events[0].trial == self.trial and events[0].scope == self.scope, 'freeze_changed')
        pending, terminal, seen = None, False, set()
        counts = {'model': 0, 'tool': 0}
        for event in events[1:]:
            require(not terminal and not isinstance(event, Freeze), 'journal_state')
            if isinstance(event, Intent):
                require(pending is None and event.operation_id not in seen, 'journal_state')
                require(event.operation_id == identity(self.campaign_id, event.stage, event.round_index), 'journal_state')
                require(event.round_index == counts[event.stage], 'journal_state')
                counts[event.stage] += 1
                if event.stage == 'tool':
                    require(event.proposal is not None and event.request is None and
                            canonical_hash(event.proposal) == event.request_sha256, 'journal_state')
                    self._validate_plan(event.proposal)
                    require(event.reserve == h.amounts(attempts=1, cycles=self.study.limits.per_attempt_cycles,
                        tool_seconds=self.study.limits.per_attempt_seconds,
                        artifact_bytes=self.study.limits.per_attempt_artifact_bytes), 'journal_reservation_changed')
                else:
                    require(event.request is not None and event.proposal is None and
                            digest(encoded(event.request)) == event.request_sha256, 'journal_state')
                    self.metadata_safe(encoded(event.request))
                    require(event.replay_parent is None and event.reserve == h.amounts(
                        model_tokens=len(encoded(event.request)) + 1024 + event.request.max_output_tokens,
                        artifact_bytes=self.study.limits.response_bytes + self.study.limits.per_attempt_artifact_bytes),
                        'journal_reservation_changed')
                seen.add(event.operation_id)
                pending = event
            elif isinstance(event, Receipt):
                require(pending is not None and event.operation_id == pending.operation_id and
                        event.record.record_id == event.operation_id and event.record.scope == self.scope and
                        isinstance(event.record, r.ModelRoundRecord) == (pending.stage == 'model'), 'journal_state')
                if event.proposal is not None:
                    self._validate_plan(event.proposal)
                ref = event.record.accepted_plan
                if ref is not None:
                    require(event.proposal is not None and self.blobs.read(ref) == encoded(event.proposal), 'saved_plan_changed')
                if isinstance(event.record, r.AttemptRecord):
                    require(event.proposal == pending.proposal and event.record.parent_attempt == pending.replay_parent,
                            'saved_plan_changed')
                    require(event.record.attempt.identity.experiment_hash == canonical_hash(pending.proposal), 'saved_plan_changed')
                    for artifact in event.record.attempt.artifacts:
                        self.blobs.read(artifact)
                else:
                    require(event.record.prompt_sha256 == pending.request_sha256 and
                        event.record.feedback == tuple(f.attempt for f in pending.request.feedback), 'saved_feedback_changed')
                    if event.record.response:
                        self.blobs.read(event.record.response)
                pending = None
            elif isinstance(event, h.Terminal):
                require(pending is None and event.record.scope == self.scope, 'journal_state')
                terminal = True
        return events, head, pending

    def _validate_plan(self, plan):
        plan = self.descriptor.validate_experiment(plan)
        require(plan.fixture_id == self.case.fixture_id and
                plan.parameters == self.case.plans[0].experiment.parameters, 'plan_binding_invalid')
        expected = self.descriptor.expected_identity(plan)
        require((expected['source_hash'], expected['checker_hash'], expected['contract_hash']) ==
                (self.scope.source_sha256, self.scope.checker_sha256, self.scope.contract_sha256), 'installed_identity_changed')
        return plan

    def _bind(self, proposal, operation_id):
        require(proposal.capability == CAPABILITIES[self.case.adapter_id], 'capability_mismatch')
        limits = self.study.limits
        payload = self.case.plans[0].experiment.model_dump(mode='json')
        payload.update(experiment_id=operation_id, seed=self.trial.seed,
                       budget=dict(max_cycles=limits.per_attempt_cycles, wall_time_seconds=limits.per_attempt_seconds,
                                   max_artifact_bytes=limits.per_attempt_artifact_bytes))
        key = 'cycles' if isinstance(proposal, BusProposal) else 'operations'
        payload[key] = proposal.model_dump(mode='json')[key]
        return self._validate_plan(payload)

    def _directed(self, index, operation_id):
        plans = [p for p in self.case.plans if p.purpose != 'replay']
        if index >= len(plans):
            return None
        payload = plans[index].experiment.model_dump(mode='json')
        key = 'cycles' if self.case.adapter_id == 'rtl.axi_lite.v1' else 'operations'
        proposal = PROPOSAL.validate_python({'capability': CAPABILITIES[self.case.adapter_id], key: payload[key]})
        return self._bind(proposal, operation_id)

    def _random(self, index, operation_id):
        rng = random.Random(int(digest(encoded((CAPABILITIES[self.case.adapter_id], self.trial.seed, index))), 16))
        count = min(32, self.study.limits.per_attempt_cycles)
        capability = CAPABILITIES[self.case.adapter_id]
        if capability == 'queue':
            rows = [{'op': 'reset'}]
            for _ in range(count - 1):
                op = rng.choice(('write', 'read', 'idle'))
                rows.append({'op': op, **({'value': rng.randrange(256)} if op == 'write' else {})})
            return self._bind(QueueProposal(capability='queue', operations=rows), operation_id)
        if capability == 'unsigned_product':
            require(count >= 4, 'random_cycle_budget')
            rows = [{'op': 'reset'}] + [{'op': 'multiply', 'a': rng.randrange(256), 'b': rng.randrange(256)}
                for _ in range(count - 3)] + [{'op': 'idle', 'cycles': 2}]
            return self._bind(ProductProposal(capability='unsigned_product', operations=rows), operation_id)
        # Only existing fixed legal bus schedules; random payloads/byte lanes,
        # never an unqualified adaptive AXI manager or arbitrary VALID toggles.
        from veriforge.axi_lite.runner import witness_experiment
        base = witness_experiment(rng.choice(('order', 'strobe', 'response'))).model_dump(mode='json')['cycles']
        for row in base:
            if row['w_valid']:
                row['w_data'], row['w_strb'] = rng.randrange(2**32), rng.randrange(16)
        return self._bind(BusProposal(capability='bounded_register_bus', cycles=base), operation_id)

    def _request(self, entries):
        feedback = tuple(Feedback(attempt=h.reference(e), execution_status=e.record.attempt.execution_status.value,
            verdict=e.record.attempt.verdict.value if e.record.attempt.verdict else None,
            first_cycle=e.record.attempt.failure_signature.first_cycle if e.record.attempt.failure_signature else None)
            for e in entries if isinstance(e.record, r.AttemptRecord)) if self.trial.strategy == 'meta_feedback' else ()
        plan = self.case.plans[0].experiment
        contract = self.descriptor.contract_for(plan)
        return FamilyInput(case=corpus.PublicCase(case_id=self.case.case_id), capability=CAPABILITIES[self.case.adapter_id],
            parameters=encoded(plan.parameters).decode(), requirements=plan.objective_ids,
            semantics=tuple(requirement.description for requirement in contract.requirements) + contract.assumptions,
            max_cycles=self.study.limits.per_attempt_cycles, max_output_tokens=self.study.limits.feedback_output_tokens,
            seed=self.trial.seed, feedback=feedback)

    def advance(self, *, expected_head, provider=None, runtime=None, replay_parent_id=None):
        with self.journal.locked() as journal:
            require(digest(Path(__file__).read_bytes()) == self.study.strategy_sha256, 'strategy_source_changed')
            corpus.validate_corpus(self.manifest)
            events, head, pending = self._read_state(journal)
            require(head == expected_head, 'head_changed')
            entries = self.sync(events)
            require(pending is None, 'recovery_required_no_automatic_redispatch')
            if isinstance(events[-1], h.Terminal):
                return events[-1].record.stop_reason
            require(self.admitted(), 'control_held')
            receipts = [e for e in events if isinstance(e, Receipt)]
            tools = [e for e in receipts if isinstance(e.record, r.AttemptRecord)]
            models = [e for e in receipts if isinstance(e.record, r.ModelRoundRecord)]
            if tools and (tools[-1].record.attempt.execution_status.value != 'completed' or
                          tools[-1].record.attempt.verdict.value not in {'pass_within_scope', 'design_mismatch', 'property_failure'}):
                return self.terminal(events, entries, 'infrastructure_failure', journal=journal)
            if models and models[-1].record.status != 'completed':
                return self.terminal(events, entries, 'blocked', journal=journal)
            if any(e.record.usage.state == 'unknown' for e in models):
                return self.terminal(events, entries, 'blocked', journal=journal)
            limits = self.study.limits
            parent, experiment = None, None
            if replay_parent_id:
                original = next((e for e in entries if isinstance(e.record, r.AttemptRecord) and
                                 e.record.record_id == replay_parent_id), None)
                require(original is not None, 'replay_parent_invalid')
                parent = h.reference(original)
                experiment = next(e.proposal for e in tools if e.operation_id == replay_parent_id)
            model = self.trial.strategy.startswith('meta_') and parent is None and (not models or len(tools) >= len(models))
            if model and self.trial.strategy == 'meta_one_pass' and models:
                return self.terminal(events, entries, 'completed', journal=journal)
            index, stage = (len(models), 'model') if model else (len(tools), 'tool')
            operation_id = identity(self.campaign_id, stage, index)
            request = self._request(entries) if model else None
            if not model and experiment is None:
                experiment = (models[-1].proposal if self.trial.strategy.startswith('meta_') else
                    self._directed(index, operation_id) if self.trial.strategy == 'directed_manual' else self._random(index, operation_id))
                if experiment is None:
                    return self.terminal(events, entries, 'completed', journal=journal)
            reserve = (h.amounts(model_tokens=len(encoded(request)) + 1024 + request.max_output_tokens,
                                 artifact_bytes=limits.response_bytes + limits.per_attempt_artifact_bytes) if model else
                       h.amounts(attempts=1, cycles=limits.per_attempt_cycles, tool_seconds=limits.per_attempt_seconds,
                                 artifact_bytes=limits.per_attempt_artifact_bytes))
            prior, allocated = self.reserved(events), self.allocated()
            elapsed = (self.clock() - events[0].admitted_at).total_seconds()
            phase = limits.per_model_seconds if model else limits.per_attempt_seconds
            reserved_time = sum(limits.per_model_seconds if e.stage == 'model' else limits.per_attempt_seconds
                                for e in events if isinstance(e, Intent))
            if (len(tools) >= limits.attempts or len(models) >= limits.attempts and model or elapsed < 0 or
                elapsed + phase > limits.end_to_end_seconds or reserved_time + phase > limits.end_to_end_seconds or
                any(getattr(prior, k) + getattr(reserve, k) > getattr(allocated, k) for k in r.Amounts.model_fields)):
                return self.terminal(events, entries, 'budget_exhausted', journal=journal)
            require(provider is not None if model else runtime is not None, 'adapter_required')
            require(self.admitted(model=model), 'control_held')
            if model:
                require(all(getattr(provider, key, None) == getattr(self.study, key) for key in
                    ('requested_model', 'model_identity_sha256', 'generation_parameters_sha256')) and
                    provider.evidence_kind == self.study.model_evidence_kind, 'adapter_identity_invalid')
                self.metadata_safe(encoded(request))
            else:
                require(runtime.evidence_kind == self.study.tool_evidence_kind, 'adapter_identity_invalid')
            request_hash = digest(encoded(request)) if model else canonical_hash(experiment)
            intent = Intent(operation_id=operation_id, stage=stage, round_index=index, reserve=reserve,
                request_sha256=request_hash, proposal=experiment, request=request, replay_parent=parent)
            journal.append(intent)
            events = (*events, intent)
            try:
                require(self.admitted(model=model), 'control_held')
                started = time.monotonic()
                if model:
                    receipt = self._model(provider, operation_id, request, events, entries)
                else:
                    receipt = self._tool(runtime, operation_id, experiment, events, parent)
                require(self.admitted(model=model), 'control_held')
                require(time.monotonic() - started <= phase and
                        (self.clock() - events[0].admitted_at).total_seconds() <= limits.end_to_end_seconds,
                        'late_effect_requires_review')
                receipt = receipt.model_copy(update={'record': receipt.record.model_copy(update={'budget': self.budget((*events, receipt))})})
                journal.append(receipt)
                self.sync((*events, receipt))
                return 'progress'
            except Exception:
                raise h.HarnessError('effect_unsettled_review_required') from None

    def _model(self, provider, operation_id, request, events, entries):
        captured = h.ProposalReceipt.model_validate_json(encoded(provider.propose(operation_id, request,
            timeout_seconds=self.study.limits.per_model_seconds, admitted=lambda: self.admitted(model=True))))
        limits = self.study.limits
        require(captured.operation_id == operation_id and captured.request_sha256 == digest(encoded(request)) and
                captured.evidence_kind == self.study.model_evidence_kind and captured.returned_model == self.study.requested_model and
                captured.elapsed_seconds <= limits.per_model_seconds and captured.usage.state != 'not_called', 'provider_receipt_invalid')
        if captured.usage.state == 'known':
            require(captured.usage.output_tokens <= request.max_output_tokens and
                    captured.usage.input_tokens + captured.usage.output_tokens <= events[-1].reserve.model_tokens, 'model_budget_exceeded')
        require(captured.response is None or len(captured.response) <= limits.response_bytes, 'response_bound')
        response = self.blobs.put(captured.response) if captured.response is not None else None
        plan, status, reason = None, captured.state, None
        if status == 'completed':
            require(response is not None, 'response_missing')
            try:
                proposal = PROPOSAL.validate_python(h.ledger_json(captured.response))
                plan = self._bind(proposal, identity(self.campaign_id, 'tool', sum(isinstance(e.record, r.AttemptRecord) for e in events if isinstance(e, Receipt))))
                require(len(encoded(plan)) <= limits.per_attempt_artifact_bytes, 'proposal_bound')
            except Exception:
                status, reason = 'rejected', 'typed_family_proposal_invalid'
        plan_ref = self.blobs.put(encoded(plan)) if plan else None
        record = r.ModelRoundRecord(**self.common(events, operation_id, captured.evidence_kind), model_round_id=operation_id,
            provider='meta', requested_model=self.study.requested_model, returned_model=captured.returned_model,
            model_version=captured.model_version, prompt_sha256=digest(encoded(request)),
            response_schema_sha256=digest(encoded(PROPOSAL.json_schema())), generation_parameters_sha256=self.study.generation_parameters_sha256,
            provider_seed=None, status=status, usage=captured.usage, response=response, accepted_plan=plan_ref,
            rejection_reason=reason, feedback=tuple(f.attempt for f in request.feedback))
        return Receipt(operation_id=operation_id, record=record, proposal=plan)

    def _tool(self, runtime, operation_id, experiment, events, parent):
        raw = runtime.execute(operation_id, experiment,
            timeout_seconds=self.study.limits.per_attempt_seconds, admitted=lambda: self.admitted())
        captured = RuntimeReceipt.model_validate(raw.model_dump(mode='python') if isinstance(raw, RuntimeReceipt) else raw)
        result, limits = captured.result, self.study.limits
        require(captured.operation_id == operation_id and captured.experiment_sha256 == canonical_hash(experiment) and
                captured.evidence_kind == self.study.tool_evidence_kind and result.identity is not None and
                result.experiment_id == experiment.experiment_id, 'runtime_receipt_invalid')
        require(all(getattr(result.identity, k) == v for k, v in self.descriptor.expected_identity(experiment).items()) and
                result.identity.environment_hash == self.study.environment_sha256, 'runtime_identity_changed')
        require(result.execution_status.value in {'completed', 'infrastructure_error', 'cancelled'} and
                result.usage.cycles <= limits.per_attempt_cycles and result.usage.wall_time_seconds <= limits.per_attempt_seconds,
                'runtime_budget_or_status_invalid')
        refs = []
        require(len({a.reference.artifact_id for a in captured.artifacts}) == len(captured.artifacts) and
                tuple(a.reference for a in captured.artifacts) == result.artifacts, 'runtime_artifact_binding')
        for artifact in captured.artifacts:
            require(len(artifact.data) == artifact.reference.size_bytes and digest(artifact.data) == artifact.reference.sha256,
                    'runtime_artifact_changed')
        raw_result, raw_plan = encoded(result), encoded(experiment)
        size = sum(len(artifact.data) for artifact in captured.artifacts) + len(raw_result) + len(raw_plan)
        require(size <= limits.per_attempt_artifact_bytes and len(raw_result) <= 256*1024 and len(raw_plan) <= 256*1024,
                'runtime_artifact_budget')
        # Check every byte against the reserved allowance before any blob write.
        for artifact in captured.artifacts:
            refs.append(self.blobs.put(artifact.data))
        refs.append(self.blobs.put(raw_result))
        plan_ref = self.blobs.put(raw_plan)
        summary = result.model_copy(update={'attempt_id': operation_id, 'trace': (), 'artifacts': tuple(refs),
            'uncertainties': ('Host-checked result; original exact result retained in private artifact',),
            'usage': result.usage.model_copy(update={'artifact_bytes': size})})
        record = r.AttemptRecord(**self.common(events, operation_id, captured.evidence_kind), experiment_record_id=operation_id,
            accepted_plan=plan_ref, attempt=summary, parent_attempt=parent, purpose='replay' if parent else 'detection')
        return Receipt(operation_id=operation_id, record=record, proposal=experiment)
