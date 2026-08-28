"""CalibrationAnalyst: Summarizes backtest calibration metrics and governance actions."""

from futuris.agents.protocol import AgentMessage
from futuris.evaluation.backtest import BacktestReport
from futuris.evaluation.drift import DriftStatus
from futuris.infra.llm import LLMAdapter, llm_adapter


class CalibrationAnalyst:
    """Agent that synthesizes backtest reports and suggests calibration actions."""

    def __init__(self, llm: LLMAdapter | None = None) -> None:
        self.llm = llm or llm_adapter
        self.name = "CalibrationAnalyst"
        self.history: list[AgentMessage] = []

    def _deterministic_fallback(
        self,
        report: BacktestReport,
        drift_status: DriftStatus | None = None,
    ) -> tuple[dict, str, float]:
        """Produce deterministic rule-based briefing without LLM dependency."""
        primary_horizon = (
            list(report.metrics_by_horizon.values())[0] if report.metrics_by_horizon else None
        )
        mae_val = primary_horizon.mae if primary_horizon else 0.0
        cov_val = primary_horizon.interval_coverage if primary_horizon else 0.0
        ece_val = primary_horizon.calibration_error if primary_horizon else 0.0

        recommended_actions = []
        if drift_status and drift_status.is_degraded:
            recommended_actions.append(f"Demote degraded model: {drift_status.model_version}")
            recommended_actions.append("Trigger automated hyperparameter retrain")
        elif report.total_forecasts < 20:
            recommended_actions.append("Insufficient sample size — continue pooling rates")
        else:
            recommended_actions.append("Maintain active model routing configuration")

        if cov_val < 0.80:
            recommended_actions.append("Widen prediction intervals (nominal coverage < 80%)")

        result = {
            "target": report.target,
            "total_forecasts": report.total_forecasts,
            "mae": mae_val,
            "coverage": cov_val,
            "ece": ece_val,
            "drift_detected": drift_status.is_degraded if drift_status else False,
            "recommended_actions": recommended_actions,
        }

        narrative = (
            f"Calibration Briefing for {report.target}: Evaluated {report.total_forecasts} "
            f"runs. MAE={mae_val:.2f}, Coverage={cov_val*100:.1f}%, ECE={ece_val:.4f}. "
            f"Primary recommendation: {recommended_actions[0]}."
        )

        n = report.total_forecasts
        confidence = 0.95 if n >= 50 else (0.75 if n >= 10 else 0.50)
        return result, narrative, confidence

    async def analyze_calibration(
        self,
        report: BacktestReport,
        drift_status: DriftStatus | None = None,
        task_context: dict | None = None,
    ) -> AgentMessage:
        """Generate a structured briefing and persist in local analyst history."""

        def fallback_wrapper() -> str:
            _, narr, _ = self._deterministic_fallback(report, drift_status)
            return narr

        if self.llm.is_available:
            first_model = (
                list(report.metrics_by_model.values())[0] if report.metrics_by_model else {}
            )
            mae_score = first_model.get("mae", 0.0)
            prompt = (
                f"Draft an executive briefing for backtest on {report.target}: "
                f"Forecasts={report.total_forecasts}, MAE={mae_score:.2f}. "
                f"Provide concise recommendation for model governance."
            )
            narrative = await self.llm.generate(
                prompt=prompt,
                system_prompt="You are a senior statistical forecasting governance officer.",
                fallback_fn=fallback_wrapper,
            )
            result_dict, _, conf = self._deterministic_fallback(report, drift_status)
        else:
            result_dict, narrative, conf = self._deterministic_fallback(report, drift_status)

        msg = AgentMessage(
            agent_name=self.name,
            task_context=task_context or {},
            evidence_refs=[],
            confidence=conf,
            result=result_dict,
            narrative=narrative,
        )
        self.history.append(msg)
        return msg
