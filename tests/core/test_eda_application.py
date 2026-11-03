"""Application dispatch seam only: no engine, queue connection or native work."""
from types import SimpleNamespace

import pytest

from veriforge.core import eda_application as app, service


def test_disabled_does_not_touch_dispatcher_or_scan(monkeypatch):
    def disabled():
        raise service.Conflict('not installed')
    monkeypatch.setattr(app.native, 'configured', disabled)
    monkeypatch.setattr(app.native, 'dispatch_ready', lambda *a, **k: pytest.fail('disabled scan'))
    assert app.dispatch_eda(object()) == {'examined': 0, 'sent': 0, 'failed': 0, 'status': 'disabled'}


def test_installed_uses_existing_queue_once_and_bounded_scan(monkeypatch):
    calls = []
    queue = SimpleNamespace(enqueue=lambda *a, **k: None)
    monkeypatch.setattr(app.native, 'configured', lambda: SimpleNamespace(enabled=lambda: calls.append('policy')))
    def dispatch(actual, *, limit):
        assert actual is queue and limit == 3
        calls.append('dispatch')
        return {'examined': 2, 'sent': 1, 'failed': 1}
    monkeypatch.setattr(app.native, 'dispatch_ready', dispatch)
    assert app.dispatch_eda(SimpleNamespace(queue=queue), limit=3) == {'examined': 2, 'sent': 1, 'failed': 1, 'status': 'checked'}
    assert calls == ['policy', 'dispatch']


def test_missing_queue_and_errors_project_no_raw_details(monkeypatch):
    monkeypatch.setattr(app.native, 'configured', lambda: SimpleNamespace(enabled=lambda: None))
    assert app.dispatch_eda(object())['status'] == 'queue_unavailable'
    def failure(*a, **k):
        raise RuntimeError('synthetic-private-detail')
    monkeypatch.setattr(app.native, 'dispatch_ready', failure)
    result = app.dispatch_eda(SimpleNamespace(queue=SimpleNamespace(enqueue=lambda: None)))
    assert result['status'] == 'unavailable' and 'synthetic-private-detail' not in str(result)


@pytest.mark.parametrize('limit', [0, 17, True, 1.5])
def test_dispatch_bound_rejected_before_runtime_access(monkeypatch, limit):
    monkeypatch.setattr(app.native, 'configured', lambda: pytest.fail('invalid bound reached runtime'))
    with pytest.raises(ValueError):
        app.dispatch_eda(object(), limit=limit)
