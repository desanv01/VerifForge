"""Fake-only launcher identity checks; no native/vendor execution or child tool."""
from hashlib import sha256
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from veriforge.vivado import repair_execution as execution
from veriforge.vivado import storage
from veriforge.vivado.repair_execution import ControllerInstallation, PrivateFixedBatchAdapter
from test_repair_execution import setup, synthetic_xdc_admission_for_fake_tool_tests


def fake_controller(tmp_path,monkeypatch,profile='synthesis',body=None):
    path=tmp_path/'installed'/'deep'/f'{profile}.py';path.parent.mkdir(parents=True,exist_ok=True)
    storage_hash=sha256(Path(storage.__file__).read_bytes()).hexdigest()
    text=body if body is not None else (
        "from pathlib import Path\nimport veriforge.vivado.storage as storage_module\n"
        f"STORAGE_HASH='{storage_hash}'\nROOT=Path('/old-private-root')\n"
        "ATTEMPT=None\nOWNER=None\nPLAN_HASH=None\nLAUNCHER='old-compiled-launcher'\n")
    raw=text.encode();path.write_bytes(raw)
    original=execution.PROFILES[profile]
    monkeypatch.setitem(execution.PROFILES,profile,(original[0],sha256(raw).hexdigest(),*original[2:]))
    return path


def inert_launcher(tmp_path,content=b'rem inert test launcher'):
    path=tmp_path/'tool'/'vivado.bat';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
    return path,sha256(content).hexdigest()


def prepared_for(tmp_path,profile='synthesis'):
    return SimpleNamespace(pins=SimpleNamespace(profile=profile),directory=tmp_path/'candidate',
                           candidate_plan_sha256='a'*64)


def test_loaded_repair_execution_is_the_candidate_overlay():
    overlay=os.environ.get('VERIFORGE_OVERLAY');expected=os.environ.get('VERIFORGE_REPAIR_EXECUTION_SHA256')
    if not overlay or not expected:pytest.skip('requires the focused overlay identity proof')
    loaded=Path(execution.__file__).resolve()
    assert loaded.is_relative_to(Path(overlay).resolve())
    assert sha256(loaded.read_bytes()).hexdigest()==expected


@pytest.mark.parametrize('profile',('synthesis','routed'))
def test_omitted_launcher_constructs_inert_but_refuses_before_controller_load(tmp_path,monkeypatch,profile):
    # The fake controller would raise AssertionError if exec() ran; the expected
    # launcher refusal must occur first for both supported profiles.
    controller=fake_controller(tmp_path,monkeypatch,profile,
                               body="raise AssertionError('controller bytes must not load')\n")
    installation=ControllerInstallation(
        tmp_path,controller,routed_controller=controller if profile=='routed'else None)
    assert installation.launcher is None and installation.launcher_sha256 is None
    adapter=PrivateFixedBatchAdapter(tmp_path,'U6-FAKE-LAUNCHER',installation=installation)
    with pytest.raises(ValueError,match='launcher identity required'):
        adapter._load_controller(prepared_for(tmp_path,profile))


def test_mismatched_digest_refuses_before_controller_load(tmp_path,monkeypatch):
    controller=fake_controller(tmp_path,monkeypatch,
                               body="raise AssertionError('controller bytes must not load')\n")
    path,_=inert_launcher(tmp_path)
    installation=ControllerInstallation(tmp_path,controller,launcher=path,launcher_sha256='0'*64)
    adapter=PrivateFixedBatchAdapter(tmp_path,'U6-FAKE-LAUNCHER',installation=installation)
    with pytest.raises(ValueError,match='launcher identity changed'):
        adapter._load_controller(prepared_for(tmp_path))


def test_missing_launcher_file_refuses(tmp_path,monkeypatch):
    controller=fake_controller(tmp_path,monkeypatch)
    path,digest=inert_launcher(tmp_path);path.unlink()
    installation=ControllerInstallation(tmp_path,controller,launcher=path,launcher_sha256=digest)
    adapter=PrivateFixedBatchAdapter(tmp_path,'U6-FAKE-LAUNCHER',installation=installation)
    with pytest.raises(ValueError,match='regular unlinked file required'):
        adapter._load_controller(prepared_for(tmp_path))


def test_changed_launcher_file_refuses(tmp_path,monkeypatch):
    controller=fake_controller(tmp_path,monkeypatch)
    path,digest=inert_launcher(tmp_path);path.write_bytes(b'rem changed after review')
    installation=ControllerInstallation(tmp_path,controller,launcher=path,launcher_sha256=digest)
    adapter=PrivateFixedBatchAdapter(tmp_path,'U6-FAKE-LAUNCHER',installation=installation)
    with pytest.raises(ValueError,match='launcher identity changed'):
        adapter._load_controller(prepared_for(tmp_path))


@pytest.mark.parametrize('profile',('synthesis','routed'))
def test_valid_explicit_launcher_is_bound_without_compiled_fallback(tmp_path,monkeypatch,profile):
    controller=fake_controller(tmp_path,monkeypatch,profile)
    path,digest=inert_launcher(tmp_path)
    installation=ControllerInstallation(
        tmp_path,controller,routed_controller=controller if profile=='routed'else None,
        launcher=path,launcher_sha256=digest)
    adapter=PrivateFixedBatchAdapter(tmp_path,'U6-FAKE-LAUNCHER',installation=installation)
    module=adapter._load_controller(prepared_for(tmp_path,profile))
    assert module.LAUNCHER==str(path)
    assert module.LAUNCHER!='old-compiled-launcher'


def test_legacy_installation_constructs_inert_but_refuses_at_execution_boundary(tmp_path):
    installation=ControllerInstallation.legacy(tmp_path)
    assert installation.launcher is None and installation.launcher_sha256 is None
    adapter=PrivateFixedBatchAdapter(tmp_path,'U6-FAKE-LEGACY',installation=installation)
    with pytest.raises(ValueError,match='launcher identity required'):
        adapter._load_controller(prepared_for(tmp_path))
