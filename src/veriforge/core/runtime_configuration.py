"""Explicit planner-role control config; no provider/broker configuration reads.

Environment is read only on an explicit resolver call, never at import. Main
may map these two variables into roles; this module neither loads env files nor
changes process environment. Manual jobs retain legacy control unless supplied.
"""
from dataclasses import dataclass
import os
from pathlib import Path

from .runtime_control import ControlReader

ENABLED_VARIABLE = "VERIFORGE_PLANNER_ENABLED"
CONTROL_VARIABLE = "VERIFORGE_CONTROL_PATH"


@dataclass(frozen=True)
class RuntimeConfiguration:
    enabled: bool = False
    control_path: Path | None = None

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("Planner enabled flag must be boolean")
        if self.control_path is not None:
            path = Path(self.control_path)
            if not path.is_absolute():
                raise ValueError("Planner control path must be absolute")
            object.__setattr__(self, "control_path", path)

    @classmethod
    def from_mapping(cls, values):
        try:
            enabled = values.get(ENABLED_VARIABLE) == "1"
            raw_path = values.get(CONTROL_VARIABLE)
            if raw_path is not None and type(raw_path) is not str:
                return cls()
            return cls(enabled=enabled, control_path=Path(raw_path) if raw_path else None)
        except (OSError, ValueError, TypeError, AttributeError):
            return cls()  # Invalid service configuration cannot authorize work.

    @classmethod
    def from_environment(cls):
        return cls.from_mapping(os.environ)

    def control(self):
        if not self.enabled or self.control_path is None:
            return "STOPPED"
        return ControlReader(self.control_path)()


def _legacy_running():
    return "RUNNING"


def resolve_control(*, planner_owned, explicit_control=None, configuration=None):
    """Return a callable, never a cached RUNNING decision or provider object."""
    if explicit_control is not None:
        if not callable(explicit_control):
            raise ValueError("Explicit control must be callable")
        return explicit_control
    if not planner_owned:
        return _legacy_running
    if configuration is None:
        configuration = RuntimeConfiguration.from_environment()
    if type(configuration) is not RuntimeConfiguration:
        raise ValueError("Expected explicit runtime configuration")
    return configuration.control
