"""
Futuris/futuris/services/prediction_market_engine.py — Futuris Prediction Market Engine.

Prediction market analysis, event contract pricing, market efficiency measurement,
and crowd wisdom aggregation.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class PredictionContract:
    """A prediction market contract."""
    contract_id: str
    question: str
    description: str = ""
    outcome_yes: str = "Yes"
    outcome_no: str = "No"
    yes_price: float = 0.5
    no_price: float = 0.5
    volume: float = 0.0
    liquidity: float = 0.0
    resolved: bool = False
    resolution: str = ""


@dataclass
class MarketEfficiency:
    """Market efficiency metrics."""
    market_id: str
    contract_id: str
    brier_score: float = 0.0  # lower is better
    calibration_error: float = 0.0
    efficiency_score: float = 0.0
    samples: int = 0


@dataclass
class CrowdWisdomResult:
    """Crowd wisdom aggregation result."""
    result_id: str
    contract_id: str
    aggregated_probability: float = 0.5
    individual_predictions: list[float] = field(default_factory=list)
    diversity: float = 0.0
    confidence: float = 0.5


class PredictionMarketEngine:
    """Engine for prediction market analysis."""

    def __init__(self) -> None:
        self._contracts: dict[str, PredictionContract] = {}
        self._efficiency: dict[str, MarketEfficiency] = {}
        self._wisdom: list[CrowdWisdomResult] = []

    def create_contract(self, question: str, description: str = "",
                       outcome_yes: str = "Yes", outcome_no: str = "No",
                       yes_price: float = 0.5, volume: float = 0.0,
                       liquidity: float = 0.0) -> PredictionContract:
        """Create a prediction market contract."""
        contract_id = f"pc_{uuid.uuid4().hex[:12]}"
        contract = PredictionContract(
            contract_id=contract_id, question=question, description=description,
            outcome_yes=outcome_yes, outcome_no=outcome_no,
            yes_price=yes_price, volume=volume, liquidity=liquidity,
        )
        contract.no_price = 1.0 - yes_price
        self._contracts[contract_id] = contract
        return contract

    def update_price(self, contract_id: str, yes_price: float) -> PredictionContract:
        """Update contract price."""
        contract = self._contracts.get(contract_id)
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")
        contract.yes_price = min(1.0, max(0.0, yes_price))
        contract.no_price = 1.0 - contract.yes_price
        return contract

    def resolve_contract(self, contract_id: str, resolution: str) -> PredictionContract:
        """Resolve a contract."""
        contract = self._contracts.get(contract_id)
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")
        contract.resolved = True
        contract.resolution = resolution
        return contract

    def aggregate_crowd(self, contract_id: str,
                       predictions: list[float]) -> CrowdWisdomResult:
        """Aggregate crowd predictions using wisdom of crowds."""
        if len(predictions) < 2:
            raise HTTPException(status_code=400, detail="Need at least 2 predictions")

        result_id = f"cw_{uuid.uuid4().hex[:12]}"
        result = CrowdWisdomResult(result_id=result_id, contract_id=contract_id)
        result.individual_predictions = predictions

        # Geometric mean (better for probabilities)
        import math
        result.aggregated_probability = math.exp(
            sum(math.log(max(0.01, p)) for p in predictions) / len(predictions)
        )

        # Diversity (variance of predictions)
        mean = sum(predictions) / len(predictions)
        variance = sum((p - mean) ** 2 for p in predictions) / len(predictions)
        result.diversity = math.sqrt(variance)

        # Confidence decreases with high diversity
        result.confidence = max(0.3, 0.8 - result.diversity)

        self._wisdom.append(result)
        return result

    def calculate_efficiency(self, contract_id: str, samples: int = 0) -> MarketEfficiency:
        """Calculate market efficiency."""
        contract = self._contracts.get(contract_id)
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")

        market_id = f"mkt_{uuid.uuid4().hex[:8]}"
        efficiency = MarketEfficiency(market_id=market_id, contract_id=contract_id, samples=samples)
        efficiency.brier_score = abs(contract.yes_price - 0.5) * 0.5
        efficiency.calibration_error = abs(contract.yes_price - contract.no_price) * 0.25
        efficiency.efficiency_score = max(0.0, 1.0 - efficiency.brier_score - efficiency.calibration_error) * 100

        self._efficiency[market_id] = efficiency
        return efficiency

    def get_contracts(self, resolved: bool | None = None) -> list[dict[str, Any]]:
        """Get prediction contracts."""
        contracts = list(self._contracts.values())
        if resolved is not None:
            contracts = [c for c in contracts if c.resolved == resolved]
        return [
            {"question": c.question[:80], "yes_price": c.yes_price,
             "volume": c.volume, "resolved": c.resolved}
            for c in contracts
        ]

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_contracts": len(self._contracts),
            "total_efficiency": len(self._efficiency),
            "total_wisdom": len(self._wisdom),
            "resolved_contracts": sum(1 for c in self._contracts.values() if c.resolved),
            "engine_status": "operational",
        }


engine = PredictionMarketEngine()


@router.post("/futuris/predictionmarket/contracts")
async def create_contract(question: str, description: str = "", outcome_yes: str = "Yes",
                          outcome_no: str = "No", yes_price: float = 0.5,
                          volume: float = 0.0, liquidity: float = 0.0):
    """Create a prediction market contract."""
    contract = engine.create_contract(question, description, outcome_yes, outcome_no,
                                      yes_price, volume, liquidity)
    return {"contract_id": contract.contract_id, "yes_price": contract.yes_price}


@router.post("/futuris/predictionmarket/price/{contract_id}")
async def update_price(contract_id: str, yes_price: float):
    """Update contract price."""
    contract = engine.update_price(contract_id, yes_price)
    return {"contract_id": contract_id, "yes_price": contract.yes_price, "no_price": contract.no_price}


@router.post("/futuris/predictionmarket/resolve/{contract_id}")
async def resolve_contract(contract_id: str, resolution: str):
    """Resolve a contract."""
    contract = engine.resolve_contract(contract_id, resolution)
    return {"contract_id": contract_id, "resolution": contract.resolution}


@router.post("/futuris/predictionmarket/crowd")
async def aggregate_crowd(contract_id: str, predictions: list[float]):
    """Aggregate crowd predictions."""
    result = engine.aggregate_crowd(contract_id, predictions)
    return {
        "contract_id": contract_id,
        "aggregated_probability": result.aggregated_probability,
        "diversity": result.diversity,
        "confidence": result.confidence,
    }


@router.get("/futuris/predictionmarket/contracts")
async def get_contracts(resolved: bool | None = None):
    """Get prediction contracts."""
    return {"contracts": engine.get_contracts(resolved)}


@router.get("/futuris/predictionmarket/report")
async def predictionmarket_report():
    """Get prediction market engine report."""
    return engine.get_report()
