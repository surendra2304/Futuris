#!/usr/bin/env python3
"""Adversarial API harness: use FUTURIS the way a hostile client would.

It enumerates every operation in the OpenAPI contract and drives it with
payloads a real client (or an attacker, or a confused agent) sends: empty
bodies, wrong types, giant values, unicode, path-traversal strings, absurd
horizons, forged headers, duplicate keys, missing auth and unknown fields.

The goal is not to be clever; it is to find every request that makes the
service return a 5xx, a non-JSON body, or an error without the documented
envelope. Those are the bugs.

Usage (in-process, no server needed):
    python scripts/adversarial_harness.py --api-key "$FUTURIS_API_KEY"

Usage (against a running server):
    python scripts/adversarial_harness.py --base-url http://127.0.0.1:8100 \
        --api-key "$FUTURIS_API_KEY"

Exit code is non-zero when any case produced a server error or a malformed
error body.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx

UUID_ZERO = "00000000-0000-0000-0000-000000000000"
UUID_FAKE = "11111111-2222-3333-4444-555555555555"

NASTY_STRINGS = [
    "",
    " ",
    "x" * 5000,
    "../../etc/passwd",
    "'; DROP TABLE forecasts; --",
    "🙂 emoji \u202e right-to-left",
    "null",
    "NaN",
    "1e999",
    "-1",
    "0",
    "true",
]

NASTY_JSON: list[Any] = [
    {},
    None,
    [],
    "string-body",
    12345,
    {"target": None},
    {"target": 12345},
    {"target": "service:x:y", "horizon": "99999999999999999999d"},
    {"target": "service:x:y", "horizon": "-5h"},
    {"target": "service:x:y", "horizon": "0h"},
    {"target": "service:x:y", "confidence_level": 0.99},
    {"target": "service:x:y", "context": {"point_estimate": "not-a-number"}},
    {"target": "service:x:y", "context": {"point_estimate": 1e308, "probability": 5.0}},
    {"target": "service:x:y", "as_of": "not-a-date"},
    {"target": "service:x:y", "as_of": "9999-12-31T23:59:59Z"},
    {"target": "service:x:y", "extra": {"nested": {"deep": [1, 2, {"a": "b"}]}}},
    {"observed_value": "NaN"},
    {"observed_value": None, "event_occurred": "maybe"},
    {"question": "x" * 20000, "base_forecast_id": UUID_FAKE},
    {"variable_changes": [{"name": "a", "delta": "huge"}]},
]


# Operations that legitimately answer 503/501 when an external peer is not
# configured in this environment. They still must use the error envelope, and
# they still count as findings when the status differs from the expectation.
EXPECTED_DEGRADED: dict[str, tuple[set[int], str]] = {
    "GET /v1/friday/calibration": ({503}, "FRIDAY credential not configured in this run"),
    "GET /v1/friday/forecasts": ({503}, "FRIDAY credential not configured in this run"),
    "POST /v1/friday/delegate": ({503, 401, 403}, "FRIDAY credential guard"),
    "POST /v1/friday/forecast": ({503, 401, 403}, "FRIDAY credential guard"),
    "POST /v1/friday/resolution": ({503, 401, 403}, "FRIDAY credential guard"),
    "POST /v1/friday/scenario": ({503, 401, 403}, "FRIDAY credential guard"),
    "POST /v1/friday/forecasts/{forecast_id}/cancel": ({503, 401, 403}, "FRIDAY credential guard"),
    "POST /v1/friday/forecasts/{forecast_id}/resolve": ({503, 401, 403}, "FRIDAY credential guard"),
    "GET /v1/market/forecast": ({503}, "Stratex telemetry unavailable offline"),
    "POST /v1/market/forecast": ({503}, "Stratex telemetry unavailable offline"),
    "POST /v1/futuris/forecast": ({503}, "Stratex telemetry unavailable offline"),
    "POST /v1/task/execute": ({501}, "documented refusal: no evidence-backed handler"),
    "POST /v1/webhooks/research-finding-relevant": (
        {503},
        "inbound webhook secret not configured in this run",
    ),
    "POST /v1/webhooks": ({503}, "inbound webhook secret not configured in this run"),
}


@dataclass
class Finding:
    operation: str
    case: str
    status: int | None
    detail: str = ""
    body: str = ""


@dataclass
class Summary:
    checked: int = 0
    findings: list[Finding] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)


def envelope_ok(status: int, body: str) -> str | None:
    """Return a complaint when an error body does not match the contract."""
    if 200 <= status < 300:
        return None
    try:
        parsed = json.loads(body)
    except ValueError:
        return "error body is not JSON"
    if not isinstance(parsed, dict) or "error" not in parsed:
        return "error body has no 'error' envelope"
    err = parsed["error"]
    if not isinstance(err, dict) or "code" not in err or "message" not in err:
        return "error envelope missing code/message"
    return None


def _aliases(operation: str) -> list[str]:
    """Every documented prefix of an operation, so expectations match all mounts.

    The same routers are mounted at ``/v1``, ``/api/v1``, ``/v1/market`` and
    ``/v1/futuris``; an expectation written once must cover all of them.
    """
    method, _, path = operation.partition(" ")
    aliases = [operation]
    for prefix in ("/api",):
        if path.startswith(prefix + "/"):
            aliases.append(f"{method} {path[len(prefix):]}")
    # /api/v1/futuris/forecast -> /v1/futuris/forecast -> /v1/market/forecast
    stripped = list(aliases)
    for alias in stripped:
        _, _, alias_path = alias.partition(" ")
        for mount in ("/v1/futuris", "/v1/market"):
            if alias_path.startswith(mount + "/"):
                aliases.append(f"{method} /v1/market{alias_path[len(mount):]}")
    for mount in ("/v1/market", "/v1/futuris"):
        if path.startswith(mount + "/"):
            aliases.append(f"{method} /v1/market{path[len(mount):]}")
    return aliases


def expected_degraded(operation: str) -> tuple[set[int], str] | None:
    for alias in _aliases(operation):
        if alias in EXPECTED_DEGRADED:
            return EXPECTED_DEGRADED[alias]
    return None


def _template(path: str) -> str:
    """Recover the templated route from a concrete path, for expectations."""
    if UUID_FAKE in path:
        return path.replace(UUID_FAKE, "{forecast_id}")
    return path


def path_variants(path: str) -> list[str]:
    """Concrete paths for a templated route, one brutal one at a time."""
    variants = [path]
    if "{" in path:
        filled = path
        while "{" in filled:
            start = filled.index("{")
            end = filled.index("}", start)
            filled = filled[:start] + UUID_FAKE + filled[end + 1 :]
        variants.append(filled)
    return variants


def build_cases(method: str, path: str) -> list[tuple[str, Any, dict[str, str]]]:
    """(case name, json body or sentinel, headers)."""
    cases: list[tuple[str, Any, dict[str, str]]] = []
    if method in ("post", "put", "patch"):
        cases.append(("empty-object", {}, {}))
        cases.append(("no-body", None, {}))
        cases.append(("string-body", "just a string", {}))
        cases.append(("array-body", [1, 2, 3], {}))
        for index, payload in enumerate(NASTY_JSON):
            cases.append((f"nasty-{index}", payload, {}))
        cases.append(("wrong-content-type", "{not json", {"Content-Type": "application/xml"}))
        cases.append(("huge-header", {}, {"X-Request-ID": "x" * 9000}))
    if method in ("get", "delete"):
        cases.append(("no-query", None, {}))
    for nasty in NASTY_STRINGS[:4]:
        cases.append((f"query-injection={nasty[:12]!r}", None, {}))
    return cases


async def probe(
    client: httpx.AsyncClient,
    summary: Summary,
    method: str,
    path: str,
    case: str,
    payload: Any,
    headers: dict[str, str],
    *,
    authenticated: bool,
    template: str | None = None,
) -> None:
    summary.checked += 1
    url = path
    request_headers = dict(headers)
    if authenticated:
        request_headers.update(client.headers)
    try:
        if payload is None:
            if method in ("get", "delete"):
                response = await client.request(
                    method.upper(), url, headers=request_headers, params={"probe": case}
                )
            else:
                response = await client.request(method.upper(), url, headers=request_headers)
        else:
            response = await client.request(
                method.upper(), url, headers=request_headers, json=payload
            )
    except Exception as exc:  # noqa: BLE001
        summary.add(
            Finding(method.upper() + " " + path, case, None, f"{type(exc).__name__}: {exc}")
        )
        return

    status = response.status_code
    body = response.text
    expected = expected_degraded(method.upper() + " " + (template or path))
    if expected and status in expected[0]:
        return
    if status >= 500:
        summary.add(
            Finding(method.upper() + " " + path, case, status, "server error", body[:2000])
        )
        return
    complaint = envelope_ok(status, body)
    if complaint:
        summary.add(Finding(method.upper() + " " + path, case, status, complaint, body[:2000]))


async def run_against(
    client: httpx.AsyncClient, spec: dict[str, Any], summary: Summary, concurrency: int = 8
) -> None:
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(
        method: str,
        concrete: str,
        case: str,
        payload: Any,
        headers: dict[str, str],
        template: str,
    ) -> None:
        async with semaphore:
            await probe(
                client,
                summary,
                method,
                concrete,
                case,
                payload,
                headers,
                authenticated=True,
                template=template,
            )

    jobs: list[asyncio.Task] = []
    for path, operations in sorted(spec.get("paths", {}).items()):
        for method, operation in sorted(operations.items()):
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if operation.get("tags") == ["UI"]:
                continue
            for concrete in path_variants(path):
                for case, payload, headers in build_cases(method, concrete):
                    jobs.append(
                        asyncio.create_task(
                            guarded(method, concrete, case, payload, headers, path)
                        )
                    )
    if jobs:
        await asyncio.gather(*jobs)


def report(summary: Summary) -> int:
    print(f"\ncases executed: {summary.checked}")
    print(f"findings: {len(summary.findings)}")
    by_operation: dict[str, int] = {}
    for finding in summary.findings:
        by_operation[finding.operation] = by_operation.get(finding.operation, 0) + 1
    for operation, count in sorted(by_operation.items(), key=lambda kv: -kv[1]):
        print(f"  {count:4d}  {operation}")
    if summary.findings:
        print("\nfirst 15 findings:")
        for finding in summary.findings[:15]:
            print(f"  [{finding.status}] {finding.operation} / {finding.case}: {finding.detail}")
            print(f"        {finding.body[:220]!r}")
    return 1 if summary.findings else 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default=os.environ.get("FUTURIS_API_KEY", ""))
    parser.add_argument("--limit-operations", type=int, default=0)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()

    if not args.api_key:
        print("FUTURIS_API_KEY (or --api-key) is required", file=sys.stderr)
        return 2

    summary = Summary()
    headers = {"X-API-Key": args.api_key}
    if args.base_url:
        transport = None
        base_url = args.base_url
        spec_source = args.base_url + "/openapi.json"
    else:
        os.environ.setdefault("FUTURIS_API_KEY", args.api_key)
        from futuris.api.app import app

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        base_url = "http://testserver"
        spec_source = None

    # A real deployment runs the ASGI lifespan (schema check, scheduler,
    # self-healing).  Driving the app without it measures a configuration nobody
    # runs -- the first requests then hit an uninitialised database and report a
    # storage failure that says more about the harness than about the app.
    lifespan = None if args.base_url else app.router.lifespan_context(app)

    async def _run() -> None:
        async with httpx.AsyncClient(
            transport=transport, base_url=base_url, headers=headers, timeout=300
        ) as client:
            if spec_source:
                spec = (await client.get(spec_source)).json()
            else:
                from futuris.api.app import app

                spec = app.openapi()

            if args.limit_operations:
                spec = {"paths": dict(list(spec["paths"].items())[: args.limit_operations])}
            await run_against(client, spec, summary, args.concurrency)

    if lifespan is None:
        await _run()
    else:
        async with lifespan:
            await _run()

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(
                [finding.__dict__ for finding in summary.findings], handle, indent=2
            )
    return report(summary)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
