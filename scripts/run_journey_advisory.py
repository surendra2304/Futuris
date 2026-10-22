"""Futuris journey hop: consume the IntelX signal, publish an advisory forecast.

Uses Futuris's real durable cursor (persist + ack inbox), runs the real
ForecastingPipeline (the production scheduler path) on the consumed
signal's target, and publishes the advisory through the signed Memora
mesh under the SAME correlation ID as the IntelX signal, so one journey
stays traceable end to end.

Usage:
    python scripts/run_journey_advisory.py --correlation-id corrJ
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from futuris.core.pipeline import ForecastingPipeline
from futuris.integrations.memora_client import memora_client
from futuris.integrations.memora_event_consumer import consume_memora_events_once
from futuris.integrations.memora_forecast_publisher import publish_forecast_advisory

_CONFIDENCE_FLOOR = {"HIGH": 0.9, "MEDIUM": 0.75, "LOW": 0.5}


async def _find_journey_signal(correlation_id: str, pages: int = 10) -> dict | None:
    """Read-only replay scan of Futuris's visible feed for the journey's signal.

    Uses the server-side event_type filter so the scan is bounded by the number
    of intelx.news events, not the full feed length (the production feed carries
    hundreds of unrelated events between journey runs).
    """
    after_id = 0
    for _ in range(pages):
        page = await asyncio.to_thread(
            memora_client.poll_events, "futuris", after_id, 100, "intelx.news"
        )
        if not isinstance(page, dict) or page.get("status") != "ok":
            return None
        events = page.get("events", [])
        newest = None
        for event in events:
            if not isinstance(event, dict) or event.get("event_type") != "intelx.news":
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if str(payload.get("correlation_id", "")) == correlation_id:
                newest = event
        if newest is not None:
            return newest
        next_after_id = page.get("next_after_id", after_id)
        if not events or next_after_id <= after_id:
            return None
        after_id = next_after_id
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--correlation-id", required=True, help="Journey correlation ID published by IntelX"
    )
    args = parser.parse_args()

    # Hop 1: durable inbox — persist + ack with Futuris's independent cursor.
    handled = await consume_memora_events_once()

    # Hop 2: replay-scan for the journey's signal (read-only).
    event = await _find_journey_signal(args.correlation_id)
    if event is None:
        print(
            json.dumps(
                {
                    "status": "signal_not_found",
                    "correlation_id": args.correlation_id,
                    "inbox_handled": handled,
                }
            )
        )
        return 1
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    signal_id = str(payload.get("signal_id") or event.get("event_id") or "unknown")[:64]
    target = f"intelx:{signal_id}"

    # Hop 3: real forecast machinery — the same ForecastingPipeline the
    # production scheduler runs — on the consumed signal's target. Its output
    # is a genuinely ACTIVE forecast; the bare engine's DRAFT forecasts are
    # deliberately unpublishable and are NOT promoted by hand.
    pipeline = ForecastingPipeline()
    now = datetime.now(UTC)
    result = await pipeline.run(
        target=target,
        as_of=now,
        horizon=timedelta(hours=24),
        lookback_days=7,
    )
    forecast = result.forecast
    score = _CONFIDENCE_FLOOR.get(getattr(forecast.confidence, "value", ""), 0.5)
    forecast_status = getattr(forecast, "status", "")
    forecast_status = getattr(forecast_status, "value", str(forecast_status))

    # Hop 4: signed advisory under the SAME correlation ID.
    event_id = await publish_forecast_advisory(forecast, score, correlation_id=args.correlation_id)

    print(
        json.dumps(
            {
                "status": "advised",
                "correlation_id": args.correlation_id,
                "inbox_handled": handled,
                "consumed_event_id": event.get("event_id"),
                "forecast_id": str(forecast.forecast_id),
                "target": target,
                "forecast_status": forecast_status,
                "prediction": forecast.prediction,
                "range": [forecast.range_lower, forecast.range_upper],
                "probability": forecast.probability,
                "confidence_score": score,
                "published_event_id": event_id,
                "prediction_is_not_authorization": True,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
