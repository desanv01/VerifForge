"""Local, import-only KiCad evidence and connectivity support.

No tool execution, design mutation, electrical signoff, or repair acceptance.
"""

from .facts import MappingRequirement, check_mapping, parse_board, parse_netlist
from .reports import import_report, parse_report
from .readiness import Check, command_argv, inventory

__all__ = [
    "Check", "MappingRequirement", "check_mapping", "command_argv", "import_report",
    "inventory", "parse_board", "parse_netlist", "parse_report",
]
