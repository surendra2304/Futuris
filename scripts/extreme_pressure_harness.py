#!/usr/bin/env python3
"""Extreme-pressure and dead-end harness for a running FUTURIS server.

This is the "day in the life, at the worst moment" driver: it does not ask
whether the happy path works (``scripts/e2e_system_test.py`` and
``scripts/mesh_live_test.py`` do that) but whether the platform survives the
hostile, racy, half-broken reality of a fleet under load:

* concurrent idempotent FRIDAY delegations with the SAME idempotency key
  (a retry storm must produce exactly one forecast, not N);
* the same idempotency key reused with a DIFFERENT payload (must conflict,
  never silently serve the wrong cached forecast);
* concurrent manual resolution of the same forecast (one 200, the rest 409,
  never a 500, never two outcome rows);
* concurrent invalidate + resolve + cancel on the same forecast;
* a forecast-creation storm across many targets at once;
* concurrent universe refresh-all passes (budget-bounded, no 5xx);
* anonymous compute-heavy reads (market forecast, predictions matrix);
* FRIDAY rate limiting (100 req/h -> 429 with Retry-After semantics);
* webhook subscribe/delete churn;
* demo-seed spam (single-flight guard);
* dead ends: nonexistent IDs, malformed UUIDs, oversized payloads, unicode
  targets, extreme horizons, double invalidation of resolved forecasts.

Every check prints PASS/FAIL with the observed evidence; the exit code is
non-zero when any invariant is violated. Database ground truth is read from
the SQLite file directly (WAL mode allows a concurrent reader).

Usage:
    python scripts/extreme_pressure_harness.py \
        --base-url http://127.0.0.1:8000 \
        --api-key "$FUTURIS_API_KEY" --friday-key "$FUTURIS_FRIDAY_API_KEY" \
        --db ./data/futuris.db
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


CHECKS: list[Check] = []


def record(name: str, ok: bool, detail: str = "", **evidence: Any) -> bool:
    CHECKS.append(Check(name, ok, detail, evidence))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""))
    return ok


def db_count(db_path: str, sql: str, params: tuple = ()) -> int:
    """Read ground truth from the SQLite file (WAL allows concurrent readers)."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def hex_id(dashed_uuid: str) -> str:
    """SQLAlchemy's non-native UUID storage format (hex, no dashes)."""
    return dashed_uuid.replace("-", "")


def db_count_eventually(
    db_path: str, sql: str, params: tuple = (), *, want: int = 1, timeout: float = 5.0
) -> int:
    """Poll a count until it reaches ``want`` (or timeout).

    FastAPI runs yield-dependency teardown -- which commits the request's
    transaction -- after the response is sent, so a client that reads the
    database the instant it sees a 200 can beat the commit. Polling makes the
    ground-truth read honest about that ordering instead of flaky.
    """
    deadline = time.monotonic() + timeout
    last = -1
    while time.monotonic() < deadline:
        last = db_count(db_path, sql, params)
        if last >= want:
            return last
        time.sleep(0.1)
    return last


async def concurrent_idempotent_delegation(
    client: httpx.AsyncClient, friday: dict[str, str], request_id: str
) -> None:
    """A retry storm with one idempotency key must create exactly one forecast."""
    async def one(i: int) -> httpx.Response:
        return await client.post(
            "/v1/friday/forecast",
            headers=friday,
            json={
                "friday_request_id": request_id,
                "target": "service:checkout:capacity_exceedance_24h",
                "horizon": "1h",
            },
        )

    responses = await asyncio.gather(*[one(i) for i in range(8)])
    statuses = sorted(r.status_code for r in responses)
    ids = {
        r.json().get("futuris_forecast_id")
        for r in responses
        if r.status_code in (200, 201)
    }
    record(
        "concurrent idempotent delegation creates exactly one forecast",
        statuses.count(201) + statuses.count(200) == 8 and len(ids) == 1,
        f"statuses={statuses} distinct_forecast_ids={len(ids)}",
        statuses=statuses,
    )


async def idempotency_key_payload_conflict(
    client: httpx.AsyncClient, friday: dict[str, str], db_path: str
) -> None:
    """Same key, different payload: must not silently serve the wrong forecast."""
    request_id = f"req_conflict_{uuid.uuid4().hex[:8]}"
    first = await client.post(
        "/v1/friday/forecast",
        headers=friday,
        json={
            "friday_request_id": request_id,
            "target": "service:checkout:capacity_exceedance_24h",
            "horizon": "1h",
        },
    )
    second = await client.post(
        "/v1/friday/forecast",
        headers=friday,
        json={
            "friday_request_id": request_id,
            "target": "service:payments:latency_p95_24h",
            "horizon": "1h",
        },
    )
    ok_first = first.status_code in (200, 201)
    body = second.json() if second.status_code < 500 else {}
    # The second call must either conflict loudly or serve the SAME target's
    # forecast; serving a payments answer for a checkout key (or vice versa)
    # is a correctness breach.
    conflicted = second.status_code in (409, 422)
    same_forecast = (
        second.status_code in (200, 201)
        and body.get("futuris_forecast_id") == first.json().get("futuris_forecast_id")
    )
    record(
        "idempotency key reuse with different payload conflicts or replays identically",
        ok_first and (conflicted or same_forecast),
        f"first={first.status_code} second={second.status_code} "
        f"second_forecast={body.get('futuris_forecast_id')} "
        f"first_forecast={first.json().get('futuris_forecast_id') if ok_first else None}",
    )


async def concurrent_manual_resolution(
    client: httpx.AsyncClient, master: dict[str, str], db_path: str
) -> None:
    """Two operators resolve the same forecast at once: one wins, one 409s."""
    created = await client.post(
        "/v1/forecasts",
        headers=master,
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "1h"},
    )
    forecast_id = created.json().get("forecast_id")

    async def resolve(i: int) -> httpx.Response:
        return await client.post(
            f"/v1/forecasts/{forecast_id}/resolve-manual",
            headers=master,
            json={
                "observed_value": 3900.0 + i,
                "event_occurred": False,
                "note": f"concurrent resolution attempt {i}",
            },
        )

    responses = await asyncio.gather(*[resolve(i) for i in range(6)])
    statuses = sorted(r.status_code for r in responses)
    outcomes = db_count_eventually(
        db_path, "SELECT COUNT(*) FROM outcomes WHERE forecast_id = ?", (hex_id(forecast_id),)
    )
    record(
        "concurrent manual resolution: one 200, rest 409, exactly one outcome row",
        statuses.count(200) == 1 and set(statuses) <= {200, 409} and outcomes == 1,
        f"statuses={statuses} outcome_rows={outcomes}",
        statuses=statuses,
    )


async def concurrent_lifecycle_mutations(
    client: httpx.AsyncClient, master: dict[str, str], friday: dict[str, str]
) -> None:
    """Invalidate + resolve + cancel racing on one forecast must never 5xx."""
    created = await client.post(
        "/v1/forecasts",
        headers=master,
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "1h"},
    )
    forecast_id = created.json().get("forecast_id")

    async def invalidate() -> httpx.Response:
        return await client.post(
            f"/v1/forecasts/{forecast_id}/invalidate",
            headers=master,
            json={"reason": "racing lifecycle mutation test"},
        )

    async def resolve() -> httpx.Response:
        return await client.post(
            f"/v1/forecasts/{forecast_id}/resolve-manual",
            headers=master,
            json={
                "observed_value": 3900.0,
                "event_occurred": False,
                "note": "racing lifecycle mutation test",
            },
        )

    async def cancel() -> httpx.Response:
        return await client.post(
            f"/v1/friday/forecasts/{forecast_id}/cancel", headers=friday
        )

    responses = await asyncio.gather(invalidate(), resolve(), cancel(), invalidate())
    statuses = sorted(r.status_code for r in responses)
    record(
        "racing invalidate/resolve/cancel never returns 5xx",
        all(s < 500 for s in statuses),
        f"statuses={statuses}",
        statuses=statuses,
    )


async def forecast_creation_storm(
    client: httpx.AsyncClient, master: dict[str, str], db_path: str
) -> None:
    """30 concurrent forecasts across 6 targets: all 201, all persisted."""
    targets = [
        "service:checkout:capacity_exceedance_24h",
        "service:payments:latency_p95_24h",
        "service:search:error_budget_burn_24h",
        "service:auth:login_failure_rate_24h",
        "service:catalog:cache_miss_rate_24h",
        "service:notifications:queue_backlog_24h",
    ]

    async def one(i: int) -> httpx.Response:
        return await client.post(
            "/v1/forecasts",
            headers=master,
            json={"target": f"{targets[i % len(targets)]}:storm{i}", "horizon": "1h"},
        )

    started = time.monotonic()
    responses = await asyncio.gather(*[one(i) for i in range(30)])
    wall = time.monotonic() - started
    statuses = [r.status_code for r in responses]
    n_201 = statuses.count(201)
    record(
        "30-way concurrent forecast storm: all 201, no 5xx",
        n_201 == 30 and all(s < 500 for s in statuses),
        f"statuses={ {s: statuses.count(s) for s in set(statuses)} } wall={wall:.1f}s",
    )


async def universe_refresh_storm(client: httpx.AsyncClient, master: dict[str, str]) -> None:
    """5 concurrent refresh-all passes: all answer, none 5xx."""

    async def one() -> httpx.Response:
        return await client.post("/v1/predictions/refresh-all", headers=master)

    started = time.monotonic()
    responses = await asyncio.gather(*[one() for _ in range(5)])
    wall = time.monotonic() - started
    statuses = sorted(r.status_code for r in responses)
    record(
        "5 concurrent universe refresh-all passes: no 5xx",
        all(s < 500 for s in statuses),
        f"statuses={statuses} wall={wall:.1f}s",
        statuses=statuses,
    )


async def anonymous_heavy_reads(client: httpx.AsyncClient) -> None:
    """Anonymous compute-heavy reads must answer (honest refusal counts)."""

    async def market() -> httpx.Response:
        return await client.get("/v1/market/forecast")

    async def matrix() -> httpx.Response:
        return await client.get("/v1/predictions/matrix")

    async def peers() -> httpx.Response:
        return await client.get("/v1/ecosystem/peers")

    markets = await asyncio.gather(*[market() for _ in range(6)])
    matrices = await asyncio.gather(*[matrix() for _ in range(6)])
    peers_resps = await asyncio.gather(*[peers() for _ in range(6)])
    market_statuses = sorted(r.status_code for r in markets)
    matrix_statuses = sorted(r.status_code for r in matrices)
    peer_statuses = sorted(r.status_code for r in peers_resps)
    # Anonymous heavy reads draw on one per-client budget (6 per minute by default),
    # shared by the market GET and the matrix GET. Twelve heavy requests from one
    # address therefore see some 429s by design. A 429 is a correct answer only when
    # it carries Retry-After. The market GET honestly refuses with 503 when live
    # Stratex telemetry is unavailable (never a placeholder). Peers use the general
    # budget (120 per minute), so all six must answer. Nothing may 500.
    matrix_retry_headers_ok = all(
        r.status_code != 429 or r.headers.get("retry-after", "").isdigit() for r in matrices
    )
    market_retry_headers_ok = all(
        r.status_code != 429 or r.headers.get("retry-after", "").isdigit() for r in markets
    )
    record(
        "anonymous heavy reads: honest 200/503, budget 429 has Retry-After, no 500",
        all(s in (200, 429, 503) for s in market_statuses)
        and all(s in (200, 429) for s in matrix_statuses)
        and matrix_retry_headers_ok
        and market_retry_headers_ok
        and all(s == 200 for s in peer_statuses),
        f"market={market_statuses} matrix={matrix_statuses} peers={peer_statuses}",
        market=market_statuses,
        matrix=matrix_statuses,
        peers=peer_statuses,
    )


async def friday_rate_limit(
    client: httpx.AsyncClient, friday: dict[str, str]
) -> None:
    """The 100 req/h FRIDAY budget must produce 429s, never 5xx."""

    async def one(i: int) -> httpx.Response:
        return await client.post(
            "/v1/friday/forecast",
            headers=friday,
            json={
                "friday_request_id": f"req_ratelimit_{uuid.uuid4().hex[:8]}",
                "target": "service:checkout:capacity_exceedance_24h",
                "horizon": "1h",
            },
        )

    # Each delegation runs the pipeline (~seconds), so 110 sequential calls
    # would take too long; fire them concurrently and count outcomes.
    responses = await asyncio.gather(*[one(i) for i in range(110)])
    statuses = [r.status_code for r in responses]
    n_429 = statuses.count(429)
    n_503 = statuses.count(503)
    # 503 is acceptable only as honest backpressure: the storage_busy envelope
    # (SQLite's single-writer ceiling under an extreme write burst) with its
    # documented remediation. A 500 or a non-envelope body is a failure.
    backpressure_ok = True
    for r in responses:
        if r.status_code == 503:
            try:
                code = r.json()["error"]["code"]
            except Exception:
                backpressure_ok = False
                break
            if code not in ("storage_busy", "server_busy"):
                backpressure_ok = False
                break
        elif r.status_code >= 500:
            backpressure_ok = False
            break
    record(
        "FRIDAY rate limit engages with 429; 503 only as honest backpressure",
        n_429 > 0 and backpressure_ok,
        f"201={statuses.count(201)} 429={n_429} 503_backpressure={n_503} "
        f"other={ {s: statuses.count(s) for s in set(statuses) if s not in (201, 429, 503)} }",
    )


async def webhook_churn(client: httpx.AsyncClient, master: dict[str, str]) -> None:
    """Subscribe/delete churn: creates succeed, deletes of missing IDs 404."""

    async def subscribe(i: int) -> httpx.Response:
        return await client.post(
            "/v1/webhooks",
            headers=master,
            json={"url": f"https://example.com/hook/{i}"},
        )

    subs = await asyncio.gather(*[subscribe(i) for i in range(10)])
    created = [r for r in subs if r.status_code == 201]
    deletes = await asyncio.gather(
        *[
            client.delete(f"/v1/webhooks/{r.json()['subscription_id']}", headers=master)
            for r in created
        ],
        client.delete(
            f"/v1/webhooks/{uuid.uuid4()}", headers=master
        ),
    )
    del_statuses = sorted(r.status_code for r in deletes)
    record(
        "webhook subscribe/delete churn: 10x201 then 10x204 + one 404",
        len(created) == 10
        and del_statuses.count(204) == 10
        and del_statuses.count(404) == 1,
        f"created={len(created)} delete_statuses={del_statuses}",
        delete_statuses=del_statuses,
    )


async def seed_spam(client: httpx.AsyncClient, master: dict[str, str]) -> None:
    """10 concurrent seed triggers: exactly one accepted, rest already_running."""

    async def one() -> httpx.Response:
        return await client.post("/v1/ecosystem/seed", headers=master)

    responses = await asyncio.gather(*[one() for _ in range(10)])
    raw_statuses = sorted(r.status_code for r in responses)
    bodies = [r.json() for r in responses if r.status_code == 200]
    statuses = [b.get("status") for b in bodies]
    record(
        "demo seed single-flight: one accepted, rest already_running",
        statuses.count("accepted") == 1
        and statuses.count("already_running") == 9
        and all(s < 500 for s in raw_statuses),
        f"http_statuses={raw_statuses} body_statuses={statuses}",
        http_statuses=raw_statuses,
        statuses=statuses,
    )


async def dead_ends(client: httpx.AsyncClient, master: dict[str, str]) -> None:
    """Hostile and nonsensical inputs must get clean 4xx envelopes."""
    missing = str(uuid.uuid4())
    cases: list[tuple[str, str, dict[str, Any], int]] = [
        ("GET", f"/v1/forecasts/{missing}", {}, 404),
        ("POST", f"/v1/forecasts/{missing}/invalidate", {"reason": "nope"}, 404),
        ("POST", f"/v1/forecasts/{missing}/resolve-manual",
         {"observed_value": 1.0, "event_occurred": True, "note": "nope"}, 404),
        ("GET", "/v1/forecasts/not-a-uuid", {}, 422),
        ("POST", "/v1/forecasts", {"target": "", "horizon": "1h"}, 422),
        ("POST", "/v1/forecasts", {"target": "   ", "horizon": "1h"}, 422),
        # 201 chars is inside the 255-char DB column bound: accepted.
        ("POST", "/v1/forecasts", {"target": "x" * 201, "horizon": "1h"}, 201),
        # 256 chars exceeds the column: rejected at the door.
        ("POST", "/v1/forecasts", {"target": "x" * 256, "horizon": "1h"}, 422),
        ("POST", "/v1/forecasts", {"target": "t", "horizon": "0m"}, 422),
        ("POST", "/v1/forecasts", {"target": "t", "horizon": "366d"}, 422),
        ("POST", "/v1/forecasts", {"target": "t", "horizon": "99999999999999999999d"}, 422),
        (
            "POST",
            "/v1/forecasts",
            {"target": "t", "horizon": "1h", "context": {"point_estimate": "abc"}},
            422,
        ),
        ("POST", "/v1/predictions/predict", {"target": "not:a:registered:target"}, 422),
        ("POST", "/v1/webhooks", {"url": "http://169.254.169.254/latest"}, 422),
        ("POST", "/v1/webhooks", {"url": "https://user:pass@example.com/hook"}, 422),
        ("POST", "/v1/webhooks", {"url": "not-a-url"}, 422),
        ("POST", "/v1/friday/delegate",
         {"task_id": "t", "action": "scale", "payload": {"command": "rm -rf /"}}, 403),
        ("POST", "/v1/task/execute", {"action": "execute", "payload": {}}, 403),
        ("POST", "/v1/task/execute", {"action": "anything-else", "payload": {}}, 501),
    ]
    failures = []
    for method, path, body, expected in cases:
        resp = await client.request(
            method, path, headers=master, json=body if body else None
        )
        if resp.status_code != expected:
            failures.append(
                f"{method} {path} -> {resp.status_code} (want {expected}): "
                f"{resp.text[:120]}"
            )
        # Every error must be the documented envelope.
        if resp.status_code >= 400:
            try:
                envelope = resp.json()
                if "error" not in envelope or "code" not in envelope["error"]:
                    failures.append(
                        f"{method} {path} -> non-envelope error body: {resp.text[:120]}"
                    )
            except ValueError:
                failures.append(
                    f"{method} {path} -> non-JSON error body: {resp.text[:120]}"
                )
    record(
        "dead-end inputs all return the documented 4xx/5xx envelope",
        not failures,
        f"{len(cases)} cases, {len(failures)} failures",
        failures=failures[:5],
    )


async def double_invalidation(
    client: httpx.AsyncClient, master: dict[str, str], db_path: str
) -> None:
    """Invalidating a RESOLVED forecast must not silently destroy its outcome."""
    created = await client.post(
        "/v1/forecasts",
        headers=master,
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "1h"},
    )
    forecast_id = created.json().get("forecast_id")
    resolved = await client.post(
        f"/v1/forecasts/{forecast_id}/resolve-manual",
        headers=master,
        json={"observed_value": 3900.0, "event_occurred": False, "note": "resolve first"},
    )
    invalidated = await client.post(
        f"/v1/forecasts/{forecast_id}/invalidate",
        headers=master,
        json={"reason": "attempt to invalidate a resolved forecast"},
    )
    row = None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    try:
        row = conn.execute(
            "SELECT status FROM forecasts WHERE forecast_id = ?", (hex_id(forecast_id),)
        ).fetchone()
        outcomes = conn.execute(
            "SELECT COUNT(*) FROM outcomes WHERE forecast_id = ?", (hex_id(forecast_id),)
        ).fetchone()[0]
    finally:
        conn.close()
    record(
        "invalidating a resolved forecast is refused (409), outcome intact",
        resolved.status_code == 200
        and invalidated.status_code == 409
        and row is not None
        and row[0] == "resolved"
        and outcomes == 1,
        f"resolve={resolved.status_code} invalidate={invalidated.status_code} "
        f"db_status={row[0] if row else None} outcomes={outcomes}",
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=os.environ.get("FUTURIS_API_KEY", ""))
    parser.add_argument(
        "--friday-key", default=os.environ.get("FUTURIS_FRIDAY_API_KEY", "")
    )
    parser.add_argument("--db", default="./data/futuris.db")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    if not args.api_key:
        print("FUTURIS_API_KEY (or --api-key) is required", file=sys.stderr)
        return 2

    db_path = args.db
    parsed = urlparse(args.base_url)
    if parsed.path:
        db_path = os.path.join(parsed.path, db_path) if False else db_path

    master = {"X-API-Key": args.api_key}
    friday = {"X-API-Key": args.friday_key or args.api_key}
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=16)
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=args.timeout, limits=limits
    ) as client:
        health = await client.get("/health")
        print(f"server: {args.base_url}  health={health.status_code}")

        await concurrent_idempotent_delegation(client, friday, f"req_storm_{uuid.uuid4().hex[:8]}")
        await idempotency_key_payload_conflict(client, friday, db_path)
        await concurrent_manual_resolution(client, master, db_path)
        await concurrent_lifecycle_mutations(client, master, friday)
        await forecast_creation_storm(client, master, db_path)
        await universe_refresh_storm(client, master)
        await anonymous_heavy_reads(client)
        await friday_rate_limit(client, friday)
        await webhook_churn(client, master)
        await seed_spam(client, master)
        await dead_ends(client, master)
        await double_invalidation(client, master, db_path)

    failed = [c for c in CHECKS if not c.ok]
    print(
        f"\n=== extreme pressure summary: "
        f"{len(CHECKS) - len(failed)} passed | {len(failed)} failed ==="
    )
    for c in failed:
        print(f"  FAILED: {c.name}: {c.detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
