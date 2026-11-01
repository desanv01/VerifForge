"""Closed native proof routing; exact legacy runtime is operator-registered only.

Registration is a trusted startup composition, never client/artifact dispatch.
The retained runtime owns its accepted operation-local batch/cache and checks.
No dependency snapshot, worker or policy is materialized/discovered on demand.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from types import ModuleType

from veriforge.contracts import native_tools as leaf
from veriforge.system_investigation import native_tool_records as native

LEGACY_HASH='083e9202c0f601ff1b80de7b6010e2aca250c1e5d33bf4de6f92c15a4996dea8'
CURRENT_HASH='1152215fe8c0b1cf6051821f8294737923ef7cc995bbf2777f7010ba15868760'
CURRENT_LEAF_HASH='d9b9b2b3c94cdce3c58e7536bd01d86448aac6e30348f5f9929ffbb39879fa81'
REPORT_KINDS=('riscv.recorded-report.v1','firmware.recorded-report.v1','riscv.recorded-report.v2')
MAX_NATIVE_PAGE=4
MAX_OPERATION_BYTES=17*1048576
OPERATION_SECONDS=8.0

class PolicyUnavailable(ValueError):
    pass

@dataclass(frozen=True)
class _Registered:
    module: ModuleType
    source_sha256: str

_REGISTERED=None
_UNREGISTERED=object()
_OPERATION=ContextVar('native_registered_policy_operation',default=None)

def _hash_source(path):
    path=Path(path)
    for part in (path,*path.parents):
        if part.is_symlink() or part.is_junction():
            raise PolicyUnavailable('Native policy source path differs')
    with path.open('rb',buffering=0) as stream:
        raw=stream.read(1048577)
    if not raw or len(raw)>1048576:
        raise PolicyUnavailable('Native policy source size differs')
    return hashlib.sha256(raw).hexdigest()

def _verify_registered(record):
    try:
        if _hash_source(record.module.__file__)!=record.source_sha256:
            raise PolicyUnavailable('Registered native policy source differs')
    except (OSError,AttributeError):
        raise PolicyUnavailable('Registered native policy source unavailable') from None

def configure_registered_validator(module,*,source_sha256=None):
    """Trusted operator startup only, after independent materialization acceptance.

    Caller supplies the host-approved source pin, not an envelope-claimed hash.
    Passing None explicitly disables future legacy reads. No worker is launched.
    In-flight operations keep their captured binding and verify its source again.
    """
    global _REGISTERED
    if module is None:
        _REGISTERED=None
        return
    if not isinstance(module,ModuleType) or not isinstance(source_sha256,str) or not re.fullmatch('[0-9a-f]{64}',source_sha256):
        raise ValueError('Host-approved native policy module and source pin required')
    expected={'LEGACY_HASH':LEGACY_HASH,'CURRENT_HASH':CURRENT_HASH,'REPORT_KINDS':REPORT_KINDS,
              'MAX_NATIVE_PAGE':MAX_NATIVE_PAGE,'MAX_OPERATION_BYTES':MAX_OPERATION_BYTES,'OPERATION_SECONDS':OPERATION_SECONDS}
    if any(getattr(module,name,None)!=value for name,value in expected.items()):
        raise ValueError('Registered native policy scope differs')
    if any(not callable(getattr(module,name,None)) for name in
           ('_verify_sources','validation_operation','select_route','read_legacy','queue_legacy','flush_legacy')):
        raise ValueError('Registered native policy interface differs')
    error=getattr(module,'PolicyUnavailable',None)
    if not isinstance(error,type) or not issubclass(error,ValueError) or not isinstance(getattr(module,'LegacyReadView',None),type):
        raise ValueError('Registered native policy types differ')
    record=_Registered(module,source_sha256)
    _verify_registered(record)
    module._verify_sources()  # Source/runtime check only; never a worker call.
    _REGISTERED=record
    return record

def _record():
    record=_OPERATION.get()
    if record is _UNREGISTERED: return None
    return _REGISTERED if record is None else record

@contextmanager
def validation_operation():
    if _OPERATION.get() is not None:
        yield
        return
    record=_REGISTERED
    token=_OPERATION.set(record if record is not None else _UNREGISTERED)
    try:
        if record is None:
            yield
        else:
            _verify_registered(record)
            try:
                with record.module.validation_operation(): yield
            except record.module.PolicyUnavailable:
                raise PolicyUnavailable('Registered native validation unavailable within operation bounds') from None
            finally:
                _verify_registered(record)
    finally:
        _OPERATION.reset(token)

def _call(name,*args,**kwargs):
    record=_record()
    if record is None:
        raise PolicyUnavailable('Exact legacy validator runtime is not registered')
    _verify_registered(record)
    try:
        return getattr(record.module,name)(*args,**kwargs)
    except record.module.PolicyUnavailable:
        raise PolicyUnavailable('Registered native validation unavailable within operation bounds') from None

def select_route(envelope):
    proof,kind=envelope.validation,envelope.record.report_kind
    if envelope.schema_version!='engineering.admission.v2' or proof.validator_id!='veriforge.native-tool-admission':
        raise ValueError('Unsupported native validator schema/identity')
    route=None
    if kind in REPORT_KINDS[:2] and proof.version=='1':
        route='legacy' if proof.implementation_hash==LEGACY_HASH else 'current' if proof.implementation_hash==CURRENT_HASH else None
    elif kind==REPORT_KINDS[2] and proof.version=='2' and proof.implementation_hash==CURRENT_HASH:
        route='current'
    if route is None: raise ValueError('Unsupported native validator proof/report combination')
    if _record() is not None:
        if _call('select_route',envelope)!=route: raise ValueError('Registered native route differs')
    elif route=='current':
        try:
            if _hash_source(native.__file__)!=CURRENT_HASH or _hash_source(leaf.__file__)!=CURRENT_LEAF_HASH:
                raise PolicyUnavailable('Current native validator source differs')
        except OSError:
            raise PolicyUnavailable('Current native validator source unavailable') from None
    return route

def read_legacy(envelope_bytes,artifacts):
    return _call('read_legacy',envelope_bytes,artifacts)

def queue_legacy(envelope_bytes,artifacts,*,page=False):
    return _call('queue_legacy',envelope_bytes,artifacts,page=page)

def flush_legacy():
    if _record() is not None: return _call('flush_legacy')

def is_native_view(value):
    if type(value) is native.AdmittedNativeInvestigation: return True
    record=_record()
    return record is not None and type(value) is record.module.LegacyReadView
