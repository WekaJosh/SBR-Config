"""Data models for sbr-config."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class NetworkManagerType(Enum):
    NETWORKMANAGER = "NetworkManager"
    SYSTEMD_NETWORKD = "systemd-networkd"
    IFUPDOWN = "ifupdown"
    NETPLAN_NETWORKD = "netplan+systemd-networkd"
    NETPLAN_NM = "netplan+NetworkManager"
    UNKNOWN = "unknown"


class ChangeType(Enum):
    ADD_RT_TABLE = "add_routing_table"
    ADD_ROUTE = "add_route"
    ADD_RULE = "add_rule"
    SET_SYSCTL = "set_sysctl"
    DEL_ROUTE = "delete_route"
    DEL_RULE = "delete_rule"


@dataclass
class InterfaceInfo:
    """Represents a discovered network interface."""
    name: str
    ip_address: str
    prefix_length: int
    subnet: str
    gateway: Optional[str]
    mac_address: str
    is_up: bool
    is_loopback: bool
    is_default_route_interface: bool
    mtu: int

    @property
    def cidr(self) -> str:
        return f"{self.ip_address}/{self.prefix_length}"


@dataclass
class RoutingTable:
    """Represents an entry in /etc/iproute2/rt_tables."""
    number: int
    name: str


@dataclass
class Route:
    """Represents a single ip route entry."""
    destination: str
    gateway: Optional[str]
    device: str
    source: Optional[str] = None
    table: Optional[str] = None
    metric: Optional[int] = None
    scope: Optional[str] = None
    protocol: Optional[str] = None

    def to_args(self) -> str:
        """Convert route to ip-route command arguments."""
        parts = [self.destination]
        if self.gateway:
            parts.extend(["via", self.gateway])
        parts.extend(["dev", self.device])
        if self.source:
            parts.extend(["src", self.source])
        if self.table:
            parts.extend(["table", self.table])
        if self.metric is not None:
            parts.extend(["metric", str(self.metric)])
        if self.scope:
            parts.extend(["scope", self.scope])
        return " ".join(parts)


@dataclass
class Rule:
    """Represents a single ip rule entry."""
    priority: int
    selector_from: Optional[str] = None
    selector_to: Optional[str] = None
    table: Optional[str] = None
    iif: Optional[str] = None
    fwmark: Optional[str] = None

    def to_args(self) -> str:
        """Convert rule to ip-rule command arguments."""
        parts = []
        if self.selector_from:
            parts.extend(["from", self.selector_from])
        if self.selector_to:
            parts.extend(["to", self.selector_to])
        if self.table:
            parts.extend(["table", self.table])
        if self.iif:
            parts.extend(["iif", self.iif])
        if self.fwmark:
            parts.extend(["fwmark", self.fwmark])
        parts.extend(["priority", str(self.priority)])
        return " ".join(parts)


@dataclass
class SysctlSetting:
    """Represents a sysctl key/value pair."""
    key: str
    current_value: Optional[str]
    required_value: str
    description: str
    reason: str

    @property
    def is_correct(self) -> bool:
        return self.current_value == self.required_value


@dataclass
class SystemState:
    """Complete snapshot of current routing state for backup/comparison."""
    interfaces: List[InterfaceInfo]
    routing_tables: List[RoutingTable]
    routes_main: List[Route]
    routes_by_table: Dict[str, List[Route]]
    rules: List[Rule]
    rt_tables_file_content: str
    sysctl_values: Dict[str, str]
    network_manager: NetworkManagerType
    timestamp: str

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        import dataclasses
        d = {}
        d["interfaces"] = [dataclasses.asdict(i) for i in self.interfaces]
        d["routing_tables"] = [dataclasses.asdict(t) for t in self.routing_tables]
        d["routes_main"] = [dataclasses.asdict(r) for r in self.routes_main]
        d["routes_by_table"] = {
            k: [dataclasses.asdict(r) for r in v]
            for k, v in self.routes_by_table.items()
        }
        d["rules"] = [dataclasses.asdict(r) for r in self.rules]
        d["rt_tables_file_content"] = self.rt_tables_file_content
        d["sysctl_values"] = self.sysctl_values
        d["network_manager"] = self.network_manager.value
        d["timestamp"] = self.timestamp
        return d


@dataclass
class PlannedChange:
    """A single atomic change to be applied."""
    change_type: ChangeType
    description: str
    reason: str
    command: str
    interface: Optional[str] = None
    rollback_command: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "change_type": self.change_type.value,
            "description": self.description,
            "reason": self.reason,
            "command": self.command,
            "interface": self.interface,
            "rollback_command": self.rollback_command,
        }


@dataclass
class ValidationResult:
    """Result of validating one aspect of SBR config."""
    interface_name: str
    check_name: str
    is_correct: bool
    current_value: str
    expected_value: str
    fix_description: str

    @property
    def status_symbol(self) -> str:
        return "PASS" if self.is_correct else "FAIL"
