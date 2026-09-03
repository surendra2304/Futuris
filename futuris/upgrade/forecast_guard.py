from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Iterable

from .models import ForecastEnvelope


class DataLeakageError(RuntimeError):
    pass


class DataQualityError(ValueError):
    pass


def normalize_as_of(as_of: datetime) -> datetime:
    return as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)


def validate_point_in_time(
    timestamps: Iterable[datetime], as_of: datetime, *, allow_equal: bool = True
) -> None:
    cutoff = normalize_as_of(as_of)
    for ts in timestamps:
        current = normalize_as_of(ts)
        if current > cutoff or (not allow_equal and current == cutoff):
            raise DataLeakageError(f"future observation found: {current.isoformat()} > as_of {cutoff.isoformat()}")


def compute_horizon_steps(horizon: timedelta, step_minutes: int) -> int:
    if step_minutes <= 0:
        raise ValueError("step_minutes must be positive")
    seconds = horizon.total_seconds()
    if seconds <= 0:
        raise ValueError("horizon must be positive")
    return max(1, int(seconds // (step_minutes * 60)))


def ensure_monotonic_unique(timestamps: Iterable[datetime]) -> list[datetime]:
    out = [normalize_as_of(ts) for ts in timestamps]
    if out != sorted(out):
        raise DataQualityError("timestamps must be monotonic")
    if len(out) != len(set(out)):
        raise DataQualityError("duplicate timestamps detected")
    return out


@dataclass(frozen=True)
class ProductionDataSource:
    name: str
    fetch: Callable[[datetime, datetime], object]
    synthetic: bool = False


class SourcePolicy:
    def __init__(self, production: bool) -> None:
        self.production = production

    def validate(self, source: ProductionDataSource) -> None:
        if self.production and source.synthetic:
            raise DataQualityError(f"synthetic source '{source.name}' is forbidden in production")
