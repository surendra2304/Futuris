"""Bounded, best-effort access to the IntelX research peer.

Two call paths already enriched forecasts with external research -- the universe
forecasting pipeline and the market router -- and each had its own copy of the
same policy: build an :class:`IntelXContextInjector`, give it a short timeout,
and carry on with no evidence when the peer is unreachable.  The FRIDAY
delegation path had none, so a delegated forecast was computed in isolation
while the mesh recorded zero calls to the research peer.

This module is the single place that policy lives, so every producer enriches
identically (and degrades identically) when the research agent is slow or down.
"""

from __future__ import annotations

import sys
from datetime import datetime

from futuris.connectors.intelx_context import IntelXContextInjector, IntelXResearchReport
from futuris.infra.config import settings
from futuris.infra.logging import get_logger

logger = get_logger("futuris.infra.research_context")

DEFAULT_TIMEOUT_SECONDS = 1.5
FINDING_PREFIX = "intelx:"
MAX_FINDING_CHARS = 45


async def fetch_research_reports(
    target: str,
    as_of: datetime,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[IntelXResearchReport]:
    """Fetch live research reports for ``target``, tolerating an absent peer.

    The timeout is deliberately well below any request budget: research is
    enrichment, never a dependency, so a slow research agent must not hold up a
    forecast (see the ``slow-research-peer`` mesh scenario).
    """
    if "pytest" in sys.modules:
        # Tests must not depend on a live peer; callers inject fixtures instead.
        return []
    try:
        injector = IntelXContextInjector(
            base_url=settings.INTELX_URL,
            api_key=settings.INTELX_API_KEY,
            timeout_seconds=timeout_seconds,
        )
        return await injector.fetch_recent_research(target, as_of=as_of)
    except Exception as exc:  # noqa: BLE001 - enrichment never fails the request
        logger.debug("research_context_unavailable", target=target, error=type(exc).__name__)
        return []


def finding_labels(reports: list[IntelXResearchReport]) -> list[str]:
    """Driver labels for the reports, matching the universe-forecast convention."""
    return [
        f"{FINDING_PREFIX}{report.summary[:MAX_FINDING_CHARS]}"
        for report in reports
        if report.summary
    ]
