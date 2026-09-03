import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from futuris.agents.calibration_analyst import CalibrationAnalyst
from futuris.agents.protocol import AgentMessage, AgentResultHandler, LogResultHandler
from futuris.agents.signal_analyst import SignalAnalyst
from futuris.evaluation.backtest import BacktestReport
from futuris.features.normalize import TrustedSignalSet
from futuris.infra.logging import get_logger

logger = get_logger("futuris.agents.runner")


class AgentRunner:
    """Orchestrates SignalAnalyst and CalibrationAnalyst runs with concurrency-safe cost guardrails."""

    def __init__(
        self,
        signal_analyst: SignalAnalyst | None = None,
        calibration_analyst: CalibrationAnalyst | None = None,
        handler: AgentResultHandler | None = None,
        max_llm_calls_per_hour: int = 50,
    ) -> None:
        self.signal_analyst = signal_analyst or SignalAnalyst()
        self.calibration_analyst = calibration_analyst or CalibrationAnalyst()
        self.handler = handler or LogResultHandler()
        self.max_llm_calls_per_hour = max_llm_calls_per_hour
        self._hourly_call_timestamps: list[datetime] = []
        self._lock = asyncio.Lock()

    async def _check_and_record_cost_guardrail(self) -> bool:
        """Check if rate limit is exceeded in the trailing 1 hour window with concurrency lock."""
        async with self._lock:
            now = datetime.now(UTC)
            cutoff = now - timedelta(hours=1)
            # Purge calls older than 1 hour
            self._hourly_call_timestamps = [t for t in self._hourly_call_timestamps if t > cutoff]

            if len(self._hourly_call_timestamps) >= self.max_llm_calls_per_hour:
                logger.warning(
                    "llm_cost_guardrail_exceeded",
                    calls_in_window=len(self._hourly_call_timestamps),
                    max_allowed=self.max_llm_calls_per_hour,
                    action="falling_back_to_rules",
                )
                return False

            self._hourly_call_timestamps.append(now)
            return True

    async def run_signal_analyst(
        self,
        signal_set: TrustedSignalSet,
        evidence_id: Any | None = None,
    ) -> AgentMessage:
        """Run SignalAnalyst and forward structured message to handler."""
        allowed = await self._check_and_record_cost_guardrail()
        msg = await self.signal_analyst.analyze_signal(
            signal_set, evidence_id=evidence_id, allow_llm=allowed
        )
        await self.handler.handle_message(msg)
        return msg

    async def run_calibration_analyst(
        self,
        report: BacktestReport,
        drift_status: Any | None = None,
    ) -> AgentMessage:
        """Run CalibrationAnalyst and forward structured message to handler."""
        allowed = await self._check_and_record_cost_guardrail()
        msg = await self.calibration_analyst.analyze_calibration(
            report, drift_status=drift_status, allow_llm=allowed
        )
        await self.handler.handle_message(msg)
        return msg
