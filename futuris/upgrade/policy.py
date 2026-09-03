from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit


class PolicyViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    rule: str


@dataclass(frozen=True)
class Policy:
    allowed_connectors: frozenset[str] = frozenset({"telemetry", "forecast", "llm"})
    blocked_domains: frozenset[str] = frozenset()
    allowed_domains: frozenset[str] = frozenset()
    max_horizon_hours: int = 24 * 30
    max_lookback_days: int = 365
    require_governance_for_actions: bool = True


class PolicyEngine:
    def __init__(self, policy: Policy) -> None:
        self._policy = policy

    def evaluate_connector(self, connector: str) -> PolicyDecision:
        if connector not in self._policy.allowed_connectors:
            return PolicyDecision(False, "connector not permitted", "connector_allowlist")
        return PolicyDecision(True, "allowed", "connector_allowlist")

    def evaluate_forecast(self, horizon_hours: float, lookback_days: int) -> PolicyDecision:
        if horizon_hours <= 0 or horizon_hours > self._policy.max_horizon_hours:
            return PolicyDecision(False, "forecast horizon outside policy", "horizon")
        if lookback_days <= 0 or lookback_days > self._policy.max_lookback_days:
            return PolicyDecision(False, "lookback outside policy", "lookback")
        return PolicyDecision(True, "allowed", "forecast_shape")

    def evaluate_action(self, *, authorized: bool, governed: bool) -> PolicyDecision:
        if self._policy.require_governance_for_actions and governed and not authorized:
            return PolicyDecision(False, "explicit governance authorization required", "governance")
        return PolicyDecision(True, "allowed", "governance")

    def evaluate_url(self, url: str) -> PolicyDecision:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return PolicyDecision(False, "invalid URL", "url_shape")
        host = parts.hostname.lower().rstrip(".")
        if host in self._policy.blocked_domains:
            return PolicyDecision(False, "domain blocked", "domain_block")
        if self._policy.allowed_domains and host not in self._policy.allowed_domains:
            return PolicyDecision(False, "domain not allowlisted", "domain_allowlist")
        try:
            for info in socket.getaddrinfo(host, None):
                ip = ipaddress.ip_address(info[4][0])
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_reserved
                    or ip.is_unspecified
                ):
                    return PolicyDecision(False, f"destination resolves to {ip}", "ssrf")
        except socket.gaierror as exc:
            raise PolicyViolation("DNS resolution failed") from exc
        return PolicyDecision(True, "allowed", "network")
