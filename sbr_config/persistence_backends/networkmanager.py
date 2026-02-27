"""NetworkManager dispatcher script persistence backend."""

import logging
import os
from typing import List

from ..constants import (
    MANAGED_COMMENT,
    NM_DISPATCHER_DIR,
    NM_DISPATCHER_SCRIPT,
    TABLE_NAME_PREFIX,
)
from ..models import InterfaceInfo, PlannedChange, RoutingTable
from ..utils import read_file, write_file_atomic
from .base import PersistenceBackend

logger = logging.getLogger(__name__)


class NetworkManagerBackend(PersistenceBackend):
    """Write a NetworkManager dispatcher script for SBR persistence.

    Creates /etc/NetworkManager/dispatcher.d/50-sbr-config which is
    called by NetworkManager when interfaces come up or go down.
    """

    def write_config(
        self,
        interfaces: List[InterfaceInfo],
        tables: List[RoutingTable],
        changes: List[PlannedChange],
    ) -> List[str]:
        script_path = os.path.join(NM_DISPATCHER_DIR, NM_DISPATCHER_SCRIPT)

        if not os.path.isdir(NM_DISPATCHER_DIR):
            os.makedirs(NM_DISPATCHER_DIR, exist_ok=True)

        # Build the dispatcher script
        script = self._generate_script(interfaces, tables, changes)
        write_file_atomic(script_path, script, mode=0o755)

        logger.info("Wrote NM dispatcher script: %s", script_path)
        return [script_path]

    def remove_config(self) -> List[str]:
        script_path = os.path.join(NM_DISPATCHER_DIR, NM_DISPATCHER_SCRIPT)
        removed = []
        if os.path.exists(script_path):
            content = read_file(script_path)
            if content and MANAGED_COMMENT in content:
                os.unlink(script_path)
                removed.append(script_path)
                logger.info("Removed NM dispatcher script: %s", script_path)
        return removed

    def describe(self) -> str:
        script_path = os.path.join(NM_DISPATCHER_DIR, NM_DISPATCHER_SCRIPT)
        return (
            f"NetworkManager dispatcher script at {script_path}\n"
            f"Called automatically when interfaces come up/down."
        )

    def _generate_script(
        self,
        interfaces: List[InterfaceInfo],
        tables: List[RoutingTable],
        changes: List[PlannedChange],
    ) -> str:
        """Generate the bash dispatcher script content."""
        lines = [
            "#!/bin/bash",
            MANAGED_COMMENT,
            "# NetworkManager dispatcher script for source-based routing.",
            "# Called with $1=interface_name $2=action (up/down)",
            "",
            'IFACE="$1"',
            'ACTION="$2"',
            "",
            'case "$IFACE" in',
        ]

        # Group changes by interface
        iface_map = {}
        for iface in interfaces:
            table_name = f"{TABLE_NAME_PREFIX}{iface.name}"
            iface_map[iface.name] = {
                "iface": iface,
                "table_name": table_name,
                "up_commands": [],
                "down_commands": [],
            }

        for change in changes:
            if not change.interface or change.interface not in iface_map:
                continue
            entry = iface_map[change.interface]

            # Skip sysctl and rt_table changes -- those are handled globally
            if change.command.startswith("sysctl") or change.command.startswith("echo"):
                continue

            entry["up_commands"].append(change.command)
            if change.rollback_command:
                entry["down_commands"].append(change.rollback_command)

        for iface_name, entry in iface_map.items():
            if not entry["up_commands"]:
                continue

            lines.append(f"    {iface_name})")
            lines.append('        if [ "$ACTION" = "up" ]; then')
            for cmd in entry["up_commands"]:
                lines.append(f"            {cmd}")
            lines.append('        elif [ "$ACTION" = "down" ]; then')
            for cmd in reversed(entry["down_commands"]):
                lines.append(f"            {cmd} 2>/dev/null")
            lines.append("        fi")
            lines.append("        ;;")

        lines.append("esac")
        lines.append("")

        return "\n".join(lines)
