"""Small application hook for explicitly installed native candidate execution.

No installation, database, broker or native process is created here. The same
trusted runtime must be installed separately in API, dispatcher and worker roles.
"""
from . import eda_repair_runtime as native, service


def dispatch_eda(dispatcher, *, limit=16):
    if type(limit) is not int or not 1 <= limit <= 16:
        raise ValueError('Invalid EDA dispatch bound')
    empty = {'examined': 0, 'sent': 0, 'failed': 0}
    try:
        runtime = native.configured()
        runtime.enabled()
    except service.Conflict:
        return {**empty, 'status': 'disabled'}
    except Exception:
        return {**empty, 'status': 'unavailable'}
    queue = getattr(dispatcher, 'queue', None)
    if queue is None or not callable(getattr(queue, 'enqueue', None)):
        return {**empty, 'status': 'queue_unavailable'}
    try:
        result = native.dispatch_ready(queue, limit=limit)
    except Exception:
        # Never project raw database, queue, native or configuration exceptions.
        return {**empty, 'status': 'unavailable'}
    return {**result, 'status': 'checked'}


def install_eda_application(sessions, *, output_root, catalog, policy_reader, control,
                            vivado=None, kicad=None):
    """Explicit trusted bootstrap in each API/dispatcher/worker process.

    Inputs are independently reviewed host objects, never request/environment
    deserialization. No default installation or automatic schema migration occurs.
    A failed replacement leaves this process disabled instead of retaining stale
    authority. Actual schema and policy are checked at each effectful entry.
    """
    native.install_runtime(None)
    from .eda_source_resolver import ImmutableEdaResolver, ReviewedNativeCatalog
    if type(catalog) is not ReviewedNativeCatalog:
        raise service.Conflict('Reviewed native catalog required')
    if not all(callable(value) for value in (sessions, policy_reader, control)):
        raise TypeError('Trusted runtime callbacks required')
    resolver = ImmutableEdaResolver(sessions, output_root=output_root, catalog=catalog,
                                    vivado=vivado, kicad=kicad)
    runtime = native.EdaRepairRuntime(sessions, resolver, policy_reader, control)
    native.install_runtime(runtime)
    return runtime
