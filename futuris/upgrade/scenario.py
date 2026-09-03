from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Mapping


class ScenarioValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Scenario:
    name: str
    changes_pct: Mapping[str, float]
    seed: int = 0


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    base: float
    result: float
    delta_pct: float
    draws: tuple[float, ...]


class ScenarioEngine:
    """Deterministic scenario simulation with explicit seed and numeric validation."""

    def run(self, base: float, scenario: Scenario, *, draws: int = 1) -> ScenarioResult:
        if not math.isfinite(base):
            raise ScenarioValidationError("base must be finite")
        if draws < 1 or draws > 100_000:
            raise ScenarioValidationError("draw count outside safe bounds")
        factor = 1.0
        for key, change in scenario.changes_pct.items():
            if not math.isfinite(change) or abs(change) > 1000:
                raise ScenarioValidationError(f"invalid change for {key}")
            factor *= 1.0 + change / 100.0
        if factor < 0:
            raise ScenarioValidationError("scenario creates negative multiplier")
        rng = random.Random(scenario.seed)
        noise = tuple(rng.uniform(0.98, 1.02) for _ in range(draws))
        result = base * factor * sum(noise) / len(noise)
        delta = 0.0 if base == 0 else (result - base) / abs(base) * 100.0
        return ScenarioResult(scenario.name, base, result, delta, noise)
