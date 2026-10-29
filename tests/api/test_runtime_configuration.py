"""Pure configuration/source checks and temporary SQLite ASGI; no services."""
import ast
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from veriforge.api import runtime
from veriforge.core.database import Campaign
from veriforge.planner.provider import MetaConfig


@pytest.fixture(autouse=True)
def forbid_provider_environment(monkeypatch):
    def forbidden(cls):
        pytest.fail("Configuration/API must not read provider environment")
    monkeypatch.setattr(MetaConfig, "from_env", classmethod(forbidden))


@pytest.fixture
def configured(tmp_path):
    control = tmp_path / "current-state.json"
    control.write_text('{"control":"RUNNING"}', encoding="utf-8")
    policy = tmp_path / "planner-policy.json"
    policy.write_text(json.dumps({
        "enabled": True, "requested_model": "synthetic-server-model",
        "limits": {"max_rounds": 3, "max_wall_seconds": 60,
                   "max_cost_usd": "1.25"},
        "pricing": {"input_usd_per_million": "0.25", "output_usd_per_million": "0.50"},
    }), encoding="utf-8")
    env = {runtime.ENABLED_VARIABLE: "1", runtime.CONTROL_VARIABLE: str(control),
           runtime.POLICY_VARIABLE: str(policy)}
    return env, control, policy


def test_reads_only_three_narrow_settings(configured):
    env, _, _ = configured
    reads = []
    class Settings:
        def get(self, key):
            assert key in env
            reads.append(key)
            return env[key]
    config, policy = runtime.load_configuration(Settings())
    assert config.control() == "RUNNING" and policy.enabled
    assert reads == [runtime.ENABLED_VARIABLE, runtime.CONTROL_VARIABLE, runtime.POLICY_VARIABLE]
    assert policy.requested_model == "synthetic-server-model"
    assert policy.limits.max_rounds == 3
    assert str(policy.pricing.input_usd_per_million) == "0.25"


@pytest.mark.parametrize("flag", [None, "", "0", "true", "True", "yes", 1, True])
def test_disabled_flag_never_opens_policy(configured, monkeypatch, flag):
    env, _, _ = configured
    env[runtime.ENABLED_VARIABLE] = flag
    monkeypatch.setattr(runtime, "load_policy", lambda path: pytest.fail("Disabled file read"))
    config, policy = runtime.load_configuration(env)
    assert not config.enabled and not policy.enabled and config.control() == "STOPPED"


@pytest.mark.parametrize("field,value", [
    (runtime.CONTROL_VARIABLE, None), (runtime.CONTROL_VARIABLE, "relative.json"),
    (runtime.CONTROL_VARIABLE, 12), (runtime.POLICY_VARIABLE, None),
    (runtime.POLICY_VARIABLE, "relative.json"), (runtime.POLICY_VARIABLE, 12),
])
def test_missing_or_invalid_absolute_path_disables(configured, field, value):
    env, _, _ = configured
    env[field] = value
    config, policy = runtime.load_configuration(env)
    assert not config.enabled and not policy.enabled


@pytest.mark.parametrize("raw", [
    b"", b"[]", b"null", b"{}", b"{", b"\xff", b'{"enabled":true}',
    b'{"enabled":true,"requested_model":"m","unexpected":1}',
    b'{"enabled":true,"enabled":false,"requested_model":"m"}',
    b'{"enabled":true,"requested_model":"m","limits":{"max_rounds":2,"max_rounds":3}}',
    b'{"enabled":true,"requested_model":"m","limits":{"unknown":1}}',
    b'{"enabled":true,"requested_model":"m","pricing":{"unknown":1}}',
    b'{"enabled":"true","requested_model":"m"}',
    b'{"enabled":true,"requested_model":"m","limits":{"max_rounds":"3"}}',
    b'{"enabled":true,"requested_model":"m","limits":{"max_wall_seconds":"1"}}',
    b'{"enabled":true,"requested_model":"m","limits":{"stop_on_finding":1}}',
    b'{"enabled":true,"requested_model":"m","limits":{"max_rounds":101}}',
    b'{"enabled":true,"requested_model":"m","limits":{"max_wall_seconds":NaN}}',
    b'{"enabled":true,"requested_model":"m","limits":{"max_cost_usd":"1"}}',
    b'{"enabled":true,"requested_model":"m","pricing":{"input_usd_per_million":"Infinity","output_usd_per_million":0}}',
    b'{"enabled":true,"requested_model":"bad model"}',
    b'{"enabled":false,"requested_model":"m"}',
    b" " * (runtime.MAX_POLICY_BYTES + 1),
    b"[" * 1100 + b"]" * 1100,
], ids=lambda raw: f"bytes-{len(raw)}-{sha256(raw).hexdigest()[:8]}")
def test_invalid_policy_fails_closed(configured, raw):
    env, _, policy_file = configured
    policy_file.write_bytes(raw)
    config, policy = runtime.load_configuration(env)
    assert not config.enabled and not policy.enabled


def test_missing_directory_and_unreadable_policy(configured, monkeypatch):
    env, _, policy = configured
    policy.unlink()
    assert not runtime.load_configuration(env)[0].enabled
    policy.mkdir()
    assert not runtime.load_configuration(env)[0].enabled
    policy.rmdir()
    policy.write_text('{"enabled":true,"requested_model":"m"}')
    monkeypatch.setattr(runtime.os, "open", lambda *args: (_ for _ in ()).throw(PermissionError()))
    assert not runtime.load_configuration(env)[0].enabled


def test_changed_file_during_read_fails_closed(configured, monkeypatch):
    env, _, policy = configured
    original = runtime.os.read
    def changing(fd, size):
        raw = original(fd, size)
        policy.write_text('{"enabled":true,"requested_model":"different"}')
        return raw
    monkeypatch.setattr(runtime.os, "read", changing)
    assert not runtime.load_configuration(env)[0].enabled


def seed_body():
    return {"schema_version": "planner.seed.v1", "project_id": "fifo_correct", "plan": {
        "schema_version": "1", "experiment_id": "seed", "fixture_id": "fifo_correct",
        "objective_ids": ["FIFO_BEHAVIOR"], "budget": {"max_cycles": 8, "wall_time_seconds": 2},
        "operations": [{"op": "reset"}, {"op": "write", "value": 17}, {"op": "read"}],
    }}


def client(db, env):
    return TestClient(runtime.create_configured_app(db[0], initialize_catalog=False, environ=env,
        planner_clock=lambda: datetime(2026, 10, 30, tzinfo=timezone.utc)))


def test_default_factory_is_disabled_and_manual_path_remains(db):
    with client(db, {}) as c:
        assert c.get("/api/health").json()["planner_admission_enabled"] is False
        assert c.post("/api/planner-seeds", json=seed_body(), headers={"Idempotency-Key": "disabled"}).status_code == 503
        body = seed_body()
        assert c.post("/api/campaigns", json={"project_id": body["project_id"], "plan": body["plan"]},
                      headers={"Idempotency-Key": "manual"}).status_code == 201


def test_factory_admits_with_trusted_policy_and_observes_atomic_control(db, configured):
    env, control, _ = configured
    with client(db, env) as c:
        assert c.get("/api/health").json()["planner_admission_enabled"] is True
        assert c.post("/api/planner-seeds", json=seed_body(), headers={"Idempotency-Key": "first"}).status_code == 200
        with db[1]() as session:
            count = session.scalar(select(func.count()).select_from(Campaign))
        replacement = control.with_suffix(".next")
        replacement.write_text('{"control":"PAUSED"}')
        replacement.replace(control)
        assert c.post("/api/planner-seeds", json=seed_body(), headers={"Idempotency-Key": "paused"}).status_code == 503
        control.write_text('{"control":"RUNNING","control":"RUNNING"}')
        assert c.post("/api/planner-seeds", json=seed_body(), headers={"Idempotency-Key": "invalid"}).status_code == 503
        with db[1]() as session:
            assert session.scalar(select(func.count()).select_from(Campaign)) == count
        replacement.write_text('{"control":"RUNNING"}')
        replacement.replace(control)
        assert c.post("/api/planner-seeds", json=seed_body(), headers={"Idempotency-Key": "resumed"}).status_code == 200


def test_bad_config_factory_never_enables_admission(db, configured):
    env, _, policy = configured
    policy.write_text('{"enabled":true,"requested_model":"m","injected":true}')
    with client(db, env) as c:
        assert c.get("/api/health").json()["planner_admission_enabled"] is False
        assert c.post("/api/planner-seeds", json=seed_body(), headers={"Idempotency-Key": "invalid"}).status_code == 503


ROOT = Path(__file__).resolve().parents[2]


def overlay_parts():
    source = (ROOT / "compose.planner.yaml").read_text()
    # Extract folded command literally without invoking Compose/interpolation.
    command = source.split("      - >-\n", 1)[1].split("    environment:", 1)[0]
    return source, " ".join(line.strip() for line in command.splitlines())


def test_overlay_source_preserves_role_separation_and_directory_control():
    source, command = overlay_parts()
    ast.parse(command)
    assert "profiles: [planner]" in source
    assert "source: ${VERIFORGE_CONTROL_DIRECTORY:?" in source
    assert "source: ${VERIFORGE_PLANNER_POLICY_DIRECTORY:?" in source
    assert source.count("create_host_path: false") == 2
    assert source.count("read_only: true") == 2
    assert "VERIFORGE_PLANNER_ENABLED:-0" in source
    services = source.split("services:\n", 1)[1]
    non_provider = services.split("  planner-worker:", 1)[0]
    assert "MODEL_API_KEY" not in non_provider and "env_file" not in source
    assert "docker.sock" not in source and "ports:" not in source
    assert "veriforge.api.runtime:create_configured_app" in source and "--factory" in source
    base = (ROOT / "compose.yaml").read_text()
    assert "127.0.0.1:" in base and "artifacts:/artifacts" in base
    assert "MODEL_API_KEY" not in base
    assert "policy.requested_model" in command


@pytest.mark.parametrize("enabled", [False, True])
def test_literal_dedicated_command_inert_when_disabled_and_uses_server_model(monkeypatch, enabled):
    from veriforge.core import planner_runtime
    from veriforge.planner import provider
    _, command = overlay_parts()
    constructed = []
    def fake_provider(config):
        constructed.append(config.model)
        return SimpleNamespace(model=config.model)
    monkeypatch.setattr(provider, "MetaProvider", fake_provider)
    monkeypatch.setattr(MetaConfig, "from_env", classmethod(
        lambda cls: cls(model="synthetic-environment-model")))
    monkeypatch.setattr(runtime, "load_configuration", lambda: (
        SimpleNamespace(enabled=enabled, control=lambda: "RUNNING"),
        SimpleNamespace(enabled=True, requested_model="trusted-server-model")))
    def fake_serve(*, enabled, control, provider_factory):
        assert control() == "RUNNING"
        if enabled:
            assert provider_factory().model == "trusted-server-model"
        return SimpleNamespace(reason="synthetic-test")
    monkeypatch.setattr(planner_runtime, "serve", fake_serve)
    exec(compile(command, "compose.planner.yaml static command", "exec"), {})
    assert constructed == (["trusted-server-model"] if enabled else [])
