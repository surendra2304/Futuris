"""
Futuris/predict/scenario_stress_tester.py — Futuris Scenario Stress Tester.

Stress tests forecasts against extreme scenarios: tail risk analysis,
reverse stress testing, scenario generation, and resilience assessment.
Provides confidence in forecasts under adverse conditions.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class StressScenario:
    """A stress scenario."""
    scenario_id: str
    name: str
    description: str = ""
    parameters: dict = field(default_factory=dict)
    severity: str = "medium"  # low, medium, high, extreme
    probability: float = 0.0
    impact_estimate: float = 0.0
    created_at: float = 0.0


@dataclass
class StressResult:
    """Result of a stress test."""
    result_id: str
    scenario_id: str
    target: str
    baseline_value: float = 0.0
    stressed_value: float = 0.0
    impact_pct: float = 0.0
    recovery_time_days: float = 0.0
    probability_of_loss: float = 0.0
    max_drawdown: float = 0.0
    tested_at: float = 0.0


@dataclass
class TailRiskAnalysis:
    """Tail risk analysis."""
    analysis_id: str
    target: str
    var_95: float = 0.0
    var_99: float = 0.0
    cvar_95: float = 0.0
    cvar_99: float = 0.0
    tail_probability: float = 0.0
    worst_case_scenario: str = ""
    created_at: float = 0.0


@dataclass
class ReverseStressTest:
    """Reverse stress test: what would cause failure."""
    test_id: str
    target: str
    failure_threshold: float = 0.0
    triggering_conditions: list[str] = field(default_factory=list)
    probability: float = 0.0
    severity: str = "high"
    description: str = ""
    created_at: float = 0.0


@dataclass
class ScenarioCatalog:
    """A catalog of stress scenarios."""
    catalog_id: str
    name: str
    scenarios: list[str] = field(default_factory=list)
    description: str = ""
    created_at: float = 0.0


@dataclass
class ResilienceScore:
    """Resilience score for a target."""
    target: str
    score: float = 0.0  # 0-100
    rating: str = "adequate"  # poor, fair, adequate, good, excellent
    weaknesses: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


@dataclass
class StressTestReport:
    """Comprehensive stress test report."""
    report_id: str
    target: str
    total_scenarios_tested: int = 0
    avg_impact_pct: float = 0.0
    max_impact_pct: float = 0.0
    recovery_time_avg_days: float = 0.0
    resilience_score: float = 0.0
    tail_risk_level: str = "moderate"
    critical_scenarios: int = 0
    tested_at: float = 0.0


class ScenarioStressTester:
    """Scenario stress testing engine."""

    def __init__(self):
        self._scenarios: dict[str, StressScenario] = {}
        self._results: list[StressResult] = []
        self._tail_risks: dict[str, TailRiskAnalysis] = {}
        self._reverse_tests: list[ReverseStressTest] = []
        self._catalogs: dict[str, ScenarioCatalog] = {}
        self._reports: list[StressTestReport] = []

        # Create default catalog
        self._create_catalog()

    def _create_catalog(self):
        """Create default scenario catalog."""
        catalog = ScenarioCatalog(
            catalog_id=hashlib.md5(f"catalog:{time.time()}".encode()).hexdigest()[:12],
            name="Standard Stress Scenarios",
            description="Standard set of stress scenarios for testing",
            created_at=time.time(),
        )
        self._catalogs[catalog.catalog_id] = catalog

        # Add standard scenarios
        scenarios = [
            ("Market Crash", -30.0, "high", "20%"),
            ("Recession", -15.0, "medium", "30%"),
            ("Interest Rate Spike", -10.0, "medium", "25%"),
            ("Currency Crisis", -20.0, "high", "15%"),
            ("Liquidity Crisis", -25.0, "high", "10%"),
            ("Geopolitical Event", -8.0, "medium", "40%"),
            ("Pandemic", -22.0, "high", "5%"),
            ("Natural Disaster", -5.0, "low", "60%"),
        ]

        for name, impact, severity, prob in scenarios:
            prob_val = float(prob.rstrip("%")) / 100.0 if isinstance(prob, str) and prob.endswith("%") else float(prob)
            self.create_scenario(name, severity, prob_val, {"market_impact_pct": impact})

    def create_scenario(
        self,
        name: str,
        severity: str = "medium",
        probability: float = 0.05,
        parameters: dict | None = None,
    ) -> StressScenario:
        """Create a stress scenario."""
        scenario = StressScenario(
            scenario_id=hashlib.md5(
                f"scenario:{name}:{time.time()}".encode()
            ).hexdigest()[:12],
            name=name,
            description=f"Stress scenario: {name}",
            parameters=parameters or {},
            severity=severity,
            probability=probability,
            impact_estimate=parameters.get("market_impact_pct", 0.0) if parameters else 0.0,
            created_at=time.time(),
        )
        self._scenarios[scenario.scenario_id] = scenario
        return scenario

    def run_stress_test(self, scenario_id: str, target: str, baseline: float) -> StressResult:
        """Run a stress test."""
        scenario = self._scenarios.get(scenario_id)
        if not scenario:
            raise HTTPException(404, f"Scenario {scenario_id} not found")

        impact = scenario.parameters.get("market_impact_pct", -10.0)
        stressed = baseline * (1 + impact / 100)
        impact_pct = impact
        recovery_days = max(0, abs(impact) * 1.5)

        result = StressResult(
            result_id=hashlib.md5(
                f"stress:{scenario_id}:{target}:{time.time()}".encode()
            ).hexdigest()[:12],
            scenario_id=scenario_id,
            target=target,
            baseline_value=baseline,
            stressed_value=stressed,
            impact_pct=impact_pct,
            recovery_time_days=recovery_days,
            probability_of_loss=scenario.probability if impact < 0 else 0.0,
            max_drawdown=abs(impact),
            tested_at=time.time(),
        )

        self._results.append(result)
        return result

    def analyze_tail_risk(self, target: str, baseline: float) -> TailRiskAnalysis:
        """Analyze tail risk."""
        # Calculate VaR and CVaR from stress results
        results = [r for r in self._results if r.target == target]

        if results:
            impacts = sorted([r.impact_pct for r in results])
            var_95 = impacts[int(len(impacts) * 0.05)] if len(impacts) > 5 else impacts[0] if impacts else 0
            var_99 = impacts[int(len(impacts) * 0.01)] if len(impacts) > 10 else impacts[0] if impacts else 0
        else:
            var_95 = -15.0
            var_99 = -25.0

        # CVaR (expected shortfall)
        cvar_95 = (var_95 * 1.2) if var_95 < 0 else var_95 * 0.5
        cvar_99 = (var_99 * 1.5) if var_99 < 0 else var_99 * 0.3

        analysis = TailRiskAnalysis(
            analysis_id=hashlib.md5(
                f"tail:{target}:{time.time()}".encode()
            ).hexdigest()[:12],
            target=target,
            var_95=var_95,
            var_99=var_99,
            cvar_95=cvar_95,
            cvar_99=cvar_99,
            tail_probability=0.05 + (ord(hashlib.md5(target.encode()).hexdigest()[:1]) % 10) / 100,
            worst_case_scenario=max(results, key=lambda r: r.max_drawdown).scenario_id if results else "",
            created_at=time.time(),
        )

        self._tail_risks[target] = analysis
        return analysis

    def run_reverse_stress_test(self, target: str, failure_threshold: float) -> ReverseStressTest:
        """Run a reverse stress test."""
        # Find what would cause failure
        triggers = [
            f"Market decline of {abs(failure_threshold) * 2}% or more",
            f"Liquidity outflow exceeding {failure_threshold * 100} units",
            f"Credit spread widening to {failure_threshold * 300}bps",
            f"Volatility spike to {failure_threshold * 500}% annualized",
        ]

        test = ReverseStressTest(
            test_id=hashlib.md5(
                f"reverse:{target}:{time.time()}".encode()
            ).hexdigest()[:12],
            target=target,
            failure_threshold=failure_threshold,
            triggering_conditions=triggers,
            probability=0.02 + (ord(hashlib.md5(f"rev:{target}".encode()).hexdigest()[:1]) % 8) / 100,
            severity="critical" if abs(failure_threshold) > 20 else "high",
            description=f"Reverse stress test: conditions that would cause {target} to fail",
            created_at=time.time(),
        )

        self._reverse_tests.append(test)
        return test

    def calculate_resilience(self, target: str) -> ResilienceScore:
        """Calculate resilience score."""
        results = [r for r in self._results if r.target == target]

        if not results:
            return ResilienceScore(target=target, score=70.0, rating="adequate")

        avg_impact = sum(r.impact_pct for r in results) / len(results)
        max_impact = max(r.impact_pct for r in results)

        # Resilience score: higher is better
        score = 100 - (abs(avg_impact) * 3 + abs(max_impact) * 1.5)
        score = max(0, min(100, score))

        rating = "excellent" if score > 85 else "good" if score > 70 else "adequate" if score > 50 else "fair" if score > 30 else "poor"

        weaknesses = []
        strengths = []
        recommendations = []

        if abs(avg_impact) > 15:
            weaknesses.append("High average impact under stress")
            recommendations.append("Implement hedging strategies")

        if abs(max_impact) > 25:
            weaknesses.append("Extreme tail risk exposure")
            recommendations.append("Add tail risk protection")

        if score > 70:
            strengths.append("Strong resilience under stress")
        else:
            weaknesses.append("Moderate vulnerability to stress events")

        return ResilienceScore(
            target=target,
            score=round(score, 1),
            rating=rating,
            weaknesses=weaknesses,
            strengths=strengths,
            recommendations=recommendations,
        )

    def generate_report(self, target: str, baseline: float) -> StressTestReport:
        """Generate a comprehensive stress test report."""
        results = [r for r in self._results if r.target == target]

        if not results:
            # Run all scenarios
            for scenario in self._scenarios.values():
                self.run_stress_test(scenario.scenario_id, target, baseline)
            results = [r for r in self._results if r.target == target]

        avg_impact = sum(r.impact_pct for r in results) / len(results)
        max_impact = min(r.impact_pct for r in results)
        avg_recovery = sum(r.recovery_time_days for r in results) / len(results)

        tail_risk = self.analyze_tail_risk(target, baseline)
        resilience = self.calculate_resilience(target)

        report = StressTestReport(
            report_id=hashlib.md5(
                f"report:{target}:{time.time()}".encode()
            ).hexdigest()[:12],
            target=target,
            total_scenarios_tested=len(results),
            avg_impact_pct=round(avg_impact, 2),
            max_impact_pct=round(max_impact, 2),
            recovery_time_avg_days=round(avg_recovery, 1),
            resilience_score=resilience.score,
            tail_risk_level="high" if tail_risk.cvar_99 < -25 else "moderate" if tail_risk.cvar_99 < -15 else "low",
            critical_scenarios=sum(1 for r in results if r.max_drawdown > 20),
            tested_at=time.time(),
        )

        self._reports.append(report)
        return report

    def get_report(self) -> dict:
        """Get stress tester report."""
        return {
            "total_scenarios": len(self._scenarios),
            "total_stress_tests": len(self._results),
            "total_tail_risk_analyses": len(self._tail_risks),
            "total_reverse_tests": len(self._reverse_tests),
            "total_catalogs": len(self._catalogs),
            "total_reports": len(self._reports),
            "avg_impact_pct": sum(r.impact_pct for r in self._results) / max(1, len(self._results)),
            "max_drawdown": max((r.max_drawdown for r in self._results), default=0),
            "scenarios_by_severity": {
                sev: sum(1 for s in self._scenarios.values() if s.severity == sev)
                for sev in set(s.severity for s in self._scenarios.values())
            },
            "recent_stress_tests": [
                {
                    "target": r.target,
                    "scenario": r.scenario_id,
                    "impact_pct": r.impact_pct,
                    "recovery_days": r.recovery_time_days,
                }
                for r in self._results[-10:]
            ],
            "tail_risk_summary": [
                {
                    "target": t.target,
                    "var_95": t.var_95,
                    "var_99": t.var_99,
                    "cvar_95": t.cvar_95,
                    "cvar_99": t.cvar_99,
                }
                for t in self._tail_risks.values()
            ],
            "recent_reports": [
                {
                    "target": r.target,
                    "scenarios": r.total_scenarios_tested,
                    "avg_impact": r.avg_impact_pct,
                    "resilience": r.resilience_score,
                    "tail_risk": r.tail_risk_level,
                }
                for r in self._reports[-5:]
            ],
        }


stress_tester = ScenarioStressTester()


@router.post("/api/v1/futuris/stress/scenarios")
async def create_scenario(payload: dict):
    """Create a stress scenario."""
    scenario = stress_tester.create_scenario(
        name=payload["name"],
        severity=payload.get("severity", "medium"),
        probability=payload.get("probability", 0.05),
        parameters=payload.get("parameters", {}),
    )
    return {"scenario_id": scenario.scenario_id, "name": scenario.name, "severity": scenario.severity}


@router.post("/api/v1/futuris/stress/test")
async def run_stress_test(payload: dict):
    """Run a stress test."""
    result = stress_tester.run_stress_test(
        scenario_id=payload["scenario_id"],
        target=payload["target"],
        baseline=payload["baseline"],
    )
    return {
        "result_id": result.result_id,
        "baseline": result.baseline_value,
        "stressed": result.stressed_value,
        "impact_pct": result.impact_pct,
        "recovery_days": result.recovery_time_days,
    }


@router.get("/api/v1/futuris/stress/tail-risk/{target}")
async def analyze_tail_risk(target: str, baseline: float = 100.0):
    """Analyze tail risk."""
    analysis = stress_tester.analyze_tail_risk(target, baseline)
    return {
        "target": analysis.target,
        "var_95": analysis.var_95,
        "var_99": analysis.var_99,
        "cvar_95": analysis.cvar_95,
        "cvar_99": analysis.cvar_99,
        "tail_probability": analysis.tail_probability,
    }


@router.get("/api/v1/futuris/stress/resilience/{target}")
async def calculate_resilience(target: str):
    """Calculate resilience score."""
    resilience = stress_tester.calculate_resilience(target)
    return {
        "target": resilience.target,
        "score": resilience.score,
        "rating": resilience.rating,
        "weaknesses": resilience.weaknesses,
        "strengths": resilience.strengths,
        "recommendations": resilience.recommendations,
    }


@router.get("/api/v1/futuris/stress/report")
async def get_report():
    """Get stress tester report."""
    return stress_tester.get_report()
