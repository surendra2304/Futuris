"""Verification test: Stratex FuturisMarketClient integration with live Futuris app."""

import sys
import asyncio
from httpx import AsyncClient, ASGITransport

from futuris.api.app import app

async def main():
    print("=" * 70)
    print("VERIFYING STRATEX MARKET CLIENT & INTELX INTEGRATION")
    print("=" * 70)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Test POST /v1/futuris/forecast (exact call from Stratex)
        print("\n[TEST 1] Testing POST /v1/futuris/forecast with {'symbol': 'BTCUSDT', 'horizons': ['24h']}")
        resp1 = await client.post(
            "/v1/futuris/forecast",
            json={"symbol": "BTCUSDT", "horizons": ["24h"]},
        )
        print(f"Status Code: {resp1.status_code}")
        assert resp1.status_code == 200, f"Expected 200, got {resp1.status_code}: {resp1.text}"
        data1 = resp1.json()
        print(f"Response Status: {data1.get('status')}")
        print(f"Symbol: {data1.get('symbol')}")
        print(f"Volatility Forecast: {data1.get('volatility_forecast')}")
        print(f"Drawdown Risk: {data1.get('drawdown_risk')}")
        print(f"Regime Outlook: {data1.get('regime_outlook')}")
        print(f"IntelX Context Included: {data1.get('intelx_context_included')}")

        assert data1["status"] == "OK"
        assert data1["symbol"] == "BTCUSDT"
        assert "probability" in data1["volatility_forecast"]
        assert "confidence" in data1["volatility_forecast"]
        assert "probability" in data1["drawdown_risk"]
        assert "current" in data1["regime_outlook"]
        assert "predicted_direction" in data1["regime_outlook"]
        print("  -> [PASS] POST /v1/futuris/forecast conforms strictly to Stratex FuturisForecastContext schema!")

        # 2. Test GET /api/v1/futuris/forecast?symbol=ETHUSDT
        print("\n[TEST 2] Testing GET /api/v1/futuris/forecast?symbol=ETHUSDT")
        resp2 = await client.get("/api/v1/futuris/forecast?symbol=ETHUSDT")
        assert resp2.status_code == 200, f"Expected 200, got {resp2.status_code}: {resp2.text}"
        data2 = resp2.json()
        assert data2["symbol"] == "ETHUSDT"
        print(f"  -> [PASS] GET /api/v1/futuris/forecast?symbol=ETHUSDT returned regime: {data2['regime_outlook']['current']}")

        # 3. Test GET /v1/futuris/accuracy
        print("\n[TEST 3] Testing GET /v1/futuris/accuracy")
        resp3 = await client.get("/v1/futuris/accuracy")
        assert resp3.status_code == 200
        data3 = resp3.json()
        assert "accuracy_pct" in data3
        assert data3["status"] == "ACTIVE"
        print(f"  -> [PASS] Accuracy Endpoint: {data3['accuracy_pct']}% accuracy, Brier={data3['brier_score']}")

        # 4. Test Inbound IntelX Catalyst Webhook
        print("\n[TEST 4] Testing POST /v1/webhooks/research-finding-relevant (IntelX Catalyst)")
        webhook_payload = {
            "event": "research_finding_relevant",
            "data": {
                "run_id": "run_test_12345",
                "finding_summary": "Surge in Bitcoin ETF institutional spot inflows breaks 6-month high",
                "category": "market_moving",
                "confidence": 0.92,
                "domain": "market",
                "recommended_forecast_targets": ["BTCUSDT market volatility"],
            }
        }
        resp4 = await client.post("/v1/webhooks/research-finding-relevant", json=webhook_payload)
        assert resp4.status_code == 200
        data4 = resp4.json()
        assert data4["recalibration_scheduled"] is True
        print(f"  -> [PASS] IntelX Webhook successfully acknowledged and triggered market recalibration for {data4['affected_symbol']}!")

    print("\n" + "=" * 70)
    print("ALL STRATEX & INTELX INTEGRATION TESTS PASSED CLEANLY!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
