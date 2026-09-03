from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .models import ActionRisk


class ToolDenied(PermissionError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    callable: Callable[..., Any]
    risk: ActionRisk
    scopes: frozenset[str] = frozenset()
    description: str = ""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolDenied(f"unknown tool: {name}") from exc

    def allowed(self, name: str, *, scopes: frozenset[str], approved: bool) -> ToolSpec:
        spec = self.get(name)
        missing = spec.scopes - scopes
        if missing:
            raise ToolDenied(f"missing scopes: {sorted(missing)}")
        if spec.risk in {ActionRisk.GOVERNED, ActionRisk.FORBIDDEN} and not approved:
            raise ToolDenied("explicit approval required")
        if spec.risk == ActionRisk.FORBIDDEN:
            raise ToolDenied("tool is forbidden")
        return spec

    def names(self) -> list[str]:
        return sorted(self._tools)
