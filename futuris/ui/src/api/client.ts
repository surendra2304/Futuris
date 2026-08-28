import { Forecast, Outcome, CalibrationCurve, BacktestRun } from './types';

const API_BASE = '/v1';

export async function fetchForecasts(target?: string, status?: string): Promise<Forecast[]> {
  const params = new URLSearchParams();
  if (target) params.append('target', target);
  if (status) params.append('status', status);
  
  const res = await fetch(`${API_BASE}/forecasts?${params.toString()}`);
  if (!res.ok) throw new Error(`Failed to fetch forecasts: ${res.statusText}`);
  return res.json();
}

export async function fetchForecastById(id: string): Promise<Forecast> {
  const res = await fetch(`${API_BASE}/forecasts/${id}`);
  if (!res.ok) throw new Error(`Failed to fetch forecast: ${res.statusText}`);
  return res.json();
}

export async function fetchOutcome(forecastId: string): Promise<Outcome | null> {
  const res = await fetch(`${API_BASE}/forecasts/${forecastId}/outcome`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Failed to fetch outcome: ${res.statusText}`);
  return res.json();
}

export async function fetchCalibration(target?: string): Promise<CalibrationCurve> {
  const params = new URLSearchParams();
  if (target) params.append('target', target);
  const res = await fetch(`${API_BASE}/evaluation/calibration?${params.toString()}`);
  if (!res.ok) throw new Error(`Failed to fetch calibration: ${res.statusText}`);
  return res.json();
}

export async function fetchBacktests(): Promise<BacktestRun[]> {
  const res = await fetch(`${API_BASE}/evaluation/backtests`);
  if (!res.ok) throw new Error(`Failed to fetch backtests: ${res.statusText}`);
  return res.json();
}