"""
Futuris/predict/realtime_event_impact.py — Futuris Real-time Event Impact Predictor.

Predicts the immediate and cascading impact of real-time events on markets,
supply chains, sentiment, and risk across multiple domains with scenario
simulation and cascade analysis.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class Event:
    """A real-time event."""
    event_id: str
    event_type: str  # market, geopolitical, natural, economic, social, tech
    title: str
    description: str
    severity: str  # low, medium, high, critical
    location: str = ""
    affected_domains: list[str] = field(default_factory=list)
    timestamp: float = 0.0
    source: str = ""
    confidence: float = 0.5


@dataclass
class ImpactPrediction:
    """Predicted impact of an event."""
    event_id: str
    prediction_id: str
    immediate_impact: dict = field(default_factory=dict)  # domain → impact_score
    cascading_impacts: list[str] = field(default_factory=list)
    market_impact: dict = field(default_factory=dict)  # symbol → direction + magnitude
    supply_chain_impact: list[str] = field(default_factory=list)
    sentiment_impact: dict = field(default_factory=dict)  # metric → change
    risk_level: str = "low"
    recovery_time_hours: float = 0.0
    confidence: float = 0.0
    model: str = "ensemble"


@dataclass
class CascadeNode:
    """A node in the cascade graph."""
    node_id: str
    event_id: str
    parent_node: str = ""
    impacted_domain: str = ""
    impact_score: float = 0.0
    time_delay_hours: float = 0.0
    description: str = ""


@dataclass
class ScenarioSimulation:
    """A scenario simulation."""
    simulation_id: str
    event_id: str
    scenario_name: str
    assumptions: list[str] = field(default_factory=list)
    predictions: list[ImpactPrediction] = field(default_factory=list)
    total_impact: float = 0.0
    probability: float = 0.5


@dataclass
class RiskAssessment:
    """Risk assessment for an event."""
    event_id: str
    overall_risk: float
    market_risk: float
    supply_chain_risk: float
    sentiment_risk: float
    operational_risk: float
    recommendations: list[str] = field(default_factory=list)


class RealTimeEventImpactPredictor:
    """Real-time event impact prediction engine."""

    def __init__(self):
        self._events: dict[str, Event] = {}
        self._predictions: dict[str, ImpactPrediction] = {}
        self._cascades: list[CascadeNode] = []
        self._scenarios: dict[str, ScenarioSimulation] = {}

        # Impact model: event type → domain impact mapping
        self._impact_model = {
            "market": {"market": 0.8, "supply_chain": 0.3, "sentiment": 0.5, "operational": 0.2},
            "geopolitical": {"market": 0.7, "supply_chain": 0.8, "sentiment": 0.6, "operational": 0.5},
            "natural": {"market": 0.5, "supply_chain": 0.9, "sentiment": 0.4, "operational": 0.7},
            "economic": {"market": 0.9, "supply_chain": 0.5, "sentiment": 0.6, "operational": 0.4},
            "social": {"market": 0.4, "supply_chain": 0.3, "sentiment": 0.8, "operational": 0.2},
            "tech": {"market": 0.6, "supply_chain": 0.4, "sentiment": 0.5, "operational": 0.8},
        }

    def register_event(
        self,
        event_type: str,
        title: str,
        description: str,
        severity: str,
        location: str = "",
        affected_domains: list[str] | None = None,
        source: str = "",
        confidence: float = 0.5,
    ) -> Event:
        """Register a real-time event."""
        event = Event(
            event_id=hashlib.md5(
                f"event:{event_type}:{title}:{time.time()}".encode()
            ).hexdigest()[:12],
            event_type=event_type,
            title=title,
            description=description,
            severity=severity,
            location=location,
            affected_domains=affected_domains or list(self._impact_model.get(event_type, {}).keys()),
            timestamp=time.time(),
            source=source,
            confidence=confidence,
        )
        self._events[event.event_id] = event
        return event

    def predict_impact(self, event_id: str) -> ImpactPrediction:
        """Predict the impact of an event."""
        event = self._events.get(event_id)
        if not event:
            raise HTTPException(404, f"Event {event_id} not found")

        impact_model = self._impact_model.get(event.event_type, {})
        severity_multiplier = {"low": 0.5, "medium": 1.0, "high": 1.5, "critical": 2.0}.get(event.severity, 1.0)

        # Calculate immediate impact
        immediate_impact = {}
        for domain, base_impact in impact_model.items():
            immediate_impact[domain] = min(1.0, base_impact * severity_multiplier * event.confidence)

        # Determine risk level
        avg_impact = sum(immediate_impact.values()) / max(1, len(immediate_impact))
        if avg_impact > 0.7:
            risk_level = "critical"
        elif avg_impact > 0.5:
            risk_level = "high"
        elif avg_impact > 0.3:
            risk_level = "medium"
        else:
            risk_level = "low"

        # Calculate market impact
        market_impact = {}
        symbols = ["BTC", "ETH", "SPX", "GOLD", "OIL"]
        for symbol in symbols:
            direction = 1 if event.event_type in ("economic", "market") else -1
            magnitude = immediate_impact.get("market", 0) * severity_multiplier * direction
            market_impact[symbol] = {"direction": "up" if magnitude > 0 else "down", "magnitude": abs(magnitude)}

        # Calculate supply chain impact
        supply_chain_impact = []
        if immediate_impact.get("supply_chain", 0) > 0.5:
            supply_chain_impact.append("Global logistics delays")
            supply_chain_impact.append("Supplier disruptions")
            supply_chain_impact.append("Inventory shortages")
        elif immediate_impact.get("supply_chain", 0) > 0.3:
            supply_chain_impact.append("Moderate logistics delays")

        # Calculate sentiment impact
        sentiment_impact = {}
        sentiment_metrics = ["social_media", "news_sentiment", "consumer_confidence", "market_sentiment"]
        for metric in sentiment_metrics:
            change = -immediate_impact.get("sentiment", 0) * severity_multiplier
            sentiment_impact[metric] = change

        # Estimate recovery time
        recovery_hours = avg_impact * 24 * severity_multiplier

        # Generate cascade predictions
        cascading_impacts = self._generate_cascades(event, immediate_impact, severity_multiplier)

        prediction = ImpactPrediction(
            event_id=event_id,
            prediction_id=hashlib.md5(
                f"impact:{event_id}:{time.time()}".encode()
            ).hexdigest()[:12],
            immediate_impact=immediate_impact,
            cascading_impacts=cascading_impacts,
            market_impact=market_impact,
            supply_chain_impact=supply_chain_impact,
            sentiment_impact=sentiment_impact,
            risk_level=risk_level,
            recovery_time_hours=recovery_hours,
            confidence=event.confidence * 0.8,
            model="ensemble",
        )

        self._predictions[prediction.prediction_id] = prediction
        return prediction

    def _generate_cascades(
        self,
        event: Event,
        immediate_impact: dict,
        severity_multiplier: float,
    ) -> list[str]:
        """Generate cascade predictions."""
        cascades = []

        if immediate_impact.get("market", 0) > 0.5:
            cascades.append("Market volatility spike → hedging demand increase → derivative volume surge")
            cascades.append("Market drop → wealth effect reduction → consumer spending decline")

        if immediate_impact.get("supply_chain", 0) > 0.5:
            cascades.append("Supply disruption → price increase → demand destruction → economic slowdown")
            cascades.append("Logistics delay → inventory buildup → cash flow strain → credit risk increase")

        if immediate_impact.get("sentiment", 0) > 0.5:
            cascades.append("Sentiment decline → social media amplification → brand impact → sales decline")
            cascades.append("Consumer panic → hoarding behavior → supply chain further strained")

        if immediate_impact.get("operational", 0) > 0.5:
            cascades.append("Operational disruption → service degradation → customer churn → revenue loss")
            cascades.append("System outage → data loss → compliance risk → regulatory action")

        return cascades

    def simulate_scenario(
        self,
        event_id: str,
        scenario_name: str,
        assumptions: list[str],
    ) -> ScenarioSimulation:
        """Simulate a scenario for an event."""
        event = self._events.get(event_id)
        if not event:
            raise HTTPException(404, f"Event {event_id} not found")

        # Calculate scenario impact
        scenario_severity = event.severity
        if any("worse" in a.lower() for a in assumptions):
            scenario_severity = "critical"
        elif any("better" in a.lower() for a in assumptions):
            scenario_severity = "low"

        # Create a modified event
        modified_event = Event(
            event_id=event_id,
            event_type=event.event_type,
            title=event.title,
            description=event.description,
            severity=scenario_severity,
            location=event.location,
            affected_domains=event.affected_domains,
            timestamp=time.time(),
            source=event.source,
            confidence=event.confidence * 0.8,
        )

        # Predict impact for scenario
        prediction = self._predict_for_event(modified_event)

        simulation = ScenarioSimulation(
            simulation_id=hashlib.md5(
                f"simulation:{event_id}:{scenario_name}:{time.time()}".encode()
            ).hexdigest()[:12],
            event_id=event_id,
            scenario_name=scenario_name,
            assumptions=assumptions,
            predictions=[prediction],
            total_impact=sum(prediction.immediate_impact.values()),
            probability=0.3 + 0.2 * len(assumptions),
        )

        self._scenarios[simulation.simulation_id] = simulation
        return simulation

    def _predict_for_event(self, event: Event) -> ImpactPrediction:
        """Predict impact for an event (internal method)."""
        impact_model = self._impact_model.get(event.event_type, {})
        severity_multiplier = {"low": 0.5, "medium": 1.0, "high": 1.5, "critical": 2.0}.get(event.severity, 1.0)

        immediate_impact = {}
        for domain, base_impact in impact_model.items():
            immediate_impact[domain] = min(1.0, base_impact * severity_multiplier * event.confidence)

        avg_impact = sum(immediate_impact.values()) / max(1, len(immediate_impact))
        risk_level = "critical" if avg_impact > 0.7 else "high" if avg_impact > 0.5 else "medium" if avg_impact > 0.3 else "low"

        return ImpactPrediction(
            event_id=event.event_id,
            prediction_id=hashlib.md5(
                f"impact:{event.event_id}:{event.severity}:{time.time()}".encode()
            ).hexdigest()[:12],
            immediate_impact=immediate_impact,
            cascading_impacts=self._generate_cascades(event, immediate_impact, severity_multiplier),
            risk_level=risk_level,
            recovery_time_hours=avg_impact * 24 * severity_multiplier,
            confidence=event.confidence * 0.8,
            model="ensemble",
        )

    def assess_risk(self, event_id: str) -> RiskAssessment:
        """Assess risk for an event."""
        event = self._events.get(event_id)
        if not event:
            raise HTTPException(404, f"Event {event_id} not found")

        prediction = self.predict_impact(event_id)

        overall_risk = sum(prediction.immediate_impact.values()) / max(1, len(prediction.immediate_impact))

        recommendations = []
        if overall_risk > 0.7:
            recommendations.extend([
                "Activate crisis response team immediately",
                "Implement contingency plans across all affected domains",
                "Increase monitoring frequency to real-time",
                "Communicate proactively with stakeholders",
            ])
        elif overall_risk > 0.5:
            recommendations.extend([
                "Prepare contingency plans",
                "Increase monitoring frequency",
                "Review risk exposure",
            ])
        else:
            recommendations.extend([
                "Monitor situation",
                "Review operational readiness",
            ])

        assessment = RiskAssessment(
            event_id=event_id,
            overall_risk=overall_risk,
            market_risk=prediction.immediate_impact.get("market", 0),
            supply_chain_risk=prediction.immediate_impact.get("supply_chain", 0),
            sentiment_risk=prediction.immediate_impact.get("sentiment", 0),
            operational_risk=prediction.immediate_impact.get("operational", 0),
            recommendations=recommendations,
        )

        return assessment

    def get_report(self) -> dict:
        """Get event impact predictor report."""
        return {
            "total_events": len(self._events),
            "total_predictions": len(self._predictions),
            "total_scenarios": len(self._scenarios),
            "event_types": {
                et: sum(1 for e in self._events.values() if e.event_type == et)
                for et in set(e.event_type for e in self._events.values())
            },
            "avg_confidence": sum(e.confidence for e in self._events.values()) / max(1, len(self._events)),
            "events_by_severity": {
                sev: sum(1 for e in self._events.values() if e.severity == sev)
                for sev in ["low", "medium", "high", "critical"]
            },
            "recent_predictions": [
                {
                    "event": e.title,
                    "risk_level": p.risk_level,
                    "confidence": p.confidence,
                }
                for e, p in zip(self._events.values(), self._predictions.values())
            ][-10:],
        }


# Module-level singleton
event_impact_predictor = RealTimeEventImpactPredictor()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/futuris/event-impact/events")
async def register_event(payload: dict):
    """Register a real-time event."""
    event = event_impact_predictor.register_event(
        event_type=payload["event_type"],
        title=payload["title"],
        description=payload["description"],
        severity=payload["severity"],
        location=payload.get("location", ""),
        affected_domains=payload.get("affected_domains", []),
        source=payload.get("source", ""),
        confidence=payload.get("confidence", 0.5),
    )
    return {"event_id": event.event_id, "title": event.title, "severity": event.severity}


@router.post("/api/v1/futuris/event-impact/{event_id}/predict")
async def predict_impact(event_id: str):
    """Predict impact of an event."""
    prediction = event_impact_predictor.predict_impact(event_id)
    return {
        "prediction_id": prediction.prediction_id,
        "immediate_impact": prediction.immediate_impact,
        "cascading_impacts": prediction.cascading_impacts,
        "market_impact": prediction.market_impact,
        "supply_chain_impact": prediction.supply_chain_impact,
        "sentiment_impact": prediction.sentiment_impact,
        "risk_level": prediction.risk_level,
        "recovery_time_hours": prediction.recovery_time_hours,
        "confidence": prediction.confidence,
    }


@router.post("/api/v1/futuris/event-impact/{event_id}/scenarios")
async def simulate_scenario(event_id: str, payload: dict):
    """Simulate a scenario for an event."""
    simulation = event_impact_predictor.simulate_scenario(
        event_id=event_id,
        scenario_name=payload["scenario_name"],
        assumptions=payload["assumptions"],
    )
    return {
        "simulation_id": simulation.simulation_id,
        "scenario_name": simulation.scenario_name,
        "total_impact": simulation.total_impact,
        "probability": simulation.probability,
    }


@router.post("/api/v1/futuris/event-impact/{event_id}/risk")
async def assess_risk(event_id: str):
    """Assess risk for an event."""
    assessment = event_impact_predictor.assess_risk(event_id)
    return assessment.__dict__


@router.get("/api/v1/futuris/event-impact/report")
async def get_report():
    """Get event impact predictor report."""
    return event_impact_predictor.get_report()
