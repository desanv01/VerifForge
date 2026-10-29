"""Independent frozen-source/file/factory checks; no Compose or live services."""
import ast
from hashlib import sha256
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from veriforge.api import runtime

ROOT = Path(__file__).resolve().parents[2]
FROZEN = {
    "src/veriforge/api/runtime.py": "9a7376adc1fd467424fd02fd10ef77e27fcd83954fe71c191027bc4bc0d11b9d",
    "tests/api/test_runtime_configuration.py": "856d75163d6b492fe8301a80f27721d0aaae993f3c4b4725336aad526f1b8b9c",
    "compose.planner.yaml": "50088b8de6f97ab0d47d5d58dadf72ef13bc1e77f1e04bab65e0016f4bfc9cca",
    "docs/project/PLANNER_LOCAL_SETUP.md": "dba1bb7dec87ccd62453397a5a1e21fc2047ef21b4c0fd6b0b3d01c5f651fce7",
}


def test_frozen_delivery():
    for name, digest in FROZEN.items():
        assert sha256((ROOT / name).read_bytes()).hexdigest() == digest


@pytest.fixture
def files(tmp_path):
    control, policy = tmp_path / "control.json", tmp_path / "policy.json"
    control.write_text('{"control":"RUNNING"}', encoding="utf-8")
    policy.write_text('{"enabled":true,"requested_model":"review-model"}', encoding="utf-8")
    return control, policy


def settings(files):
    control, policy = files
    return {"VERIFORGE_PLANNER_ENABLED": "1", "VERIFORGE_CONTROL_PATH": str(control),
            "VERIFORGE_PLANNER_POLICY_PATH": str(policy)}


def test_three_exact_names_and_uncached_atomic_control(files):
    values, reads = settings(files), []
    class Narrow:
        def get(self, key):
            reads.append(key)
            assert key in values
            return values[key]
    config, policy = runtime.load_configuration(Narrow())
    assert reads == list(values)
    assert config.enabled and policy.requested_model == "review-model"
    assert 0 < policy.limits.max_rounds <= 100
    control = files[0]
    for state in ("PAUSED", "RUNNING"):
        replacement = control.with_suffix(".next")
        replacement.write_text(json.dumps({"control": state}))
        replacement.replace(control)
        assert config.control() == ("RUNNING" if state == "RUNNING" else "STOPPED")


@pytest.mark.parametrize("flag", [None, "0", "true", " 1", "1 ", 1, True])
def test_disabled_never_reads_policy(files, monkeypatch, flag):
    values = settings(files)
    values["VERIFORGE_PLANNER_ENABLED"] = flag
    monkeypatch.setattr(runtime, "load_policy", lambda _: pytest.fail("disabled policy I/O"))
    config, policy = runtime.load_configuration(values)
    assert not config.enabled and not policy.enabled and config.control() == "STOPPED"


@pytest.mark.parametrize("patch", [
    {"enabled": 1}, {"requested_model": 7}, {"unknown": True},
    {"limits": {"max_rounds": True}}, {"limits": {"max_rounds": 1.0}},
    {"limits": {"max_execution_seconds": "2"}}, {"limits": {"stop_on_finding": "false"}},
    {"limits": {"max_total_cycles": 10_000_001}}, {"limits": {"max_model_seconds": 121}},
    {"max_seed_artifact_bytes": 1023}, {"pricing": {"unknown": 1}},
    {"limits": {"max_cost_usd": "1"}},
])
def test_strict_policy_rejections(files, patch):
    value = {"enabled": True, "requested_model": "review-model", **patch}
    files[1].write_text(json.dumps(value))
    config, policy = runtime.load_configuration(settings(files))
    assert not config.enabled and not policy.enabled


@pytest.mark.parametrize("raw", [
    b'{"enabled":true,"requested_model":"a","requested_model":"b"}',
    b'{"enabled":true,"requested_model":"a","limits":{"max_rounds":1,"max_rounds":2}}',
    b'{"enabled":true,"requested_model":"a","pricing":{"input_usd_per_million":0,"input_usd_per_million":1,"output_usd_per_million":0}}',
    b'{"enabled":true,"requested_model":"a","limits":{"max_wall_seconds":Infinity}}',
    b'{"enabled":true,"requested_model":"a","limits":{"max_wall_seconds":1e999}}',
    b'\xff', b'[]', b'null',
], ids=lambda raw: sha256(raw).hexdigest()[:8])
def test_json_ambiguity_and_invalid_encoding(files, raw):
    files[1].write_bytes(raw)
    assert not runtime.load_policy(str(files[1])).enabled


def test_exact_byte_boundary_regular_file_and_absolute_path(files):
    policy = files[1]
    original = policy.read_bytes()
    policy.write_bytes(original + b" " * (runtime.MAX_POLICY_BYTES - len(original)))
    assert runtime.load_policy(str(policy)).enabled
    policy.write_bytes(policy.read_bytes() + b" ")
    assert not runtime.load_policy(str(policy)).enabled
    assert not runtime.load_policy(str(policy.parent)).enabled
    assert not runtime.load_policy("relative.json").enabled
    assert not runtime.load_policy(policy).enabled  # exact string contract


@pytest.mark.parametrize("failure", ["short", "growth", "identity", "denied", "close"])
def test_file_failures_disable_and_close_descriptor(files, monkeypatch, failure):
    policy = files[1]
    real_read, real_stat, real_close = runtime.os.read, runtime.os.fstat, runtime.os.close
    closed = []
    def close(fd):
        real_close(fd)
        closed.append(fd)
        if failure == "close":
            raise OSError("synthetic close failure")
    monkeypatch.setattr(runtime.os, "close", close)
    if failure == "denied":
        monkeypatch.setattr(runtime.os, "open", lambda *a: (_ for _ in ()).throw(PermissionError()))
    elif failure in ("short", "growth"):
        def read(fd, size):
            assert size == runtime.MAX_POLICY_BYTES + 1
            raw = real_read(fd, size)
            return raw[:-1] if failure == "short" else raw + b" "
        monkeypatch.setattr(runtime.os, "read", read)
    elif failure == "identity":
        calls = []
        def changed(fd):
            actual = real_stat(fd)
            calls.append(fd)
            fields = {name: getattr(actual, name) for name in
                      ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")}
            fields["st_ino"] += len(calls) - 1
            return SimpleNamespace(**fields)
        monkeypatch.setattr(runtime.os, "fstat", changed)
    assert not runtime.load_policy(str(policy)).enabled
    assert len(closed) == (0 if failure == "denied" else 1)


def test_factory_forwards_detached_policy_and_live_control_without_provider(files, monkeypatch):
    from veriforge.api import app
    from veriforge.planner.provider import MetaConfig
    monkeypatch.setattr(MetaConfig, "from_env", classmethod(lambda cls: pytest.fail("secret read")))
    seen = {}
    def factory(engine, **kwargs):
        seen.update(kwargs)
        return "synthetic-app"
    monkeypatch.setattr(app, "create_app", factory)
    assert runtime.create_configured_app(environ=settings(files)) == "synthetic-app"
    assert seen["planner_policy"].requested_model == "review-model"
    files[1].write_text('{"enabled":true,"requested_model":"new-model"}')
    assert seen["planner_policy"].requested_model == "review-model"  # restart policy
    files[0].write_text('{"control":"PAUSED"}')
    assert seen["planner_control"]() == "STOPPED"
    runtime.create_configured_app(environ={})
    assert not seen["planner_policy"].enabled


def test_manual_control_and_module_import_do_not_load_policy_or_secrets(monkeypatch):
    import importlib
    from veriforge.core.runtime_configuration import resolve_control, RuntimeConfiguration
    from veriforge.planner.provider import MetaConfig
    monkeypatch.setattr(MetaConfig, "from_env", classmethod(lambda cls: pytest.fail("secret read")))
    monkeypatch.setattr(RuntimeConfiguration, "from_environment",
                        classmethod(lambda cls: pytest.fail("manual configuration read")))
    assert resolve_control(planner_owned=False)() == "RUNNING"
    # Reload runs the real module body, while configuration entrypoints remain uncalled.
    importlib.reload(runtime)


def test_explicit_decimal_policy_and_both_absolute_paths(files):
    files[1].write_text(json.dumps({"enabled": True, "requested_model": "review-model",
        "limits": {"max_cost_usd": "1.25"},
        "pricing": {"input_usd_per_million": "0.25", "output_usd_per_million": 0.5}}))
    assert str(runtime.load_configuration(settings(files))[1].limits.max_cost_usd) == "1.25"
    for key in ("VERIFORGE_CONTROL_PATH", "VERIFORGE_PLANNER_POLICY_PATH"):
        for invalid in (None, 1, "relative.json"):
            values = {**settings(files), key: invalid}
            config, policy = runtime.load_configuration(values)
            assert not config.enabled and not policy.enabled


def overlay():
    text = (ROOT / "compose.planner.yaml").read_text()
    folded = text.split("      - >-\n", 1)[1].split("    environment:", 1)[0]
    return text, " ".join(line.strip() for line in folded.splitlines())


def test_overlay_source_merge_contract():
    text, command = overlay()
    base = (ROOT / "compose.yaml").read_text()
    services = text.split("services:\n")[1]
    roles = {name: re.split(r"\n  [a-z][a-z-]*:\n", services.split(f"  {name}:\n", 1)[1])[0]
             for name in ("api", "reconciler", "rtl-worker", "planner-worker")}
    # Source checks only: documented merge keys are environment names and mount targets.
    assert text.count("read_only: true") == text.count("create_host_path: false") == 2
    assert "target: /veriforge-control" in text and "target: /veriforge-policy" in text
    assert "VERIFORGE_PLANNER_ENABLED:-0" in text
    assert "MODEL_API_KEY" not in services.split("  planner-worker:")[0]
    assert "MODEL_API_KEY" not in base
    assert "env_file" not in text + base and "docker.sock" not in text + base
    assert "ports:" not in text and "127.0.0.1:" in base
    assert "artifacts:/artifacts" in base and "target: /artifacts" not in text
    assert "profiles: [planner]" in text
    for name, section in roles.items():
        assert "<<: *planner-settings" in section
        assert "- *planner-control" in section
        assert ("- *planner-policy" in section) is (name in ("api", "planner-worker"))
        assert ("MODEL_API_KEY:" in section) is (name == "planner-worker")
    assert "--factory" in text and "veriforge.api.runtime:create_configured_app" in text
    assert "shell" not in ast.dump(ast.parse(command))
    assert set(roles) == {"api", "reconciler", "rtl-worker", "planner-worker"}


@pytest.mark.parametrize("enabled,policy_enabled", [(False, True), (True, False), (True, True)])
def test_literal_role_command_defers_secrets_and_pins_model(monkeypatch, enabled, policy_enabled):
    from veriforge.core import planner_runtime
    from veriforge.planner import provider
    _, command = overlay()
    events = []
    monkeypatch.setattr(runtime, "load_configuration", lambda: (
        SimpleNamespace(enabled=enabled, control=lambda: "RUNNING"),
        SimpleNamespace(enabled=policy_enabled, requested_model="trusted-review-model")))
    def env(cls):
        events.append("credential-read")
        return cls(model="environment-model")
    monkeypatch.setattr(provider.MetaConfig, "from_env", classmethod(env))
    monkeypatch.setattr(provider, "MetaProvider", lambda config: events.append(config.model))
    def serve(**kwargs):
        assert not events
        assert kwargs["enabled"] is (enabled and policy_enabled)
        if kwargs["enabled"]:
            kwargs["provider_factory"]()
        return SimpleNamespace(reason="synthetic-review")
    monkeypatch.setattr(planner_runtime, "serve", serve)
    exec(compile(command, "frozen overlay command", "exec"), {})
    assert events == (["credential-read", "trusted-review-model"] if enabled and policy_enabled else [])


def test_actual_disabled_role_gate_has_no_database_or_provider_access(monkeypatch):
    from veriforge.core import planner_runtime, database
    monkeypatch.setattr(database, "make_engine", lambda: pytest.fail("database access"))
    assert planner_runtime.serve(enabled=False, provider_factory=lambda: pytest.fail("provider access")).reason == "disabled"
    assert planner_runtime.serve(enabled=True, control=lambda: "STOPPED",
                                 provider_factory=lambda: pytest.fail("provider access")).reason == "paused"
