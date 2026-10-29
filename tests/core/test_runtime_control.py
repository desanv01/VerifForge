"""Strict service control uses bounded local files only; no service operations."""
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from veriforge.core.runtime_control import ControlReader


@pytest.mark.parametrize("raw", [b"", b"{", b"[]", b"null", b'{"control":true}',
    b'{"control":"PAUSED"}', b'{"control":"UNKNOWN"}', b'{"control":"running"}',
    b'{"control":"RUNNING","control":"PAUSED"}', b'{"control":"RUNNING","x":NaN}', b"\xff"])
def test_non_authoritative_control_fails_closed(tmp_path, raw):
    path = tmp_path / "control.json"
    path.write_bytes(raw)
    assert ControlReader(path)() == "STOPPED"


def test_missing_and_atomic_replacement_are_never_cached(tmp_path):
    path = tmp_path / "control.json"
    reader = ControlReader(path)
    assert reader() == "STOPPED"
    path.write_text(json.dumps({"control": "RUNNING", "project": "test"}))
    assert reader() == "RUNNING"
    replacement = tmp_path / "replacement.json"
    replacement.write_text('{"control":"PAUSED"}')
    os.replace(replacement, path)
    assert reader() == "STOPPED"
    replacement.write_text('{"control":"RUNNING"}')
    os.replace(replacement, path)
    assert reader() == "RUNNING"
    path.unlink()
    assert reader() == "STOPPED"


def test_byte_bound_applies_before_json_parse(tmp_path):
    path = tmp_path / "control.json"
    path.write_bytes(b'{"control":"RUNNING","padding":"' + b"x" * 1000 + b'"}')
    assert ControlReader(path, max_bytes=64)() == "STOPPED"


@pytest.mark.parametrize("bound", [0, 65_537, True])
def test_invalid_control_bounds_reject(tmp_path, bound):
    with pytest.raises(ValueError): ControlReader(tmp_path / "control.json", max_bytes=bound)


def test_permission_error_is_fixed_stopped_status(tmp_path, monkeypatch):
    path = tmp_path / "control.json"
    path.write_text('{"control":"RUNNING"}')
    calls = []
    def denied(*args, **kwargs):
        calls.append(True)
        raise PermissionError("private path must not escape")
    monkeypatch.setattr(os, "open", denied)
    assert ControlReader(path)() == "STOPPED" and calls == [True]


def test_file_replaced_during_read_fails_closed(tmp_path, monkeypatch):
    path, replacement = tmp_path / "control.json", tmp_path / "replacement.json"
    path.write_text('{"control":"RUNNING"}')
    replacement.write_text('{"control":"PAUSED"}')
    original_read = os.read
    def replace_after_read(descriptor, size):
        raw = original_read(descriptor, size)
        os.replace(replacement, path)
        return raw
    monkeypatch.setattr(os, "read", replace_after_read)
    assert ControlReader(path)() == "STOPPED"


@pytest.mark.parametrize("mode", [stat.S_IFIFO, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK, stat.S_IFDIR])
def test_nonregular_target_is_rejected_before_open(tmp_path, monkeypatch, mode):
    def forbidden(*args, **kwargs): pytest.fail("opened nonregular control target")
    monkeypatch.setattr(Path, "stat", lambda *args, **kwargs: SimpleNamespace(st_mode=mode, st_size=0))
    monkeypatch.setattr(os, "open", forbidden)
    assert ControlReader(tmp_path / "control.json")() == "STOPPED"


def test_nonregular_descriptor_is_rejected_before_read(tmp_path, monkeypatch):
    path = tmp_path / "control.json"
    path.write_text('{"control":"RUNNING"}')
    actual = os.fstat
    def fifo(descriptor):
        value = actual(descriptor)
        return SimpleNamespace(st_mode=stat.S_IFIFO, st_size=value.st_size)
    def forbidden(*args, **kwargs): pytest.fail("read from nonregular descriptor")
    monkeypatch.setattr(os, "fstat", fifo)
    monkeypatch.setattr(os, "read", forbidden)
    assert ControlReader(path)() == "STOPPED"


def test_open_uses_nonblocking_flag_where_available(tmp_path, monkeypatch):
    path = tmp_path / "control.json"
    path.write_text('{"control":"RUNNING"}')
    actual, flags = os.open, []
    def observe(target, value):
        flags.append(value)
        return actual(target, value)
    monkeypatch.setattr(os, "open", observe)
    assert ControlReader(path)() == "RUNNING"
    if hasattr(os, "O_NONBLOCK"):
        assert flags[0] & os.O_NONBLOCK
