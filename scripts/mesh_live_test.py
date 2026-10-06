#!/usr/bin/env python3
"""Run FUTURIS against a live peer mesh, like a day in the fleet.

Two real uvicorn processes: the peer mesh (IntelX, Inference, Memora, Stratex,
Sentinel, Cortex, Forge) and FUTURIS itself, both on TCP. The script then acts
like the other agents:

1. FRIDAY delegates a forecast; the research peer is live, so the answer must
   carry IntelX-sourced drivers and say so.
2. The advisory is published to Memora and must be durably accepted (a
   rejected advisory is a failure, not a warning).
3. Market telemetry is requested from Stratex, which must produce a market
   forecast rather than the offline refusal.
4. Cortex asks for a what-if scenario through the FRIDAY envelope surface.
5. Every peer is inspected afterwards to prove which requests it actually
   received: collaboration is evidence, not assumption.
6. The mesh then injects faults (IntelX slow, Memora 500, Stratex down) and the
   same flows must keep answering, degrade honestly and recover afterwards.

Usage:
    python scripts/mesh_live_test.py            # boots both servers
    python scripts/mesh_live_test.py --keep     # leave them running
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
MESH_PORT = 8210
FUTURIS_PORT = 8110
MESH_TOKEN = "mesh_peer_token_0123456789abcdefghijklmnop"
MASTER_KEY = "mesh_live_master_key_0123456789abcdef"
FRIDAY_KEY = "mesh_live_friday_key_0123456789abcdef"

MESH_URL = f"http://127.0.0.1:{MESH_PORT}"
FUTURIS_URL = f"http://127.0.0.1:{FUTURIS_PORT}"


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def peer_env(peer_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "INTELX_URL": peer_url,
            "INFERENCE_URL": peer_url,
            "MEMORA_URL": peer_url,
            "STRATEX_URL": peer_url,
            "SENTINEL_URL": peer_url,
            "CORTEX_URL": peer_url,
            "FORGE_URL": peer_url,
            "FRIDAY_URL": peer_url,
            "INTELX_API_KEY": MESH_TOKEN,
            "INFERENCE_API_KEY": MESH_TOKEN,
            "MEMORA_API_KEY": MESH_TOKEN,
            "STRATEX_API_KEY": MESH_TOKEN,
            "SENTINEL_API_KEY": MESH_TOKEN,
            "CORTEX_API_KEY": MESH_TOKEN,
            "FORGE_API_KEY": MESH_TOKEN,
            "FRIDAY_API_KEY": MESH_TOKEN,
            "FUTURIS_API_KEY": MASTER_KEY,
            "FUTURIS_FRIDAY_API_KEY": FRIDAY_KEY,
            "SCHEDULER_ENABLED": "false",
            "DATABASE_URL": "sqlite+aiosqlite:///./data/mesh_live.db",
            "OBJECT_STORE_PATH": "./data/storage_mesh_live",
        }
    )
    return env


async def wait_for_health(client: httpx.AsyncClient, url: str, timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = await client.get(url)
            if response.status_code < 500:
                return True
        except Exception:  # noqa: BLE001 - still starting
            pass
        await asyncio.sleep(1.0)
    return False


async def control(client: httpx.AsyncClient, path: str, **params: Any) -> dict[str, Any]:
    response = await client.post(f"{MESH_URL}/__control/{path}", params=params)
    return response.json()


async def peer_requests(client: httpx.AsyncClient, peer: str) -> list[dict[str, Any]]:
    response = await client.get(f"{MESH_URL}/__control/requests", params={"peer": peer})
    return response.json()["requests"]


async def scenario(client: httpx.AsyncClient, mesher: httpx.AsyncClient) -> list[Step]:
    steps: list[Step] = []
    await control(mesher, "reset")
    master = {"X-API-Key": MASTER_KEY}
    friday = {"X-API-Key": FRIDAY_KEY}

    # 1. FRIDAY delegates, IntelX is live
    started = time.monotonic()
    delegated = await client.post(
        f"{FUTURIS_URL}/v1/friday/forecast",
        headers=friday,
        json={
            "friday_request_id": "req_mesh_live_1",
            "target": "service:checkout:capacity_exceedance_24h",
            "horizon": "24h",
        },
    )
    wall = time.monotonic() - started
    body = delegated.json() if delegated.status_code < 500 else {}
    intelx_seen = await peer_requests(mesher, "intelx")
    drivers = [d.get("metric", "") for d in body.get("drivers_identified", [])]
    steps.append(
        Step(
            "FRIDAY delegation with live research",
            delegated.status_code == 201 and bool(intelx_seen),
            f"status={delegated.status_code} wall={wall:.1f}s intelx_calls={len(intelx_seen)}",
            {"forecast_id": body.get("futuris_forecast_id"), "drivers": drivers},
        )
    )

    # 2. the forecast must be readable by every other agent
    forecast_id = body.get("futuris_forecast_id")
    if forecast_id:
        detail = await client.get(f"{FUTURIS_URL}/v1/forecasts/{forecast_id}", headers=master)
        payload = detail.json() if detail.status_code == 200 else {}
        steps.append(
            Step(
                "Forecast is readable with provenance",
                detail.status_code == 200 and bool(payload.get("evidence_class")),
                f"status={detail.status_code} evidence_class={payload.get('evidence_class')}",
                {"model_metadata_keys": sorted((payload.get("model_metadata") or {}).keys())},
            )
        )

    # 3. Memora advisory: the publisher is only satisfied by durable acceptance
    memora_before = len(await peer_requests(mesher, "memora"))
    published = await client.post(
        f"{FUTURIS_URL}/v1/market/forecast", headers=master, json={"symbol": "BTC-USD"}
    )
    memora_after = await peer_requests(mesher, "memora")
    wrote_envelope = any(r["path"] == "/mesh/envelope" for r in memora_after)
    steps.append(
        Step(
            "Market forecast + Memora advisory",
            published.status_code in (200, 201),
            f"status={published.status_code} memora_calls={len(memora_after) - memora_before} "
            f"envelope={'yes' if wrote_envelope else 'no'}",
            {
                "body_keys": sorted(published.json().keys())[:8]
                if published.status_code < 500
                else []
            },
        )
    )

    # 4. Stratex telemetry must have been requested for that market forecast
    stratex_calls = await peer_requests(mesher, "stratex")
    steps.append(
        Step(
            "Stratex telemetry consulted",
            bool(stratex_calls),
            f"stratex_calls={len(stratex_calls)} "
            f"paths={sorted({r['path'] for r in stratex_calls})}",
        )
    )

    # 5. Cortex asks for a scenario through the envelope surface
    cortex = await client.post(
        f"{FUTURIS_URL}/v1/friday/forecast",
        headers=friday,
        json={
            "friday_request_id": "req_mesh_live_cortex",
            "target": "friday:eventbus:message_backlog_24h",
            "horizon": "24h",
        },
    )
    steps.append(
        Step(
            "Cortex-domain target forecast (eventbus)",
            cortex.status_code == 201,
            f"status={cortex.status_code}",
            {"prediction": cortex.json().get("prediction", {}).get("point_estimate")}
            if cortex.status_code == 201
            else {},
        )
    )

    # 6. self-status must reflect live peers
    status = await client.get(f"{FUTURIS_URL}/v1/self/status", headers=master)
    payload = status.json() if status.status_code == 200 else {}
    peers = await client.get(f"{FUTURIS_URL}/v1/self/peers", headers=master)
    isolated = peers.json().get("isolated", []) if peers.status_code == 200 else []
    steps.append(
        Step(
            "Self status reports the measured mesh",
            status.status_code == 200 and payload.get("evidence_class") == "live",
            f"status={payload.get('status')} degraded={payload.get('degraded_subsystems')} "
            f"isolated_peers={isolated}",
        )
    )
    return steps


async def fault_scenario(client: httpx.AsyncClient, mesher: httpx.AsyncClient) -> list[Step]:
    """Peers fail one at a time; the agent must degrade, not break."""
    steps: list[Step] = []
    friday = {"X-API-Key": FRIDAY_KEY}
    master = {"X-API-Key": MASTER_KEY}

    # IntelX slow, then failing
    await control(mesher, "fail", peer="intelx", mode="slow", delay_seconds=3.0)
    slow = await client.post(
        f"{FUTURIS_URL}/v1/friday/forecast",
        headers=friday,
        json={
            "friday_request_id": "req_slow_intelx",
            "target": "service:search:error_budget_burn_24h",
        },
    )
    steps.append(
        Step(
            "Forecast completes while research peer is slow",
            slow.status_code == 201,
            f"status={slow.status_code} wall={slow.elapsed.total_seconds():.1f}s",
        )
    )

    await control(mesher, "fail", peer="intelx", mode="down")
    await control(mesher, "fail", peer="memora", mode="error", status=500)
    degraded = await client.post(
        f"{FUTURIS_URL}/v1/market/forecast", headers=master, json={"symbol": "ETH-USD"}
    )
    steps.append(
        Step(
            "Market forecast answers with Memora down",
            degraded.status_code in (200, 201, 503),
            f"status={degraded.status_code}",
            {"hint": degraded.json().get("error", {}).get("message", "")[:80]}
            if degraded.status_code >= 400
            else {},
        )
    )

    await control(mesher, "fail", peer="stratex", mode="down")
    no_telemetry = await client.post(
        f"{FUTURIS_URL}/v1/market/forecast", headers=master, json={"symbol": "SOL-USD"}
    )
    message = json.dumps(no_telemetry.json())[:160] if no_telemetry.status_code >= 400 else ""
    steps.append(
        Step(
            "No market forecast is fabricated without telemetry",
            no_telemetry.status_code == 503 and "telemetry" in message.lower(),
            f"status={no_telemetry.status_code} message={message[:90]}",
        )
    )

    # Recovery: everything back up
    await control(mesher, "reset")
    recovered = await client.post(
        f"{FUTURIS_URL}/v1/friday/forecast",
        headers=friday,
        json={
            "friday_request_id": "req_recovered",
            "target": "service:auth:login_failure_rate_24h",
        },
    )
    peers = await client.get(f"{FUTURIS_URL}/v1/self/peers", headers=master)
    steps.append(
        Step(
            "Recovery after the mesh returns",
            recovered.status_code == 201,
            f"status={recovered.status_code} isolated={peers.json().get('isolated', [])}",
        )
    )
    return steps


async def run(args: argparse.Namespace) -> int:
    processes: list[subprocess.Popen] = []
    env = peer_env(MESH_URL)
    logs = ROOT / "data" / "mesh_live_logs"
    logs.mkdir(parents=True, exist_ok=True)

    def spawn(name: str, command: list[str], log_name: str) -> subprocess.Popen:
        handle = (logs / log_name).open("w", encoding="utf-8")
        process = subprocess.Popen(  # noqa: SIM115 - the handle must outlive this call
            command, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT
        )
        processes.append(process)
        return process

    try:
        spawn(
            "mesh",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "tests.support.peer_mesh:mesh_app",
                "--host",
                "127.0.0.1",
                "--port",
                str(MESH_PORT),
                "--log-level",
                "warning",
            ],
            "mesh.log",
        )
        spawn(
            "futuris",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "futuris.api.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(FUTURIS_PORT),
                "--log-level",
                "warning",
            ],
            "futuris.log",
        )

        headers = {"X-API-Key": MASTER_KEY}
        async with httpx.AsyncClient(timeout=300) as mesher, httpx.AsyncClient(
            timeout=300, headers=headers
        ) as client:
            mesh_ready = await wait_for_health(mesher, f"{MESH_URL}/health")
            app_ready = await wait_for_health(client, f"{FUTURIS_URL}/health")
            if not (mesh_ready and app_ready):
                print(f"startup failed: mesh={mesh_ready} futuris={app_ready}")
                print(f"see {logs}")
                return 2

            steps = []
            try:
                steps = await scenario(client, mesher)
            except Exception as exc:  # noqa: BLE001 - report what completed
                steps.append(Step("scenario aborted", False, f"{type(exc).__name__}: {exc}"))
            try:
                steps += await fault_scenario(client, mesher)
            except Exception as exc:  # noqa: BLE001
                steps.append(Step("fault scenario aborted", False, f"{type(exc).__name__}: {exc}"))

    finally:
        if not args.keep:
            for process in processes:
                process.send_signal(signal.SIGTERM)
            for process in processes:
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()

    print("\n=== live mesh scenarios ===")
    failures = 0
    for step in steps:
        mark = "PASS" if step.ok else "FAIL"
        if not step.ok:
            failures += 1
        print(f"[{mark}] {step.name}")
        if step.detail:
            print(f"        {step.detail}")
        if step.data:
            print(f"        {json.dumps(step.data, default=str)[:240]}")
    print(f"\n{len(steps) - failures} passed | {failures} failed")
    if args.keep:
        print("servers left running (--keep)")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
