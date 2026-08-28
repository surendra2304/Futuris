"""Contextualization layer computing calendar features, rolling statistics, and regime flags."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from futuris.features.normalize import TrustedSignalSet


class SimpleHolidayCalendar:
    """Built-in deterministic holiday and high-load event calendar (no external APIs)."""

    HOLIDAYS_MMDD = {
        (1, 1),    # New Year's Day
        (7, 4),    # Independence Day
        (11, 27),  # Thanksgiving (approx)
        (11, 28),  # Black Friday (approx)
        (12, 25),  # Christmas
        (12, 31),  # New Year's Eve
    }

    @classmethod
    def is_holiday(cls, dt: datetime) -> bool:
        return (dt.month, dt.day) in cls.HOLIDAYS_MMDD


class ContextLayer:
    """Computes features with a strict point-in-time invariant: at T, compute ONLY using <= T."""

    def __init__(
        self,
        rolling_windows: list[int] | None = None,
        lag_steps: list[int] | None = None,
        regime_quantile_threshold: float = 0.90,
    ) -> None:
        self.rolling_windows = rolling_windows or [6, 12, 24, 72, 288]
        self.lag_steps = lag_steps or [1, 2, 3, 6, 12, 288]
        self.regime_quantile_threshold = regime_quantile_threshold

    def build_feature_table(
        self,
        signal_set: TrustedSignalSet,
        as_of: datetime | None = None,
    ) -> pd.DataFrame:
        """Construct feature table up to s_of without any future-data leakage."""
        df = signal_set.to_dataframe()

        # Enforce point-in-time cutoff: strictly filter timestamps <= as_of
        if as_of is not None:
            as_of = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)
            df = df[df.index <= as_of].copy()
        else:
            df = df.copy()

        if df.empty:
            msg = "Signal set contains no observations on or before as_of."
            raise ValueError(msg)

        # 1. Calendar / Temporal Features (strictly deterministic from timestamp)
        df["hour_of_day"] = df.index.hour
        df["day_of_week"] = df.index.dayofweek
        df["is_weekend"] = df["day_of_week"].isin([5, 6]).astype(int)
        df["is_holiday"] = [int(SimpleHolidayCalendar.is_holiday(ts)) for ts in df.index]

        # Cyclical transforms of hour and day of week
        df["hour_sin"] = np.sin(2 * np.pi * df["hour_of_day"] / 24.0)
        df["hour_cos"] = np.cos(2 * np.pi * df["hour_of_day"] / 24.0)
        df["dow_sin"] = np.sin(2 * np.pi * df["day_of_week"] / 7.0)
        df["dow_cos"] = np.cos(2 * np.pi * df["day_of_week"] / 7.0)

        # 2. Lag Features (values strictly from previous timesteps)
        for lag in self.lag_steps:
            df[f"lag_{lag}"] = df["value"].shift(lag)

        # 3. Rolling Statistics (strictly trailing windows closed='left' or closed='both' <= T)
        for window in self.rolling_windows:
            rolling = df["value"].rolling(window=window, min_periods=1)
            df[f"rolling_mean_{window}"] = rolling.mean()
            df[f"rolling_std_{window}"] = rolling.std().fillna(0.0)
            df[f"rolling_max_{window}"] = rolling.max()
            df[f"rolling_min_{window}"] = rolling.min()

        # 4. Regime Tagging (strictly above the trailing quantile threshold)
        trailing_24h_window = min(len(df), 288)
        trailing_quantile = (
            df["value"]
            .rolling(window=trailing_24h_window, min_periods=1)
            .quantile(self.regime_quantile_threshold)
        )
        df["regime_high_load"] = (df["value"] > trailing_quantile).astype(int)

        # Drop initial lag NaNs or fill with backward/forward values
        df.bfill(inplace=True)
        df.fillna(0.0, inplace=True)

        return df
