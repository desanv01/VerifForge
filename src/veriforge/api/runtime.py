"""Opt-in local API configuration. No env-file loading or provider access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat

from veriforge.core.runtime_configuration import (
    CONTROL_VARIABLE, ENABLED_VARIABLE, RuntimeConfiguration,
)
from .planner import PlannerPolicy

POLICY_VARIABLE = "VERIFORGE_PLANNER_POLICY_PATH"
MAX_POLICY_BYTES = 65_536


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate policy key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Non-JSON policy constant")


def load_policy(path):
    """Load one absolute regular file, bounded before opening and while reading.

    Invalid, concurrently changed, or unreadable configuration returns a disabled
    policy. Never include operator file contents or paths in API errors/logs.
    Policy changes require role restart; canonical control is read on each call.
    """
    descriptor = None
    try:
        if type(path) is not str or not Path(path).is_absolute():
            return PlannerPolicy()
        target = Path(path)
        before_path = target.stat()
        if not stat.S_ISREG(before_path.st_mode) or before_path.st_size > MAX_POLICY_BYTES:
            return PlannerPolicy()
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(target, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_POLICY_BYTES:
            return PlannerPolicy()
        raw = os.read(descriptor, MAX_POLICY_BYTES + 1)
        after = os.fstat(descriptor)
        after_path = target.stat()
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns)
        if (len(raw) > MAX_POLICY_BYTES or len(raw) != before.st_size or
                identity(before_path) != identity(before) or identity(before) != identity(after) or
                identity(after) != identity(after_path)):
            return PlannerPolicy()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
        # JSON strict validation preserves decimal JSON representations while
        # rejecting coerced booleans/integers/float strings and unknown fields.
        return PlannerPolicy.model_validate_json(json.dumps(value), strict=True)
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return PlannerPolicy()
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                return PlannerPolicy()


def load_configuration(environ=None):
    """Read exactly three non-secret settings; invalid opt-in stays disabled."""
    source = os.environ if environ is None else environ
    values = {key: source.get(key) for key in (ENABLED_VARIABLE, CONTROL_VARIABLE, POLICY_VARIABLE)}
    configuration = RuntimeConfiguration.from_mapping(values)
    if not configuration.enabled or configuration.control_path is None:
        return RuntimeConfiguration(), PlannerPolicy()
    policy = load_policy(values[POLICY_VARIABLE])
    if not policy.enabled:
        return RuntimeConfiguration(), PlannerPolicy()
    return configuration, policy


def create_configured_app(engine=None, *, initialize_catalog=True, artifact_root=None,
                          environ=None, planner_clock=None):
    """Uvicorn --factory entrypoint; all model settings come from server policy."""
    from .app import create_app

    configuration, policy = load_configuration(environ)
    return create_app(engine, initialize_catalog=initialize_catalog, artifact_root=artifact_root,
                      planner_policy=policy, planner_control=configuration.control,
                      planner_clock=planner_clock)
