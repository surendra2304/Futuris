import React, { useEffect, useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { fetchForecasts, fetchUniverseMatrix, refreshUniverseMatrix, triggerDemoSeed } from '../api/client';
import { Forecast, ConfidenceLevel, UniverseMatrixResponse } from '../api/types';
import {
  HelpCircle,
  AlertCircle,
  RefreshCw,
  Zap,
  Sparkles,
  Shield,
  Activity,
  Layers,
  Cpu,
  Database,
  Terminal,
  TrendingUp,
  Globe,
  Radio,
  CheckCircle2,
} from 'lucide-react';

export const ForecastListPage: React.FC = () => {
  const [forecasts, setForecasts] = useState<Forecast[]>([]);
  const [matrix, setMatrix] = useState<UniverseMatrixResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState<string>('');
  const [selectedDomain, setSelectedDomain] = useState<string>('all');
  const [latestOnly, setLatestOnly] = useState<boolean>(true);
  const [generating, setGenerating] = useState<boolean>(false);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [fData, mData] = await Promise.all([
        fetchForecasts(undefined, filterStatus || undefined),
        fetchUniverseMatrix().catch(() => null),
      ]);
      setForecasts(fData);
      if (mData) setMatrix(mData);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load forecasts');
    } finally {
      setLoading(false);
    }
  };

  const handleRefreshUniverse = async () => {
    setGenerating(true);
    setError(null);
    try {
      const updated = await refreshUniverseMatrix();
      setMatrix(updated);
      const fData = await fetchForecasts(undefined, filterStatus || undefined);
      setForecasts(fData);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to refresh universe predictions');
    } finally {
      setGenerating(false);
    }
  };

  const handleQuickSeed = async () => {
    setGenerating(true);
    setError(null);
    try {
      await triggerDemoSeed();
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to seed workspace');
    } finally {
      setGenerating(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [filterStatus]);

  // Format dynamic prediction units
  const formatPrediction = (target: string, val: number): string => {
    const t = target.toLowerCase();
    if (
      t.includes('volatility') ||
      t.includes('risk') ||
      t.includes('probability') ||
      t.includes('drawdown') ||
      t.includes('breach') ||
      t.includes('oom') ||
      t.includes('failure') ||
      t.includes('backlog') ||
      t.includes('regression')
    ) {
      return `${val.toFixed(1)}%`;
    }
    if (t.includes('days') || t.includes('exhaustion')) {
      return `${val.toFixed(1)} days`;
    }
    if (t.includes('latency') || t.includes('duration')) {
      return `${val.toFixed(0)} ms`;
    }
    if (t.includes('capacity') || t.includes('checkout') || t.includes('rpm')) {
      return `${val.toFixed(0)} rpm`;
    }
    if (t.includes('health') || t.includes('score') || t.includes('velocity') || t.includes('idx')) {
      return `${val.toFixed(1)} idx`;
    }
    return `${val.toFixed(1)}`;
  };

  // Format dynamic uncertainty ranges
  const formatRange = (target: string, lower: number, upper: number): string => {
    const t = target.toLowerCase();
    if (
      t.includes('volatility') ||
      t.includes('risk') ||
      t.includes('probability') ||
      t.includes('drawdown') ||
      t.includes('breach') ||
      t.includes('oom') ||
      t.includes('failure') ||
      t.includes('backlog') ||
      t.includes('regression')
    ) {
      return `[${lower.toFixed(1)}%, ${upper.toFixed(1)}%]`;
    }
    if (t.includes('days') || t.includes('exhaustion')) {
      return `[${lower.toFixed(0)}d, ${upper.toFixed(0)}d]`;
    }
    if (t.includes('latency')) {
      return `[${lower.toFixed(0)}ms, ${upper.toFixed(0)}ms]`;
    }
    if (t.includes('capacity') || t.includes('checkout') || t.includes('rpm')) {
      return `[${lower.toFixed(0)}, ${upper.toFixed(0)}] rpm`;
    }
    return `[${lower.toFixed(1)}, ${upper.toFixed(1)}]`;
  };

  // Deduce domain info and human label
  const getDomainInfo = (target: string) => {
    const t = target.toLowerCase();
    if (t.startsWith('sentinel')) {
      return {
        domain: 'sentinel',
        agent: 'SENTINEL',
        label: t.includes('threat') ? 'Threat Anomaly Risk' : 'API Rate-Limit Saturation',
        style: 'bg-rose-100 text-rose-800 border-rose-300',
      };
    }
    if (t.startsWith('cortex')) {
      return {
        domain: 'cortex',
        agent: 'CORTEX',
        label: t.includes('sla') ? 'Task SLA Breach Risk' : 'Subagent Thrashing Risk',
        style: 'bg-purple-100 text-purple-800 border-purple-300',
      };
    }
    if (t.startsWith('forge')) {
      return {
        domain: 'forge',
        agent: 'FORGE',
        label: t.includes('pipeline') ? 'Build Pipeline Failure Risk' : 'Release Rollback Risk',
        style: 'bg-amber-100 text-amber-800 border-amber-300',
      };
    }
    if (t.startsWith('memora')) {
      return {
        domain: 'memora',
        agent: 'MEMORA',
        label: t.includes('storage') ? 'Storage Exhaustion Horizon' : 'Vector Latency Spike Risk',
        style: 'bg-cyan-100 text-cyan-800 border-cyan-300',
      };
    }
    if (t.startsWith('inference')) {
      return {
        domain: 'inference',
        agent: 'INFERENCE',
        label: t.includes('oom') ? 'GPU VRAM OOM Risk' : 'Queue Starvation Risk',
        style: 'bg-blue-100 text-blue-800 border-blue-300',
      };
    }
    if (t.startsWith('intelx')) {
      return {
        domain: 'intelx',
        agent: 'INTELX',
        label: t.includes('velocity') ? 'Breaking Topic Velocity' : 'API Quota Depletion Risk',
        style: 'bg-violet-100 text-violet-800 border-violet-300',
      };
    }
    if (t.startsWith('market') || t.includes('crypto') || t.startsWith('stratex')) {
      return {
        domain: 'stratex',
        agent: 'STRATEX',
        label: t.includes('btcusdt')
          ? 'BTC/USDT Volatility'
          : t.includes('ethusdt')
          ? 'ETH/USDT Volatility'
          : 'Drawdown Risk',
        style: 'bg-emerald-100 text-emerald-800 border-emerald-300',
      };
    }
    if (t.startsWith('friday')) {
      return {
        domain: 'friday',
        agent: 'FRIDAY',
        label: t.includes('health') ? 'Global System Health Index' : 'Event Bus Congestion Risk',
        style: 'bg-indigo-100 text-indigo-800 border-indigo-300',
      };
    }
    return {
      domain: 'infra',
      agent: 'INFRA',
      label: 'Checkout Capacity Exceedance',
      style: 'bg-slate-100 text-slate-800 border-slate-300',
    };
  };

  // Filtered & optionally deduplicated forecasts
  const displayedForecasts = useMemo(() => {
    let list = forecasts;

    // Filter by domain
    if (selectedDomain !== 'all') {
      list = list.filter((f) => getDomainInfo(f.target).domain === selectedDomain);
    }

    // Deduplicate: latest per target
    if (latestOnly) {
      const seen = new Set<string>();
      const deduplicated: Forecast[] = [];
      for (const f of list) {
        if (!seen.has(f.target)) {
          seen.add(f.target);
          deduplicated.push(f);
        }
      }
      return deduplicated;
    }

    return list;
  }, [forecasts, selectedDomain, latestOnly]);

  const renderConfidenceBadge = (level: ConfidenceLevel) => {
    const styles = {
      high: 'bg-emerald-100 text-emerald-800 border-emerald-300',
      medium: 'bg-amber-100 text-amber-800 border-amber-300',
      low: 'bg-rose-100 text-rose-800 border-rose-300',
    };
    return (
      <span
        className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wider border ${styles[level]}`}
      >
        {level}
      </span>
    );
  };

  const renderProbabilityBar = (prob: number | null) => {
    if (prob === null) return <span className="text-slate-400 font-mono text-xs">N/A</span>;
    const pct = Math.round(prob * 100);
    return (
      <div className="flex items-center space-x-2">
        <div className="w-16 bg-slate-200 rounded-full h-2 overflow-hidden">
          <div
            className={`h-full ${pct > 60 ? 'bg-rose-500' : pct > 30 ? 'bg-amber-500' : 'bg-emerald-500'}`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="text-xs font-mono font-medium text-slate-700">{pct}%</span>
      </div>
    );
  };

  const domainTabs = [
    { id: 'all', name: 'All Domains', icon: Globe },
    { id: 'friday', name: 'FRIDAY Core', icon: Radio },
    { id: 'sentinel', name: 'Sentinel (Security)', icon: Shield },
    { id: 'cortex', name: 'Cortex (Cognition)', icon: Cpu },
    { id: 'forge', name: 'Forge (CI/CD)', icon: Terminal },
    { id: 'memora', name: 'Memora (Memory)', icon: Database },
    { id: 'inference', name: 'Inference (GPU)', icon: Zap },
    { id: 'intelx', name: 'IntelX (Research)', icon: Layers },
    { id: 'stratex', name: 'Stratex (Trading)', icon: TrendingUp },
    { id: 'infra', name: 'Infra (Capacity)', icon: Activity },
  ];

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Top Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 flex items-center space-x-2">
            <Radio className="w-6 h-6 text-indigo-600 animate-pulse" />
            <span>FRIDAY Universe Predictive Intelligence</span>
          </h1>
          <p className="text-sm text-slate-500">
            Real-time calibrated forecasting matrix across all 9 FRIDAY Universe subsystems
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={handleRefreshUniverse}
            disabled={generating}
            className="flex items-center space-x-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-md text-xs font-semibold shadow-sm disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${generating ? 'animate-spin' : ''}`} />
            <span>{generating ? 'Re-Forecasting Universe...' : 'Re-Forecast Universe'}</span>
          </button>
          <button
            onClick={handleQuickSeed}
            disabled={generating}
            className="flex items-center space-x-1.5 px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-300 rounded-md text-xs font-semibold disabled:opacity-50"
          >
            <Sparkles className="w-3.5 h-3.5 text-amber-500" />
            <span>Seed 9-Agent Matrix</span>
          </button>
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="px-3 py-1.5 border border-slate-300 rounded-md text-xs bg-white font-medium text-slate-700"
          >
            <option value="">All Statuses</option>
            <option value="active">Active</option>
            <option value="resolved">Resolved</option>
            <option value="invalidated">Invalidated</option>
          </select>
          <button
            onClick={loadData}
            className="flex items-center space-x-1 px-3 py-1.5 bg-slate-800 text-white rounded-md text-xs hover:bg-slate-700 font-medium"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Universe Health Posture Banner */}
      {matrix && (
        <div className="bg-gradient-to-r from-slate-900 via-indigo-950 to-slate-900 text-white rounded-xl p-5 shadow-sm border border-indigo-900 flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center space-x-4">
            <div className="w-12 h-12 rounded-lg bg-indigo-500/20 border border-indigo-500/40 flex items-center justify-center">
              <CheckCircle2 className="w-6 h-6 text-emerald-400" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <span className="text-xs font-semibold tracking-wider uppercase text-indigo-300">
                  Global Ecosystem Health Index
                </span>
                <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 uppercase">
                  {matrix.overall_posture}
                </span>
              </div>
              <div className="text-2xl font-black tracking-tight">{matrix.ecosystem_health_score} / 100</div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-6 text-xs text-slate-300">
            <div>
              <div className="text-slate-400 uppercase text-[10px] font-semibold">Subsystems Active</div>
              <div className="text-sm font-bold text-white font-mono">{matrix.total_domains} / 9 Active</div>
            </div>
            <div>
              <div className="text-slate-400 uppercase text-[10px] font-semibold">Tracked Metrics</div>
              <div className="text-sm font-bold text-white font-mono">{matrix.total_active_targets} Operational</div>
            </div>
            <div>
              <div className="text-slate-400 uppercase text-[10px] font-semibold">IntelX Grounding</div>
              <div className="text-sm font-bold text-emerald-400 font-mono">ONLINE (Exogenous)</div>
            </div>
          </div>
        </div>
      )}

      {/* Domain Category Filter Tabs */}
      <div className="flex items-center space-x-2 overflow-x-auto pb-2 border-b border-slate-200">
        {domainTabs.map((tab) => {
          const Icon = tab.icon;
          const isSelected = selectedDomain === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setSelectedDomain(tab.id)}
              className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition-all ${
                isSelected
                  ? 'bg-indigo-600 text-white shadow-sm'
                  : 'bg-white text-slate-600 hover:bg-slate-100 border border-slate-200'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              <span>{tab.name}</span>
            </button>
          );
        })}
      </div>

      {/* View Options Bar */}
      <div className="flex items-center justify-between text-xs text-slate-500">
        <div className="flex items-center space-x-3">
          <label className="flex items-center space-x-1.5 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={latestOnly}
              onChange={(e) => setLatestOnly(e.target.checked)}
              className="rounded text-indigo-600 focus:ring-indigo-500 h-3.5 w-3.5"
            />
            <span className="font-medium text-slate-700">Latest Run Per Target Only (Deduplicate)</span>
          </label>
        </div>
        <div>Showing {displayedForecasts.length} projections</div>
      </div>

      {error && (
        <div className="p-4 bg-rose-50 border border-rose-200 rounded-md flex items-center space-x-2 text-rose-700 text-sm">
          <AlertCircle className="w-5 h-5 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Main Table */}
      {loading ? (
        <div className="p-12 text-center text-slate-400 font-mono text-sm">
          Loading universe forecasts across all 9 domains...
        </div>
      ) : displayedForecasts.length === 0 ? (
        <div className="p-12 text-center bg-white border border-dashed border-slate-300 rounded-lg text-slate-500 space-y-4">
          <div className="max-w-md mx-auto space-y-1">
            <h3 className="text-base font-semibold text-slate-800">No forecasts found for this filter</h3>
            <p className="text-xs text-slate-500">
              Initialize benchmark forecasts across all 9 FRIDAY Universe subsystems to populate the workspace.
            </p>
          </div>
          <div className="flex justify-center items-center gap-3">
            <button
              onClick={handleRefreshUniverse}
              disabled={generating}
              className="flex items-center space-x-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-md text-xs font-semibold shadow-sm disabled:opacity-50"
            >
              <Zap className={`w-3.5 h-3.5 ${generating ? 'animate-bounce' : ''}`} />
              <span>{generating ? 'Orchestrating...' : 'Generate Full Universe Matrix'}</span>
            </button>
            <button
              onClick={handleQuickSeed}
              disabled={generating}
              className="flex items-center space-x-2 px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-300 rounded-md text-xs font-semibold disabled:opacity-50"
            >
              <Sparkles className="w-3.5 h-3.5 text-amber-500" />
              <span>Seed 9-Agent Benchmark</span>
            </button>
          </div>
        </div>
      ) : (
        <div className="overflow-x-auto bg-white border border-slate-200 rounded-lg shadow-sm">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50 text-slate-600 font-medium text-xs">
              <tr>
                <th className="px-4 py-3 text-left">Target / Subsystem</th>
                <th className="px-4 py-3 text-left">
                  <div className="flex items-center space-x-1" title="Calibrated central point estimate">
                    <span>Point Prediction</span>
                    <HelpCircle className="w-3.5 h-3.5 text-slate-400" />
                  </div>
                </th>
                <th className="px-4 py-3 text-left">Uncertainty Range</th>
                <th className="px-4 py-3 text-left">
                  <div className="flex items-center space-x-1" title="Calibrated probability of event occurrence">
                    <span className="text-indigo-600 font-semibold">Probability</span>
                    <HelpCircle className="w-3.5 h-3.5 text-indigo-400" />
                  </div>
                </th>
                <th className="px-4 py-3 text-left">
                  <div className="flex items-center space-x-1" title="Meta-confidence metric">
                    <span className="text-emerald-600 font-semibold">Meta-Confidence</span>
                    <HelpCircle className="w-3.5 h-3.5 text-emerald-400" />
                  </div>
                </th>
                <th className="px-4 py-3 text-left">Status</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {displayedForecasts.map((f) => {
                const info = getDomainInfo(f.target);
                return (
                  <tr key={f.forecast_id} className="hover:bg-slate-50 transition-colors">
                    <td className="px-4 py-3">
                      <div className="space-y-1">
                        <div className="flex items-center space-x-2">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider border font-mono ${info.style}`}
                          >
                            {info.agent}
                          </span>
                          <span className="font-semibold text-slate-900 text-xs">{info.label}</span>
                        </div>
                        <div className="font-mono text-[11px] text-slate-500 truncate max-w-xs md:max-w-md" title={f.target}>
                          {f.target}
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 font-semibold text-slate-800 text-xs">
                      {formatPrediction(f.target, f.prediction)}
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500 font-mono">
                      {formatRange(f.target, f.range.lower, f.range.upper)}
                    </td>
                    <td className="px-4 py-3">{renderProbabilityBar(f.probability)}</td>
                    <td className="px-4 py-3">{renderConfidenceBadge(f.confidence)}</td>
                    <td className="px-4 py-3">
                      <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-slate-100 text-slate-700 capitalize font-medium">
                        {f.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Link
                        to={`/forecasts/${f.forecast_id}`}
                        className="text-indigo-600 hover:text-indigo-900 font-medium text-xs"
                      >
                        View Details →
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};