import { Forecast, Outcome, CalibrationCurve, BacktestRun, EcosystemOverview, UniverseMatrixResponse, UniversePredictionResponse } from './types';

const API_BASE = '/v1';

function getHeaders(): HeadersInit {
  const apiKey = (typeof window !== 'undefined' && localStorage.getItem('futuris_api_key')) || 'futuris_api';
  return {
    'Accept': 'application/json',
    'Content-Type': 'application/json',
    'X-API-Key': apiKey,
  };
}

async function handleResponse<T>(res: Response, fallbackMsg: string): Promise<T> {
  if (!res.ok) {
    let errorDetail = '';
    try {
      const errorJson = await res.json();
      errorDetail = errorJson.detail || errorJson.message || JSON.stringify(errorJson);
    } catch {
      errorDetail = await res.text().catch(() => '');
    }
    const suffix = errorDetail ? `: ${errorDetail}` : res.statusText ? `: ${res.statusText}` : '';
    throw new Error(`${fallbackMsg} (HTTP ${res.status})${suffix}`);
  }
  return res.json();
}

export async function fetchForecasts(target?: string, status?: string): Promise<Forecast[]> {
  const params = new URLSearchParams();
  if (target) params.append('target', target);
  if (status) params.append('status', status);
  
  const res = await fetch(`${API_BASE}/forecasts?${params.toString()}`, {
    headers: getHeaders(),
  });
  return handleResponse<Forecast[]>(res, 'Failed to fetch forecasts');
}

export async function fetchForecastById(id: string): Promise<Forecast> {
  const res = await fetch(`${API_BASE}/forecasts/${id}`, {
    headers: getHeaders(),
  });
  return handleResponse<Forecast>(res, `Failed to fetch forecast ${id}`);
}

export async function fetchOutcome(forecastId: string): Promise<Outcome | null> {
  const res = await fetch(`${API_BASE}/forecasts/${forecastId}/outcome`, {
    headers: getHeaders(),
  });
  if (res.status === 404) return null;
  return handleResponse<Outcome>(res, `Failed to fetch outcome for ${forecastId}`);
}

export async function fetchCalibration(target?: string): Promise<CalibrationCurve> {
  const params = new URLSearchParams();
  if (target) params.append('target', target);
  const res = await fetch(`${API_BASE}/evaluation/calibration?${params.toString()}`, {
    headers: getHeaders(),
  });
  return handleResponse<CalibrationCurve>(res, 'Failed to fetch calibration');
}

export async function fetchBacktests(): Promise<BacktestRun[]> {
  const res = await fetch(`${API_BASE}/evaluation/backtests`, {
    headers: getHeaders(),
  });
  return handleResponse<BacktestRun[]>(res, 'Failed to fetch backtests');
}

export async function fetchPeers(): Promise<EcosystemOverview> {
  const res = await fetch(`${API_BASE}/ecosystem/peers`, {
    headers: getHeaders(),
  });
  return handleResponse<EcosystemOverview>(res, 'Failed to fetch ecosystem peers');
}

export async function triggerForecast(target: string, horizon: string = '24h'): Promise<Forecast> {
  const res = await fetch(`${API_BASE}/forecasts`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({
      target,
      horizon,
    }),
  });
  return handleResponse<Forecast>(res, `Failed to generate forecast for ${target}`);
}

export async function triggerDemoSeed(): Promise<{ status: string; message: string }> {
  const res = await fetch(`${API_BASE}/ecosystem/seed`, {
    method: 'POST',
    headers: getHeaders(),
  });
  return handleResponse<{ status: string; message: string }>(res, 'Failed to trigger seed');
}

export async function triggerMarketForecast(symbol: string = 'BTCUSDT'): Promise<any> {
  const res = await fetch(`/v1/futuris/forecast`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({
      symbol,
      horizons: ['24h'],
      include_intelx: true,
      include_inference: true,
    }),
  });
  return handleResponse<any>(res, `Failed to generate market forecast for ${symbol}`);
}

export async function fetchUniverseMatrix(): Promise<UniverseMatrixResponse> {
  const res = await fetch(`${API_BASE}/predictions/matrix`, {
    headers: getHeaders(),
  });
  return handleResponse<UniverseMatrixResponse>(res, 'Failed to fetch universe matrix');
}

export async function refreshUniverseMatrix(): Promise<UniverseMatrixResponse> {
  const res = await fetch(`${API_BASE}/predictions/refresh-all`, {
    method: 'POST',
    headers: getHeaders(),
  });
  return handleResponse<UniverseMatrixResponse>(res, 'Failed to refresh universe predictions');
}

export async function requestUniversePrediction(
  target: string,
  domain?: string,
  context?: Record<string, any>
): Promise<UniversePredictionResponse> {
  const res = await fetch(`${API_BASE}/predictions/predict`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({
      target,
      domain,
      context: context || {},
    }),
  });
  return handleResponse<UniversePredictionResponse>(res, `Failed to request prediction for ${target}`);
}