import React, { useEffect, useState } from 'react';
import { fetchPeers, triggerForecast, triggerDemoSeed, triggerMarketForecast, requestUniversePrediction } from '../api/client';
import { EcosystemOverview, PeerAgent } from '../api/types';
import { Radio, CheckCircle, AlertTriangle, XCircle, RefreshCw, Zap, Server, Activity, ShieldCheck, Shield, Cpu, Terminal, Database } from 'lucide-react';

export const EcosystemPage: React.FC = () => {
  const [data, setData] = useState<EcosystemOverview | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState<boolean>(false);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const overview = await fetchPeers();
      setData(overview);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load ecosystem peers');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleGenerateCheckoutForecast = async () => {
    setActionBusy(true);
    setActionMsg(null);
    try {
      const fc = await triggerForecast('service:checkout:capacity_exceedance_24h', '24h');
      setActionMsg(`Successfully generated forecast ${fc.forecast_id} with IntelX research context!`);
    } catch (err: unknown) {
      setActionMsg(err instanceof Error ? err.message : 'Failed to generate forecast');
    } finally {
      setActionBusy(false);
    }
  };

  const handleGenerateTradingForecast = async () => {
    setActionBusy(true);
    setActionMsg(null);
    try {
      const fc = await triggerMarketForecast('BTCUSDT');
      setActionMsg(`Successfully generated BTCUSDT market forecast! Regime: ${fc.regime_outlook.current} (${fc.regime_outlook.predicted_direction}), Volatility Prob: ${(fc.volatility_forecast.probability * 100).toFixed(1)}%, IntelX: ${fc.intelx_context_included ? 'ONLINE' : 'FALLBACK'}, Stratex Dispatch: SENT`);
    } catch (err: unknown) {
      setActionMsg(err instanceof Error ? err.message : 'Failed to generate market forecast');
    } finally {
      setActionBusy(false);
    }
  };

  const handleSeed = async () => {
    setActionBusy(true);
    setActionMsg(null);
    try {
      const res = await triggerDemoSeed();
      setActionMsg(res.message || 'Seeded 180 days of workspace data successfully.');
    } catch (err: unknown) {
      setActionMsg(err instanceof Error ? err.message : 'Seeding failed');
    } finally {
      setActionBusy(false);
    }
  };

  const handlePredictUniverse = async (target: string, label: string) => {
    setActionBusy(true);
    setActionMsg(null);
    try {
      const res = await requestUniversePrediction(target);
      setActionMsg(`Generated ${label} prediction! Risk: ${res.risk_level}, Projected: ${res.point_prediction.toFixed(1)} ${res.unit} (Confidence: ${res.confidence})`);
    } catch (err: unknown) {
      setActionMsg(err instanceof Error ? err.message : `Failed to predict ${label}`);
    } finally {
      setActionBusy(false);
    }
  };

  const getStatusBadge = (status: PeerAgent['status']) => {
    switch (status) {
      case 'online':
        return (
          <span className="flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 border border-emerald-300">
            <CheckCircle className="w-3 h-3 text-emerald-600" />
            <span>ONLINE</span>
          </span>
        );
      case 'degraded':
        return (
          <span className="flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300">
            <AlertTriangle className="w-3 h-3 text-amber-600" />
            <span>DEGRADED</span>
          </span>
        );
      default:
        return (
          <span className="flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-100 text-rose-800 border border-rose-300">
            <XCircle className="w-3 h-3 text-rose-600" />
            <span>OFFLINE</span>
          </span>
        );
    }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 flex items-center space-x-2">
            <Radio className="w-6 h-6 text-indigo-600 animate-pulse" />
            <span>FRIDAY Universe Ecosystem Integration</span>
          </h1>
          <p className="text-sm text-slate-500">
            Live multi-agent orchestration fabric: exogenous research, multi-model reasoning, shared memory, and algorithmic trading.
          </p>
        </div>
        <div className="flex items-center space-x-3">
          <button
            onClick={loadData}
            disabled={loading}
            className="flex items-center space-x-1 px-3 py-1.5 bg-slate-800 text-white rounded-md text-sm hover:bg-slate-700 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            <span>Probe Health</span>
          </button>
        </div>
      </div>

      {actionMsg && (
        <div className="p-4 bg-indigo-50 border border-indigo-200 rounded-md text-sm text-indigo-900 flex items-center justify-between">
          <span>{actionMsg}</span>
          <button onClick={() => setActionMsg(null)} className="text-xs text-indigo-500 hover:text-indigo-800">Dismiss</button>
        </div>
      )}

      {error && (
        <div className="p-4 bg-rose-50 border border-rose-200 rounded-md text-sm text-rose-800 flex items-center space-x-2">
          <AlertTriangle className="w-5 h-5 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Quick Action Hub */}
      <div className="bg-white border border-slate-200 rounded-lg p-5 shadow-sm space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-500 flex items-center space-x-2">
          <Zap className="w-4 h-4 text-amber-500" />
          <span>Live Cross-Agent Actions</span>
        </h2>
        <div className="flex flex-wrap gap-3">
          <button
            onClick={handleGenerateCheckoutForecast}
            disabled={actionBusy}
            className="flex items-center space-x-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-md text-xs font-semibold shadow-sm disabled:opacity-50"
          >
            <Activity className="w-4 h-4" />
            <span>Generate Checkout Capacity Forecast (IntelX + Inference)</span>
          </button>
          <button
            onClick={handleGenerateTradingForecast}
            disabled={actionBusy}
            className="flex items-center space-x-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-md text-xs font-semibold shadow-sm disabled:opacity-50"
          >
            <Server className="w-4 h-4" />
            <span>Generate Trading Volatility Forecast (Stratex + Memora)</span>
          </button>
          <button
            onClick={() => handlePredictUniverse('sentinel:security:threat_anomaly_risk_24h', 'Sentinel Threat Anomaly')}
            disabled={actionBusy}
            className="flex items-center space-x-2 px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-md text-xs font-semibold shadow-sm disabled:opacity-50"
          >
            <Shield className="w-4 h-4" />
            <span>Sentinel Threat Risk</span>
          </button>
          <button
            onClick={() => handlePredictUniverse('cortex:execution:sla_breach_probability_24h', 'Cortex Task SLA')}
            disabled={actionBusy}
            className="flex items-center space-x-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-md text-xs font-semibold shadow-sm disabled:opacity-50"
          >
            <Cpu className="w-4 h-4" />
            <span>Cortex SLA Risk</span>
          </button>
          <button
            onClick={() => handlePredictUniverse('forge:ci_cd:pipeline_failure_risk_24h', 'Forge CI/CD Pipeline')}
            disabled={actionBusy}
            className="flex items-center space-x-2 px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white rounded-md text-xs font-semibold shadow-sm disabled:opacity-50"
          >
            <Terminal className="w-4 h-4" />
            <span>Forge CI/CD Risk</span>
          </button>
          <button
            onClick={() => handlePredictUniverse('memora:storage:capacity_exhaustion_days', 'Memora Storage Horizon')}
            disabled={actionBusy}
            className="flex items-center space-x-2 px-4 py-2 bg-cyan-700 hover:bg-cyan-800 text-white rounded-md text-xs font-semibold shadow-sm disabled:opacity-50"
          >
            <Database className="w-4 h-4" />
            <span>Memora Storage Horizon</span>
          </button>
          <button
            onClick={() => handlePredictUniverse('inference:gpu:vram_oom_probability_24h', 'Inference GPU VRAM')}
            disabled={actionBusy}
            className="flex items-center space-x-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-md text-xs font-semibold shadow-sm disabled:opacity-50"
          >
            <Zap className="w-4 h-4" />
            <span>Inference GPU OOM Risk</span>
          </button>
          <button
            onClick={handleSeed}
            disabled={actionBusy}
            className="flex items-center space-x-2 px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-300 rounded-md text-xs font-semibold disabled:opacity-50"
          >
            <ShieldCheck className="w-4 h-4 text-slate-500" />
            <span>Populate 9-Agent Matrix Workspace</span>
          </button>
        </div>
      </div>

      {/* Peer Agent Cards Grid */}
      {loading && !data ? (
        <div className="p-12 text-center text-slate-400 font-mono text-sm">Probing all FRIDAY Universe microservices...</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {data?.peers.map((peer) => (
            <div
              key={peer.name}
              className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm hover:shadow transition-shadow flex flex-col justify-between space-y-4"
            >
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="font-bold text-base text-slate-900 tracking-wide font-mono uppercase">
                    {peer.name}
                  </div>
                  {getStatusBadge(peer.status)}
                </div>
                <p className="text-xs text-slate-500 font-medium">{peer.role}</p>
                <div className="text-xs font-mono text-slate-400 truncate bg-slate-50 px-2 py-1 rounded border border-slate-100">
                  {peer.url}
                </div>
              </div>

              <div className="space-y-2 pt-2 border-t border-slate-100">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-slate-400">Roundtrip Latency:</span>
                  <span className="font-mono font-semibold text-slate-700">
                    {peer.latency_ms !== null ? `${peer.latency_ms.toFixed(1)} ms` : 'N/A'}
                  </span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-slate-400">Last Synced:</span>
                  <span className="font-mono text-slate-500 text-[11px] truncate max-w-[160px]">
                    {peer.last_interaction}
                  </span>
                </div>
                <div className="space-y-1">
                  <div className="text-[11px] text-slate-400 uppercase tracking-wider font-semibold">Active Capabilities:</div>
                  <div className="flex flex-wrap gap-1">
                    {peer.capabilities.map((c) => (
                      <span key={c} className="px-2 py-0.5 bg-slate-100 text-slate-600 rounded text-[10px] font-mono">
                        {c}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
