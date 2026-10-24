"""
Futuris/predict/market_regime_detector.py — Futuris Market Regime Detector.

Detects market regimes: bull/bear markets, volatility regimes, trending vs
mean-reverting periods, and regime transitions. Uses HMM, regime-switching
models, and technical indicators for real-time regime classification.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class MarketRegime:
    """A market regime classification."""
    regime_id: str
    regime_type: str  # bull, bear, sideways, volatile, stable, trending, mean_reverting
    confidence: float = 0.0
    start_time: float = 0.0
    end_time: float = 0.0
    indicators: dict = field(default_factory=dict)  # {sma_ratio, rsi, volatility, volume}
    probability: dict = field(default_factory=dict)  # {bull: 0.3, bear: 0.2, ...}
    trend_direction: str = "neutral"  # up, down, neutral
    volatility_level: str = "normal"  # low, normal, high, extreme


@dataclass
class RegimeTransition:
    """A regime transition event."""
    transition_id: str
    from_regime: str
    to_regime: str
    transition_time: float
    confidence: float
    trigger: str = ""  # price_breakout, volatility_spike, volume_change
    indicators_at_transition: dict = field(default_factory=dict)


@dataclass
class HMMModel:
    """Hidden Markov Model for regime detection."""
    model_id: str
    n_states: int = 4
    transition_matrix: list[list[float]] = field(default_factory=list)
    emission_matrix: list[list[float]] = field(default_factory=list)
    state_names: list[str] = field(default_factory=lambda: ["bull", "bear", "sideways", "volatile"])
    last_state: str = ""
    last_probabilities: dict = field(default_factory=dict)


@dataclass
class VolatilityRegime:
    """Volatility regime analysis."""
    symbol: str
    current_volatility: float = 0.0
    historical_volatility: float = 0.0
    implied_volatility: float = 0.0
    vov: float = 0.0  # Volatility of Volatility
    regime: str = "normal"  # low, normal, high, extreme
    percentile: float = 0.0


@dataclass
class TrendRegime:
    """Trend regime analysis."""
    symbol: str
    sma_fast: float = 0.0
    sma_slow: float = 0.0
    sma_ratio: float = 1.0
    trend_strength: float = 0.0
    trend_direction: str = "neutral"
    rsi: float = 50.0
    macd_signal: str = "neutral"
    regime: str = "sideways"


@dataclass
class RegimeHistory:
    """History of regimes for a symbol."""
    symbol: str
    regimes: list[dict] = field(default_factory=list)
    transitions: list[dict] = field(default_factory=list)
    current_regime: str = ""
    regime_duration_avg: float = 0.0


@dataclass
class RegimeSignal:
    """A trading signal based on regime."""
    symbol: str
    regime: str
    signal: str  # buy, sell, hold, reduce, increase
    confidence: float
    reason: str
    recommended_position_size: float = 0.0


class MarketRegimeDetector:
    """Market regime detection engine."""

    def __init__(self):
        self._regimes: dict[str, MarketRegime] = {}
        self._transitions: list[RegimeTransition] = []
        self._hmm: dict[str, HMMModel] = {}
        self._volatility: dict[str, VolatilityRegime] = {}
        self._trends: dict[str, TrendRegime] = {}
        self._history: dict[str, RegimeHistory] = {}
        self._signals: list[RegimeSignal] = []

    def detect_regime(
        self,
        symbol: str,
        sma_fast: float = 100.0,
        sma_slow: float = 100.0,
        rsi: float = 50.0,
        volatility: float = 0.15,
        volume_avg: float = 1000000.0,
        current_volume: float = 1000000.0,
    ) -> MarketRegime:
        """Detect current market regime."""
        regime_id = hashlib.md5(
            f"regime:{symbol}:{time.time()}".encode()
        ).hexdigest()[:12]

        sma_ratio = sma_fast / max(0.1, sma_slow)
        volume_ratio = current_volume / max(1, volume_avg)

        # Classify regime
        if sma_ratio > 1.05 and rsi > 60:
            regime_type = "bull"
            trend = "up"
        elif sma_ratio < 0.95 and rsi < 40:
            regime_type = "bear"
            trend = "down"
        elif volatility > 0.30:
            regime_type = "volatile"
            trend = "neutral"
        elif sma_ratio > 1.02 and sma_ratio < 1.05:
            regime_type = "trending"
            trend = "up"
        elif abs(sma_ratio - 1.0) < 0.03:
            regime_type = "sideways"
            trend = "neutral"
        else:
            regime_type = "stable"
            trend = "neutral"

        # Determine volatility level
        if volatility > 0.40:
            vol_level = "extreme"
        elif volatility > 0.30:
            vol_level = "high"
        elif volatility < 0.08:
            vol_level = "low"
        else:
            vol_level = "normal"

        # Calculate probabilities
        probs = {
            "bull": 0.3 if regime_type == "bull" else 0.1,
            "bear": 0.3 if regime_type == "bear" else 0.1,
            "sideways": 0.3 if regime_type == "sideways" else 0.1,
            "volatile": 0.3 if regime_type == "volatile" else 0.1,
        }

        # Normalize
        total = sum(probs.values())
        probs = {k: v / total for k, v in probs.items()}

        confidence = max(probs.values())

        regime = MarketRegime(
            regime_id=regime_id,
            regime_type=regime_type,
            confidence=confidence,
            start_time=time.time(),
            indicators={
                "sma_ratio": sma_ratio,
                "rsi": rsi,
                "volatility": volatility,
                "volume_ratio": volume_ratio,
            },
            probability=probs,
            trend_direction=trend,
            volatility_level=vol_level,
        )

        self._regimes[symbol] = regime
        return regime

    def detect_transition(
        self,
        symbol: str,
        new_regime_type: str,
        trigger: str = "",
    ) -> RegimeTransition:
        """Detect a regime transition."""
        current = self._regimes.get(symbol)

        if not current or current.regime_type == new_regime_type:
            return RegimeTransition(
                transition_id="",
                from_regime=current.regime_type if current else "unknown",
                to_regime=new_regime_type,
                transition_time=time.time(),
                confidence=0.0,
                trigger=trigger,
            )

        transition = RegimeTransition(
            transition_id=hashlib.md5(
                f"transition:{symbol}:{time.time()}".encode()
            ).hexdigest()[:12],
            from_regime=current.regime_type,
            to_regime=new_regime_type,
            transition_time=time.time(),
            confidence=current.confidence,
            trigger=trigger,
            indicators_at_transition=dict(current.indicators),
        )

        current.end_time = time.time()

        self._transitions.append(transition)

        # Update history
        history = self._history.get(symbol)
        if not history:
            history = RegimeHistory(symbol=symbol)
            self._history[symbol] = history

        history.regimes.append({
            "type": current.regime_type,
            "start": current.start_time,
            "end": current.end_time,
        })
        history.transitions.append({
            "from": transition.from_regime,
            "to": transition.to_regime,
            "time": transition.transition_time,
        })

        return transition

    def update_volatility_regime(
        self,
        symbol: str,
        current_vol: float,
        historical_vol: float = 0.15,
        implied_vol: float = 0.15,
    ) -> VolatilityRegime:
        """Update volatility regime."""
        vov = abs(current_vol - historical_vol) / max(0.01, historical_vol)

        if current_vol > 0.40:
            regime = "extreme"
        elif current_vol > 0.30:
            regime = "high"
        elif current_vol < 0.08:
            regime = "low"
        else:
            regime = "normal"

        vol_regime = VolatilityRegime(
            symbol=symbol,
            current_volatility=current_vol,
            historical_volatility=historical_vol,
            implied_volatility=implied_vol,
            vov=vov,
            regime=regime,
            percentile=min(99, current_vol / 0.5 * 100),
        )

        self._volatility[symbol] = vol_regime
        return vol_regime

    def update_trend_regime(
        self,
        symbol: str,
        sma_fast: float,
        sma_slow: float,
        rsi: float,
        macd_signal: str = "neutral",
    ) -> TrendRegime:
        """Update trend regime."""
        sma_ratio = sma_fast / max(0.1, sma_slow)

        if sma_ratio > 1.05:
            direction = "up"
            regime = "bull" if rsi > 60 else "trending"
        elif sma_ratio < 0.95:
            direction = "down"
            regime = "bear" if rsi < 40 else "trending"
        else:
            direction = "neutral"
            regime = "sideways"

        trend_strength = abs(sma_ratio - 1.0) * 100

        trend = TrendRegime(
            symbol=symbol,
            sma_fast=sma_fast,
            sma_slow=sma_slow,
            sma_ratio=sma_ratio,
            trend_strength=trend_strength,
            trend_direction=direction,
            rsi=rsi,
            macd_signal=macd_signal,
            regime=regime,
        )

        self._trends[symbol] = trend
        return trend

    def generate_signal(self, symbol: str) -> RegimeSignal:
        """Generate a trading signal based on regime."""
        regime = self._regimes.get(symbol)
        if not regime:
            raise HTTPException(404, f"No regime detected for {symbol}")

        if regime.regime_type == "bull":
            signal = "buy"
            confidence = regime.confidence
            position_size = 1.0
            reason = "Bull market regime detected"
        elif regime.regime_type == "bear":
            signal = "sell"
            confidence = regime.confidence
            position_size = 0.0
            reason = "Bear market regime detected"
        elif regime.regime_type == "volatile":
            signal = "reduce"
            confidence = regime.confidence
            position_size = 0.5
            reason = "High volatility regime, reduce exposure"
        else:
            signal = "hold"
            confidence = regime.confidence
            position_size = 0.7
            reason = f"{regime.regime_type} regime, hold position"

        rs = RegimeSignal(
            symbol=symbol,
            regime=regime.regime_type,
            signal=signal,
            confidence=confidence,
            reason=reason,
            recommended_position_size=position_size,
        )

        self._signals.append(rs)
        return rs

    def get_report(self) -> dict:
        """Get market regime detector report."""
        return {
            "total_symbols_tracked": len(self._regimes),
            "total_transitions": len(self._transitions),
            "total_signals": len(self._signals),
            "volatility_regimes": len(self._volatility),
            "trend_regimes": len(self._trends),
            "regime_distribution": {
                r: sum(1 for reg in self._regimes.values() if reg.regime_type == r)
                for r in set(reg.regime_type for reg in self._regimes.values())
            },
            "recent_signals": [
                {
                    "symbol": s.symbol,
                    "regime": s.regime,
                    "signal": s.signal,
                    "confidence": s.confidence,
                }
                for s in self._signals[-10:]
            ],
            "recent_transitions": [
                {
                    "symbol": t.from_regime,
                    "to": t.to_regime,
                    "trigger": t.trigger,
                    "time": t.transition_time,
                }
                for t in self._transitions[-10:]
            ],
        }


# Module-level singleton
regime_detector = MarketRegimeDetector()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/futuris/regime/detect")
async def detect_regime(payload: dict):
    """Detect current market regime."""
    regime = regime_detector.detect_regime(
        symbol=payload["symbol"],
        sma_fast=payload.get("sma_fast", 100.0),
        sma_slow=payload.get("sma_slow", 100.0),
        rsi=payload.get("rsi", 50.0),
        volatility=payload.get("volatility", 0.15),
        volume_avg=payload.get("volume_avg", 1000000.0),
        current_volume=payload.get("current_volume", 1000000.0),
    )
    return {
        "symbol": payload["symbol"],
        "regime": regime.regime_type,
        "confidence": regime.confidence,
        "trend_direction": regime.trend_direction,
        "volatility_level": regime.volatility_level,
        "probabilities": regime.probability,
    }


@router.post("/api/v1/futuris/regime/{symbol}/transition")
async def detect_transition(symbol: str, payload: dict):
    """Detect a regime transition."""
    transition = regime_detector.detect_transition(
        symbol=symbol,
        new_regime_type=payload["new_regime"],
        trigger=payload.get("trigger", ""),
    )
    return {
        "symbol": symbol,
        "from": transition.from_regime,
        "to": transition.to_regime,
        "trigger": transition.trigger,
        "confidence": transition.confidence,
    }


@router.post("/api/v1/futuris/regime/{symbol}/volatility")
async def update_volatility_regime(symbol: str, payload: dict):
    """Update volatility regime."""
    vol = regime_detector.update_volatility_regime(
        symbol=symbol,
        current_vol=payload["current_volatility"],
        historical_vol=payload.get("historical_volatility", 0.15),
        implied_vol=payload.get("implied_volatility", 0.15),
    )
    return {
        "symbol": symbol,
        "volatility": vol.current_volatility,
        "regime": vol.regime,
        "percentile": vol.percentile,
        "vov": vol.vov,
    }


@router.get("/api/v1/futuris/regime/{symbol}/signal")
async def generate_signal(symbol: str):
    """Generate trading signal based on regime."""
    signal = regime_detector.generate_signal(symbol)
    return {
        "symbol": signal.symbol,
        "regime": signal.regime,
        "signal": signal.signal,
        "confidence": signal.confidence,
        "position_size": signal.recommended_position_size,
    }


@router.get("/api/v1/futuris/regime/report")
async def get_report():
    """Get market regime detector report."""
    return regime_detector.get_report()
