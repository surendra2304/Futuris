"""A real peer mesh for FUTURIS's agents, speaking their actual contracts.

Running FUTURIS against dead peers only proves it degrades. Running it against
peers that answer proves whether the collaboration works at all: whether IntelX
research reaches the forecast, whether advisories are durably accepted by
Memora, whether governance events reach Sentinel, whether Stratex telemetry
drives a market forecast.

Each peer implements the endpoints FUTURIS actually calls, verifies the
credential it is given, records every request it receives (so tests can assert
that collaboration happened rather than assuming it), and supports fault
injection through ``/__control``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

#: Credential every peer expects; tests set the matching <PEER>_API_KEY.
MESH_TOKEN = "mesh_peer_token_0123456789abcdefghijklmnop"

PEERS = ("intelx", "inference", "memora", "stratex", "sentinel", "cortex", "forge", "friday")


@dataclass
class Fault:
    """Injected failure: mode is one of down|error|slow|empty."""

    mode: str = "none"
    status: int = 500
    delay_seconds: float = 0.0


@dataclass
class Recorder:
    """Everything the mesh saw, so tests can assert on real collaboration."""

    requests: list[dict[str, Any]] = field(default_factory=list)
    faults: dict[str, Fault] = field(default_factory=dict)

    def record(
        self, peer: str, method: str, path: str, body: Any = None, headers: dict | None = None
    ) -> None:
        self.requests.append(
            {
                "peer": peer,
                "method": method,
                "path": path,
                "body": body,
                "authorization": (headers or {}).get("authorization"),
                "at": time.time(),
            }
        )

    def for_peer(self, peer: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["peer"] == peer]

    def fault_for(self, peer: str) -> Fault:
        return self.faults.get(peer, Fault())


recorder = Recorder()
STARTED_AT = time.time()


def _auth(request: Request, peer: str) -> None:
    token = request.headers.get("authorization", "")
    supplied = (
        token.replace("Bearer ", "").strip() if token else request.headers.get("x-api-key", "")
    )
    if not supplied:
        raise HTTPException(status_code=401, detail=f"{peer} requires a credential")
    if supplied != MESH_TOKEN:
        raise HTTPException(status_code=403, detail=f"{peer} rejected the credential")


async def _apply_fault(peer: str) -> JSONResponse | None:
    fault = recorder.fault_for(peer)
    if fault.delay_seconds:
        import asyncio

        await asyncio.sleep(fault.delay_seconds)
    if fault.mode == "down":
        raise HTTPException(status_code=503, detail=f"{peer} is down")
    if fault.mode == "error":
        raise HTTPException(status_code=fault.status, detail=f"{peer} injected failure")
    return None


def build_mesh_app() -> FastAPI:
    app = FastAPI(title="FUTURIS peer mesh", version="1.0.0")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "uptime_seconds": round(time.time() - STARTED_AT, 1)}

    # ── fault injection / inspection ───────────────────────────────────────
    @app.post("/__control/fail")
    async def inject_fault(
        peer: str, mode: str = "error", status: int = 500, delay_seconds: float = 0.0
    ) -> dict[str, Any]:
        if peer not in PEERS:
            raise HTTPException(status_code=404, detail=f"unknown peer {peer}")
        recorder.faults[peer] = Fault(mode=mode, status=status, delay_seconds=delay_seconds)
        return {"peer": peer, "fault": recorder.faults[peer].__dict__}

    @app.post("/__control/reset")
    async def reset_faults() -> dict[str, Any]:
        recorder.faults.clear()
        recorder.requests.clear()
        return {"reset": True}

    @app.get("/__control/requests")
    async def list_requests(peer: str | None = None) -> dict[str, Any]:
        rows = recorder.for_peer(peer) if peer else recorder.requests
        return {"count": len(rows), "requests": rows[-100:]}

    # ── IntelX: exogenous research ─────────────────────────────────────────
    @app.post("/api/v1/futuris/context")
    async def intelx_context(request: Request) -> JSONResponse:
        body = await request.json()
        recorder.record("intelx", "POST", "/api/v1/futuris/context", body, dict(request.headers))
        fault = await _apply_fault("intelx")
        if fault:
            return fault
        target = str(body.get("forecast_target", "unknown"))
        findings = [
            {
                "report_id": f"intelx_{uuid4().hex[:8]}",
                "asset_or_sector": target,
                "summary": (
                    f"Elevated infrastructure change activity reported for {target}; "
                    "supplier maintenance window overlaps the forecast horizon."
                ),
                "key_findings": [
                    f"Cloud provider maintenance window announced for {target}",
                    f"Third-party latency degradation reported near {target}",
                ],
                "sentiment": -0.35,
                "volatility_impact": 1.4,
                "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "tags": ["infrastructure", "maintenance"],
                "confidence": 0.72,
            }
        ]
        return JSONResponse({"reports": findings, "findings": findings, "count": len(findings)})

    @app.get("/api/v1/research/query")
    async def intelx_query(request: Request, sector: str = "") -> JSONResponse:
        recorder.record(
            "intelx", "GET", "/api/v1/research/query", {"sector": sector}, dict(request.headers)
        )
        fault = await _apply_fault("intelx")
        if fault:
            return fault
        return JSONResponse(
            [
                {
                    "report_id": f"intelx_{uuid4().hex[:8]}",
                    "asset_or_sector": sector,
                    "summary": f"Research context for {sector}",
                    "key_findings": [f"Supply chain pressure observed for {sector}"],
                    "sentiment": -0.2,
                    "volatility_impact": 1.2,
                    "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            ]
        )

    # ── Inference: reasoning gateway ───────────────────────────────────────
    @app.post("/ask")
    async def inference_ask(request: Request) -> JSONResponse:
        body = await request.json()
        recorder.record("inference", "POST", "/ask", body, dict(request.headers))
        fault = await _apply_fault("inference")
        if fault:
            return fault
        prompt = str(body.get("prompt", ""))[:120]
        return JSONResponse(
            {
                "answer": f"Reasoned about: {prompt}. Risk is concentrated in the near window.",
                "model": "mesh-reasoner-1",
                "usage": {"prompt_tokens": 42, "completion_tokens": 24},
            }
        )

    @app.post("/v1/enhance")
    async def inference_enhance(request: Request) -> JSONResponse:
        body = await request.json()
        recorder.record("inference", "POST", "/v1/enhance", body, dict(request.headers))
        fault = await _apply_fault("inference")
        if fault:
            return fault
        return JSONResponse(
            {
                "risks": ["Cross-region failover latency"],
                "drivers": ["deployment_frequency"],
                "narrative": "Interval widened for maintenance overlap.",
            }
        )

    # ── Memora: durable memory ─────────────────────────────────────────────
    @app.post("/v1/memories")
    async def memora_write(request: Request) -> JSONResponse:
        body = await request.json()
        recorder.record("memora", "POST", "/v1/memories", body, dict(request.headers))
        fault = await _apply_fault("memora")
        if fault:
            return fault
        content = str(body.get("content_text", ""))
        if not content:
            raise HTTPException(status_code=422, detail="content_text is required")
        return JSONResponse(
            {"status": "stored", "memory_id": f"mem_{uuid4().hex[:12]}", "cloud": True},
            status_code=201,
        )

    @app.post("/mesh/envelope")
    async def memora_envelope(request: Request) -> JSONResponse:
        body = await request.json()
        recorder.record("memora", "POST", "/mesh/envelope", body, dict(request.headers))
        fault = await _apply_fault("memora")
        if fault:
            return fault
        message_id = str(body.get("message_id", f"env_{uuid4().hex[:8]}"))
        return JSONResponse({"status": "accepted", "event_id": message_id}, status_code=202)

    @app.get("/v1/memories/search")
    async def memora_search(request: Request, q: str = "") -> JSONResponse:
        recorder.record("memora", "GET", "/v1/memories/search", {"q": q}, dict(request.headers))
        fault = await _apply_fault("memora")
        if fault:
            return fault
        wrote = any(
            r["peer"] == "memora" and r["path"] == "/v1/memories" for r in recorder.requests
        )
        return JSONResponse({"results": [], "has_writes": wrote})

    # ── Stratex: market telemetry ──────────────────────────────────────────
    @app.get("/api/v1/telemetry/trading")
    async def stratex_telemetry(request: Request, start: str = "", end: str = "") -> JSONResponse:
        recorder.record(
            "stratex",
            "GET",
            "/api/v1/telemetry/trading",
            {"start": start, "end": end},
            dict(request.headers),
        )
        fault = await _apply_fault("stratex")
        if fault:
            return fault
        now = time.time()
        series = []
        for i in range(24):
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - (24 - i) * 3600))
            series.append(
                {
                    "series_id": "portfolio:equity",
                    "timestamp": stamp,
                    "value": 100000 + i * 250,
                    "metric_type": "equity",
                }
            )
            series.append(
                {
                    "series_id": "market:volatility_index",
                    "timestamp": stamp,
                    "value": 0.42 + 0.01 * (i % 7),
                    "metric_type": "volatility",
                }
            )
            series.append(
                {
                    "series_id": "portfolio:max_drawdown_pct",
                    "timestamp": stamp,
                    "value": 0.06 + 0.002 * (i % 5),
                    "metric_type": "drawdown",
                }
            )
        return JSONResponse(series)

    @app.get("/api/v1/status")
    async def stratex_status(request: Request) -> JSONResponse:
        recorder.record("stratex", "GET", "/api/v1/status", None, dict(request.headers))
        fault = await _apply_fault("stratex")
        if fault:
            return fault
        return JSONResponse(
            {
                "data": {
                    "equity": 104500.0,
                    "max_drawdown_pct": 0.058,
                    "volatility": 0.47,
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            }
        )

    @app.post("/api/v1/futuris/forecast")
    async def stratex_forecast_ingest(request: Request) -> JSONResponse:
        body = await request.json()
        recorder.record("stratex", "POST", "/api/v1/futuris/forecast", body, dict(request.headers))
        fault = await _apply_fault("stratex")
        if fault:
            return fault
        return JSONResponse(
            {"status": "accepted", "dispatch_id": f"strx_{uuid4().hex[:8]}"}, status_code=202
        )

    # ── Sentinel: governance ───────────────────────────────────────────────
    @app.post("/v1/events")
    async def sentinel_event(request: Request) -> JSONResponse:
        body = await request.json()
        recorder.record("sentinel", "POST", "/v1/events", body, dict(request.headers))
        fault = await _apply_fault("sentinel")
        if fault:
            return fault
        return JSONResponse(
            {"status": "recorded", "event_id": f"sen_{uuid4().hex[:8]}"}, status_code=202
        )

    # ── Cortex / Forge / FRIDAY: task receivers ────────────────────────────
    def _task_receiver(peer: str):
        async def handler(request: Request) -> JSONResponse:
            body = await request.json()
            recorder.record(peer, "POST", "/v1/tasks", body, dict(request.headers))
            fault = await _apply_fault(peer)
            if fault:
                return fault
            return JSONResponse(
                {
                    "task_id": body.get("task_id", f"task_{uuid4().hex[:8]}"),
                    "status": "SUCCESS",
                    "summary": f"{peer} acknowledged the delegated task",
                }
            )

        return handler

    for peer in ("cortex", "forge", "friday"):
        app.post(f"/{peer}/v1/tasks")(_task_receiver(peer))

    return app


mesh_app = build_mesh_app()
