"""Deterministic synthetic telemetry generator for operational capacity forecasting."""

import math
from datetime import UTC, datetime, timedelta

import numpy as np

from futuris.connectors.base import BaseConnector, Observation


class SyntheticTelemetryConnector(BaseConnector):
    """Deterministic generator producing realistic service demand time-series.

    Simulates:
    - Multi-scale seasonality: Daily diurnal cycles + weekly business-day patterns.
    - Long-term mild organic growth trend.
    - Autocorrelated Gaussian noise.
    - Sporadic incident spikes / promotional surge events.
    - Static and stepped service capacity limits.
    """

    def __init__(
        self,
        service_name: str = "checkout",
        base_demand: float = 1200.0,
        trend_slope_per_day: float = 2.5,
        daily_seasonality_amplitude: float = 800.0,
        weekly_seasonality_amplitude: float = 400.0,
        noise_std: float = 45.0,
        capacity_base: float = 4000.0,
        seed: int = 42,
    ) -> None:
        self.service_name = service_name
        self.base_demand = base_demand
        self.trend_slope_per_day = trend_slope_per_day
        self.daily_seasonality_amplitude = daily_seasonality_amplitude
        self.weekly_seasonality_amplitude = weekly_seasonality_amplitude
        self.noise_std = noise_std
        self.capacity_base = capacity_base
        self.seed = seed

    async def fetch(self, start: datetime, end: datetime) -> list[Observation]:
        """Fetch observations deterministically for the given time range."""
        return self.generate_series(start, end, step_minutes=5)

    def generate_series(
        self, start: datetime, end: datetime, step_minutes: int = 5
    ) -> list[Observation]:
        """Generate a deterministic list of Observations at regular minute intervals."""
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)

        total_seconds = int((end - start).total_seconds())
        step_seconds = step_minutes * 60
        num_steps = max(0, total_seconds // step_seconds) + 1

        # Use reproducible pseudo-random state keyed to seed
        rng = np.random.default_rng(self.seed)

        observations: list[Observation] = []
        base_time = start

        for i in range(num_steps):
            current_time = base_time + timedelta(seconds=i * step_seconds)
            if current_time > end:
                break

            day_fraction = (
                current_time.hour * 3600
                + current_time.minute * 60
                + current_time.second
            ) / 86400.0
            day_of_week = current_time.weekday()
            days_from_start = (current_time - base_time).total_seconds() / 86400.0

            # 1. Daily Diurnal Seasonality (peaks in afternoon ~14:00, troughs at ~04:00)
            daily_pattern = math.sin(2 * math.pi * (day_fraction - 0.25))
            daily_val = self.daily_seasonality_amplitude * daily_pattern

            # 2. Weekly Seasonality (higher Mon-Fri, lower Sat-Sun)
            weekly_pattern = math.cos(2 * math.pi * (day_of_week / 7.0))
            weekly_val = self.weekly_seasonality_amplitude * weekly_pattern

            # 3. Organic Growth Trend
            trend_val = self.trend_slope_per_day * days_from_start

            # 4. Stochastic Gaussian Noise
            noise = rng.normal(0, self.noise_std)

            # 5. Sporadic Incidents / Flash Sales (pseudo-random spikes)
            spike = 0.0
            if rng.random() < 0.0015:
                spike = rng.uniform(800.0, 2200.0)

            # Compute gross demand (bounded below by 0)
            demand_val = max(
                10.0, self.base_demand + daily_val + weekly_val + trend_val + noise + spike
            )

            # Step capacity upgrade scenario (e.g. after day 90 capacity increases)
            capacity = self.capacity_base
            if days_from_start > 90:
                capacity += 1000.0

            obs = Observation(
                observed_at=current_time,
                source="telemetry:synthetic",
                series_id=f"{self.service_name}:requests_per_minute",
                value=round(demand_val, 2),
                unit="rpm",
                tags={
                    "service": self.service_name,
                    "env": "production",
                    "capacity_limit": capacity,
                },
            )
            observations.append(obs)

        return observations
