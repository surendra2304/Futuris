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
  expires_at: string;
  review_at: string;
  status: ForecastStatus;
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
  last_interaction: string;
  capabilities: string[];
}

export interface EcosystemOverview {
  peers: PeerAgent[];
  total_online: number;
  total_peers: number;
  timestamp: string;
}

export type RiskLevel = 'NOMINAL' | 'ELEVATED' | 'HIGH' | 'CRITICAL';

export interface TargetPostureItem {
  target: string;
  name: string;
  domain: string;
  point_prediction: number;
  unit: string;
  range_lower: number;
  range_upper: number;
  probability: number | null;
  confidence: string;
  risk_level: RiskLevel;
  mitigation_action: string;
  interpretation: string;
  last_updated: string;
}

export interface DomainPostureSummary {
  domain: string;
  display_name: string;
  risk_level: RiskLevel;
  target_count: number;
  targets: TargetPostureItem[];
}

export interface UniverseMatrixResponse {
  ecosystem_health_score: number;
  overall_posture: RiskLevel;
  total_domains: number;
  total_active_targets: number;
  domains: DomainPostureSummary[];
  timestamp: string;
}

export interface UniversePredictionResponse {
  forecast_id: string;
  target: string;
  target_name: string;
  domain: string;
  point_prediction: number;
  unit: string;
  range_lower: number;
  range_upper: number;
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