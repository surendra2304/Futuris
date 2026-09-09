import sys
from pathlib import Path
from unittest.mock import patch
import requests

# Add Stratex to sys.path
stratex_path = Path("..") / "Stratex"
sys.path.insert(0, str(stratex_path.resolve()))

from intelligence.futuris_client import FuturisMarketClient

print("=" * 70)
print("TESTING STRATEX REAL FuturisMarketClient AGAINST FUTURIS API")
print("=" * 70)

from fastapi.testclient import TestClient
from futuris.api.app import app

test_client = TestClient(app)

def mock_post(url, **kwargs):
    # Route through fastapi TestClient
    path = url.replace("https://futuris-x4f4.onrender.com", "")
    return test_client.post(path, **kwargs)

with patch("requests.post", side_effect=mock_post):
    client = FuturisMarketClient()
    print("Calling client.fetch_forecast('BTCUSDT')...")
    fc = client.fetch_forecast("BTCUSDT")

    print(f"Symbol: {fc.symbol}")
    print(f"Is Valid: {fc.is_valid()}")
    print(f"Volatility Forecast: {fc.volatility_forecast}")
    print(f"Drawdown Risk: {fc.drawdown_risk}")
    print(f"Regime Outlook: {fc.regime_outlook}")

    assert fc.symbol == "BTCUSDT"
    assert fc.is_valid() is True
    assert "probability" in fc.volatility_forecast
    assert "probability" in fc.drawdown_risk
    assert "current" in fc.regime_outlook

    advisory_ctx = client.get_latest_futuris_context("BTCUSDT")
    print("\nAdvisory Context for Stratex Decision Funnel:")
    print(advisory_ctx)
    assert advisory_ctx is not None
    assert "volatility_forecast" in advisory_ctx

print("\n" + "=" * 70)
print("STRATEX CLIENT SUCCESSFULLY CONSUMES FUTURIS PREDICTIONS!")
print("=" * 70)
