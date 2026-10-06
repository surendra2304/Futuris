#!/usr/bin/env python3
"""Real-world pressure harness for a running FUTURIS server.

The in-process integration tests use an ASGI transport; this harness talks to a
real uvicorn over a real socket, which is where concurrency, keep-alive, peer
latency and the event loop's behaviour under load actually show up.

It measures three things a forecasting agent must survive:

* forecast latency under a concurrent burst (p50/p90/p95/max, status counts);
* whether the server keeps answering while forecasts are computing -- a
  liveness poller hits /v1/self/status throughout the burst, because an agent
  that cannot report its own state cannot help the rest of the mesh;
* whether degraded answers are labelled as degraded (selection_degraded +
  candidates_skipped) rather than silently served as full-quality work.

Usage:
    python scripts/pressure_harness.py --base-url http://127.0.0.1:8100 \
        --api-key "$FUTURIS_API_KEY" --requests 12 --concurrency 6

Exit code is non-zero when any request returned 5xx, when no request was ever
served, or when the liveness poller was starved beyond ``--max-stall`` seconds.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

TARGETS = [
    "service:checkout:capacity_exceedance_24h",
    "service:payments:latency_p95_24h",
    "service:search:error_budget_burn_24h",
    "service:auth:login_failure_rate_24h",
    "service:catalog:cache_miss_rate_24h",
    "service:notifications:queue_backlog_24h",
]


@dataclass
class Sample:
    status: int | None
    wall_seconds: float
    degraded: bool | None = None
    skipped: list[str] = field(default_factory=list)
    error: str | None = None


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


async def forecast_once(
    client: httpx.AsyncClient, headers: dict[str, str], target: str, horizon: str
) -> Sample:
    started = time.monotonic()
    try:
        response = await client.post(
            "/v1/forecasts", headers=headers, json={"target": target, "horizon": horizon}
        )
        wall = time.monotonic() - started
        body: dict[str, Any] = {}
        try:
            body = response.json()
        except ValueError:
            body = {}
        meta = body.get("model_metadata") if isinstance(body, dict) else None
        return Sample(
            status=response.status_code,
            wall_seconds=wall,
            degraded=(meta or {}).get("selection_degraded"),
            skipped=(meta or {}).get("candidates_skipped") or [],
        )
    except Exception as exc:  # noqa: BLE001 - the harness reports, it does not raise
        return Sample(
            status=None,
            wall_seconds=time.monotonic() - started,
            error=f"{type(exc).__name__}: {exc}",
        )


async def liveness_poller(
    client: httpx.AsyncClient, headers: dict[str, str], stop: asyncio.Event, out: list[float]
) -> None:
    while not stop.is_set():
        started = time.monotonic()
        try:
            response = await client.get("/v1/self/status", headers=headers)
            if response.status_code < 500:
                out.append(time.monotonic() - started)
        except Exception:  # noqa: BLE001 - a failed probe is itself a data point
            out.append(time.monotonic() - started)
        await asyncio.sleep(0.1)


def summarize(samples: list[Sample], liveness: list[float], wall: float) -> int:
    ok = [s for s in samples if s.status and 200 <= s.status < 300]
    failed = [s for s in samples if s.status is None or s.status >= 500]
    latencies = [s.wall_seconds for s in ok]
    degraded = [s for s in ok if s.degraded]
    unlabelled = [s for s in degraded if not s.skipped]

    print(f"\nrequests: {len(samples)}  ok: {len(ok)}  failed: {len(failed)}  wall: {wall:.2f}s")
    if latencies:
        print(
            "latency s: "
            f"p50={percentile(latencies, 0.5):.2f} "
            f"p90={percentile(latencies, 0.9):.2f} "
            f"p95={percentile(latencies, 0.95):.2f} "
            f"max={max(latencies):.2f}"
        )
    if liveness:
        print(
            f"liveness probes: {len(liveness)}  "
            f"p50={percentile(liveness, 0.5) * 1000:.1f}ms  "
            f"p95={percentile(liveness, 0.95) * 1000:.1f}ms  "
            f"max={max(liveness) * 1000:.1f}ms"
        )
    print(
        f"pressure-shedded answers: {len(degraded)} of {len(ok)} "
        f"(all labelled: {not unlabelled})"
    )
    if latencies and len(latencies) > 1:
        print(f"throughput: {len(ok) / wall:.2f} forecasts/s")

    for sample in samples:
        if sample.error:
            print(f"  transport error: {sample.error}")
    return 0 if ok and not failed else 1


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--api-key", default=os.environ.get("FUTURIS_API_KEY", ""))
    parser.add_argument("--requests", type=int, default=12)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--horizon", default="24h")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--max-stall",
        type=float,
        default=5.0,
        help="fail if the liveness poller waits longer than this (seconds)",
    )
    args = parser.parse_args()

    if not args.api_key:
        print("FUTURIS_API_KEY (or --api-key) is required", file=sys.stderr)
        return 2

    headers = {"X-API-Key": args.api_key}
    limits = httpx.Limits(max_connections=args.concurrency + 4, max_keepalive_connections=4)
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=args.timeout, limits=limits
    ) as client:
        ready = await client.get("/health")
        print(f"server: {args.base_url}  health={ready.status_code}")

        stop = asyncio.Event()
        liveness: list[float] = []
        poller = asyncio.create_task(liveness_poller(client, headers, stop, liveness))

        semaphore = asyncio.Semaphore(args.concurrency)

        async def one(index: int) -> Sample:
            target = TARGETS[index % len(TARGETS)]
            # Distinct target so a repeated run is not deduplicated as a retry.
            unique = f"{target}:probe{index}"
            async with semaphore:
                return await forecast_once(client, headers, unique, args.horizon)

        started = time.monotonic()
        samples = await asyncio.gather(*[one(i) for i in range(args.requests)])
        wall = time.monotonic() - started
        stop.set()
        await poller

    exit_code = summarize(list(samples), liveness, wall)
    if liveness and max(liveness) > args.max_stall:
        print(
            f"FAIL: the server went {max(liveness):.1f}s without answering a liveness probe",
            file=sys.stderr,
        )
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
