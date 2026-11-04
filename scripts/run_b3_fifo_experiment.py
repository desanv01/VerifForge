"""Run one hash-bound, public, model-proposed curated FIFO experiment.

Only the manual B3 workflow calls this script. No provider, database, uploaded
code, shell, user-selected source path or configurable checker is supported.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path('/evidence')
MAX_INPUT = 16_384
MAX_EVIDENCE = 16 * 1024 * 1024


def require(value, code):
    if not value:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def decode_envelope(text, expected_sha256):
    require(type(text) is str and len(text) <= 4 * ((MAX_INPUT+2)//3), 'input_size')
    require(type(expected_sha256) is str and re.fullmatch('[0-9a-f]{64}',expected_sha256), 'input_digest')
    raw=base64.b64decode(text,validate=True)
    require(0 < len(raw) <= MAX_INPUT and sha(raw)==expected_sha256,'envelope_digest')
    def pairs(items):
        result={}
        for key,value in items:
            require(key not in result,'duplicate_key')
            result[key]=value
        return result
    row=json.loads(raw,object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite_json')))
    require(type(row) is dict and set(row)=={'schema','commit','external_round_id',
        'external_request_sha256','external_completion_sha256','experiment','experiment_sha256',
        'source_file_sha256','checker_sha256','contract_sha256'},'envelope_shape')
    require(row['schema']=='veriforge.b3.public-fifo-experiment.v1','envelope_schema')
    require(type(row['commit']) is str and re.fullmatch('[0-9a-f]{40}',row['commit']),'commit_shape')
    require(str(UUID(row['external_round_id']))==row['external_round_id'],'external_round_shape')
    for key in ('external_request_sha256','external_completion_sha256','experiment_sha256',
                'source_file_sha256','checker_sha256','contract_sha256'):
        require(type(row[key]) is str and re.fullmatch('[0-9a-f]{64}',row[key]),'envelope_pin_shape')
    return raw,row


def validate(row, *, expected_commit, root=ROOT):
    from veriforge.contracts import Experiment, canonical_hash
    from veriforge.rtl import oracle
    from veriforge.rtl.runner import reviewed_contract
    require(row['commit']==expected_commit,'commit_identity')
    plan=Experiment.model_validate(row['experiment'])
    require(plan.fixture_id in {'fifo_correct','fifo_reset_bug'} and plan.target=='fifo','curated_fifo_only')
    require(plan.parameters.width==8 and plan.parameters.depth==4,'supported_configuration')
    require(plan.budget.max_cycles<=32 and plan.budget.wall_time_seconds<=60 and
            plan.budget.max_artifact_bytes<=10_485_760,'experiment_budget')
    require(canonical_hash(plan)==row['experiment_sha256'],'experiment_identity')
    require(sha((root/'targets/fifo/fifo.sv').read_bytes())==row['source_file_sha256'],'source_identity')
    require(sha(Path(oracle.__file__).read_bytes())==row['checker_sha256'],'checker_identity')
    contract=reviewed_contract(plan.parameters)
    require(canonical_hash(contract)==row['contract_sha256'] and plan.contract_id==contract.contract_id,
            'contract_identity')
    require(set(plan.objective_ids)<={r.requirement_id for r in contract.requirements},'reviewed_objectives')
    # Experiment's own semantic validator enforces reset-before-traffic and
    # legal op values; count expanded stimulus independently as a second bound.
    require(0 < len(oracle.expand(plan)) <= 32,'expanded_cycle_limit')
    return plan


def run():
    # The container receives only these public workflow inputs. No model or
    # other credential variable is ever passed by the workflow.
    require(not any(key in os.environ for key in ('MODEL_API_KEY','META_API_KEY','OPENAI_API_KEY',
        'GITHUB_TOKEN','GH_TOKEN','ACTIONS_RUNTIME_TOKEN')),'credential_in_tool_role')
    raw,row=decode_envelope(os.environ.get('B3_ENVELOPE_BASE64'),os.environ.get('B3_ENVELOPE_SHA256'))
    plan=validate(row,expected_commit=os.environ.get('B3_EXPECTED_COMMIT'))
    require(OUTPUT.is_dir() and not OUTPUT.is_symlink() and not any(OUTPUT.iterdir()),'fresh_output_required')
    # The exact admitted public model experiment is durable before tool dispatch.
    with (OUTPUT/'admitted-envelope.json').open('xb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    with (OUTPUT/'native.intent.json').open('xb') as stream:
        stream.write(canonical({'envelope_sha256':sha(raw),'experiment_sha256':row['experiment_sha256'],
            'commit':row['commit'],'external_round_id':row['external_round_id'],'native_executions':1}))
        stream.flush();os.fsync(stream.fileno())
    from veriforge.rtl.runner import run_experiment
    result=run_experiment(plan,OUTPUT/'attempt')  # Exactly one fixed real call.
    files=[]
    for path in sorted(OUTPUT.rglob('*')):
        require(not path.is_symlink(),'evidence_symlink')
        if path.is_file():
            require(path.stat().st_size<=MAX_EVIDENCE,'evidence_file_cap')
            files.append({'path':path.relative_to(OUTPUT).as_posix(),'bytes':path.stat().st_size,
                          'sha256':sha(path.read_bytes())})
    require(sum(item['bytes'] for item in files)<=MAX_EVIDENCE-65536,'evidence_total_cap')
    receipt={'schema':'veriforge.b3.public-fifo-result.v1','commit':row['commit'],
        'envelope_sha256':sha(raw),'external_round_id':row['external_round_id'],
        'external_request_sha256':row['external_request_sha256'],
        'external_completion_sha256':row['external_completion_sha256'],
        'experiment_sha256':row['experiment_sha256'],'native_executions':1,
        'execution_status':result.execution_status.value,'verdict':result.verdict.value if result.verdict else None,
        'failure_signature':result.failure_signature.model_dump(mode='json') if result.failure_signature else None,
        'identity':result.identity.model_dump(mode='json') if result.identity else None,
        'tool_versions':[tool.model_dump(mode='json') for tool in result.tool_versions],
        'files':files,'acceptance':False}
    with (OUTPUT/'tool-receipt.json').open('xb') as stream:
        stream.write(canonical(receipt));stream.flush();os.fsync(stream.fileno())
    require(result.execution_status.value=='completed' and result.verdict.value in
            {'pass_within_scope','design_mismatch','property_failure'},'native_execution_not_checked')
    print(json.dumps({'status':result.execution_status.value,'verdict':result.verdict.value,
                      'native_executions':1,'acceptance':False}))


def stage_evidence(source, destination):
    """Bound uploads even after timeout/partial failure; never execute evidence."""
    import shutil
    source=Path(source);destination=Path(destination)
    require(not destination.exists(),'fresh_upload_stage_required')
    destination.mkdir(parents=True)
    rows=[];total=0;reason=None
    if not source.is_dir() or source.is_symlink():
        reason='evidence_missing_or_unsafe'
    else:
        for path in source.rglob('*'):
            if path.is_symlink():reason='evidence_symlink';break
            if path.is_file():
                size=path.stat().st_size;total+=size
                rows.append((path,size))
                if len(rows)>100 or total>MAX_EVIDENCE:
                    reason='evidence_exceeded_upload_bound';break
    if reason:
        (destination/'upload-status.json').write_bytes(canonical({'state':reason,
            'observed_files':len(rows),'observed_bytes':total,'native_acceptance':False,
            'complete_native_evidence_uploaded':False,'original_runner_evidence_preserved_until_runner_disposal':True}))
        return False
    for path,size in rows:
        require(path.stat().st_size==size,'evidence_changed_during_stage')
        target=destination/path.relative_to(source)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
        require(target.stat().st_size==size,'evidence_changed_during_copy')
    return True


if __name__=='__main__':
    try:
        if sys.argv[1:]==['--stage-evidence']:
            stage_evidence(ROOT/'ci-artifacts/b3-fifo',ROOT/'ci-artifacts/b3-upload')
        else:
            require(not sys.argv[1:],'unsupported_arguments')
            run()
    except Exception as error:
        # Public input/receipt already preserves exact data. Don't emit arbitrary
        # exception content or environment into workflow logs.
        print(json.dumps({'status':'failed_preserved','error_type':type(error).__name__,
                          'acceptance':False}),file=sys.stderr)
        raise SystemExit(1)
