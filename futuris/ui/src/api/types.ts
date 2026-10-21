export type ForecastStatus = 'draft' | 'active' | 'resolved' | 'expired' | 'invalidated';
export type ConfidenceLevel = 'high' | 'medium' | 'low';
export type ScenarioType = 'baseline' | 'upside' | 'downside' | 'stress' | 'counterfactual' | 'user_defined';

export interface Driver {
  name: string;
  direction: 'positive' | 'negative' | 'neutral';
  strength: number;
  leading_or_lagging: 'leading' | 'lagging';
  evidence_refs: string[];
}

export interface EvidenceRef {
  evidence_id: string;
  source: string;
  source_trust: string;
  signal_class: string;
  as_of: string;
  snapshot_path: string;
  content_hash: string;
}

export interface RangeValues {
  lower: number;
  central: number;
  upper: number;
}

export interface Forecast {
  forecast_id: string;
  target: string;
  prediction: number;
  range: RangeValues;
  probability: number | null;
  confidence: ConfidenceLevel;
  drivers: Driver[];
  evidence: EvidenceRef[];
  assumptions: string[];
  model: string;
  as_of?: string;
  horizon?: string;
  expires_at: string;
  review_at: string;
  status: ForecastStatus;
  created_at?: string;
  evidence_class?: 'live' | 'derived' | 'synthetic' | 'demo';
  evidence_source?: string | null;
  input_telemetry_verified?: boolean;
}

export interface Outcome {
  outcome_id: string;
  forecast_id: string;
  observed_value: number | null;
  event_occurred: boolean | null;
  resolved_at: string;
  resolution_method: string;
  ambiguity_note: string | null;
  resolution_rule_version: string;
}

export interface CalibrationCurve {
  target: string;
  bin_centers: number[];
  observed_frequencies: number[];
  bin_counts: number[];
  expected_calibration_error: number;
}

export interface BacktestRun {
  run_id: string;
  target: string;
  stride_hours: number;
  horizon: string;
  total_forecasts: number;
  mae: number;
  coverage_90: number;
  created_at: string;
}

export interface ForecastSubscription {
  target: string;
  horizon: string;
  refresh_interval_minutes: number;
  delta_prob_threshold: number;
  delta_pred_threshold: number;
  enabled: boolean;
}

export interface PeerAgent {
  name: string;
  url: string;
  role: string;
  status: 'online' | 'degraded' | 'offline';
  latency_ms: number | null;
  evidence_class: 'http_health_200' | 'http_health_non_200' | 'probe_failed';
  http_status: number | null;
  observed_at: string;
  capabilities: string[];
}

export interface EcosystemOverview {
  peers: PeerAgent[];
  total_online: number;
  total_peers: number;
  timestamp: string;
}

export type RiskLevel = 'UNKNOWN' | 'NOMINAL' | 'ELEVATED' | 'HIGH' | 'CRITICAL';

export interface TargetPostureItem {
  target: string;
  name: string;
  domain: string;
  point_prediction: number | null;
  unit: string;
  range_lower: number | null;
  range_upper: number | null;
  probability: number | null;
  confidence: string;
  risk_level: RiskLevel;
  mitigation_action: string;
  interpretation: string;
  last_updated: string | null;
  status?: string;
  evidence_class?: string | null;
  evidence_source?: string | null;
}

export interface DomainPostureSummary {
  domain: string;
  display_name: string;
  risk_level: RiskLevel;
  target_count: number;
  scored_target_count: number;
  unknown_target_count: number;
  targets: TargetPostureItem[];
}

export interface UniverseMatrixResponse {
  ecosystem_health_score: number | null;
  overall_posture: RiskLevel;
  total_domains: number;
  total_active_targets: number;
  scored_target_count: number;
  unknown_target_count: number;
  coverage_pct: number | null;
  domains: DomainPostureSummary[];
  timestamp: string;
}

export interface UniversePredictionResponse {
  forecast_id: string;
  target: string;
  target_name: string;
  domain: string;
  point_prediction: number | null;
  unit: string;
  range_lower: number | null;
  range_upper: number | null;
  probability: number | null;
  confidence: string;
  risk_level: RiskLevel;
  mitigation_action: string;
  interpretation: string;
  drivers: string[];
  model_version: string;
  as_of: string;
  expires_at: string;
  intelx_context_included: boolean;
}

export interface PositionItem {
  symbol: string;
  side: 'LONG' | 'SHORT' | string;
  size: number;
  entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  leverage: number;
  liquidation_price: number;
  margin: number;
  updated_at: string;
}

export interface AssetRiskReport {
  symbol: string;
  sample_size: number;
  basel_traffic_light: 'green' | 'yellow' | 'red';
  var_95: number;
  var_99: number;
  cvar_95: number;
  cvar_99: number;
  kupiec_passed: boolean;
  kupiec_p_value: number;
  breach_count_95: number;
  weight: number;
}

export interface PortfolioRiskSummary {
  total_exposure_usd: number;
  total_margin_usd: number;
  total_unrealized_pnl: number;
  portfolio_var_95: number;
  portfolio_cvar_95: number;
  highest_risk_asset: string;
  overall_traffic_light: 'green' | 'yellow' | 'red';
  confidence_level: number;
  lookback_window_days: number;
}

export interface PortfolioRiskResponse {
  active_pairs: string[];
  positions: PositionItem[];
  assets: AssetRiskReport[];
  portfolio_summary: PortfolioRiskSummary;
  prediction_is_not_authorization: boolean;
  timestamp: string;
}

export interface FanChartSlicePayload {
  step: number;
  mean: number;
  std: number;
  p05: number;
  p10: number;
  p25: number;
  p50: number;
  p75: number;
  p90: number;
  p95: number;
  p99: number;
  exceedance_probability?: number | null;
}

export interface FanChartResponse {
  target: string;
  base_value: number;
  num_samples: number;
  horizon_steps: number;
  fan_chart: FanChartSlicePayload[];
  trajectories_sample: number[][];
  driver_names: string[];
  sample_correlations: Record<string, Record<string, number>>;
  prediction_is_not_authorization: boolean;
  timestamp: string;
}
