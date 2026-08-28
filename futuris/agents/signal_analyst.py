"""SignalAnalyst: Analyzes telemetry signal anomalies and produces structured assessments."""

from uuid import UUID

import pandas as pd

from futuris.agents.protocol import AgentMessage
from futuris.features.normalize import DataQualityReport, TrustedSignalSet
from futuris.infra.llm import LLMAdapter, llm_adapter


class SignalAnalyst:
    """Agent that assesses telemetry signal variations and flags meaningful anomalies."""

    def __init__(self, llm: LLMAdapter | None = None) -> None:
        self.llm = llm or llm_adapter
        self.name = "SignalAnalyst"

    def _deterministic_fallback(
        self,
        series: pd.Series,
        quality_report: DataQualityReport,
    ) -> tuple[dict, str, float]:
        """Compute rule-based z-score anomaly analysis without LLM dependency."""
        mean_val = float(series.mean())
        std_val = float(series.std()) if len(series) > 1 else 1.0
        latest_val = float(series.iloc[-1]) if len(series) > 0 else mean_val
        z_score = float((latest_val - mean_val) / (std_val + 1e-5))

        is_anomaly = abs(z_score) > 2.5
        severity = "high" if abs(z_score) > 3.5 else ("medium" if is_anomaly else "low")

        hypotheses = []
        if z_score > 2.5:
            hypotheses.append("Sudden traffic influx or promotional load spike")
            hypotheses.append("Downstream retry storm inflating requests")
        elif z_score < -2.5:
            hypotheses.append("Upstream gateway failure or network partition")
        else:
            hypotheses.append("Normal operational variance within standard regime")

        result = {
            "is_meaningful": is_anomaly,
            "z_score": round(z_score, 2),
            "severity": severity,
            "hypotheses": hypotheses,
            "signal_coverage": quality_report.coverage_percentage,
        }

        cov = quality_report.coverage_percentage
        narrative = (
            f"Signal analysis for {series.name or 'target'}: latest value {latest_val:.1f} "
            f"(z={z_score:.2f}, severity={severity}). Coverage is {cov}%. "
            f"Primary hypothesis: {hypotheses[0]}."
        )
        confidence = 0.90 if quality_report.coverage_percentage >= 95.0 else 0.65
        return result, narrative, confidence

    async def analyze_signal(
        self,
        signal_set: TrustedSignalSet,
        evidence_id: UUID | None = None,
        task_context: dict | None = None,
    ) -> AgentMessage:
        """Evaluate normalized signal set and produce structured AgentMessage."""
        df = signal_set.to_dataframe()
        series = df["value"]
        series.name = signal_set.series_id
        report = signal_set.quality_report

        def fallback_wrapper() -> str:
            res, narr, _ = self._deterministic_fallback(series, report)
            return narr

        if self.llm.is_available:
            prompt = (
                f"Analyze telemetry series {series.name}: mean={series.mean():.2f}, "
                f"latest={series.iloc[-1]:.2f}, coverage={report.coverage_percentage}%. "
                f"Provide concise anomaly judgment and root causes."
            )
            narrative = await self.llm.generate(
                prompt=prompt,
                system_prompt="You are a principal SRE and telemetry signal analyst.",
                fallback_fn=fallback_wrapper,
            )
            result_dict, _, conf = self._deterministic_fallback(series, report)
        else:
            result_dict, narrative, conf = self._deterministic_fallback(series, report)

        return AgentMessage(
            agent_name=self.name,
            task_context=task_context or {},
            evidence_refs=[evidence_id] if evidence_id else [],
            confidence=conf,
            result=result_dict,
            narrative=narrative,
        )
