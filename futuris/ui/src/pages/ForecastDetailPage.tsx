import React, { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { fetchForecastById } from '../api/client';
import { Forecast } from '../api/types';
import { ArrowLeft, CheckCircle, ShieldAlert, Activity, Cpu } from 'lucide-react';

export const ForecastDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    const load = async () => {
      try {
        const data = await fetchForecastById(id);
        setForecast(data);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : 'Failed to load forecast');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [id]);

  if (loading) return <div className="p-12 text-center text-slate-400 font-mono text-sm">Loading forecast details...</div>;
  if (error || !forecast) return <div className="p-12 text-center text-rose-500 font-mono text-sm">Error: {error || 'Forecast not found'}</div>;

  const probPct = Math.round((forecast.probability || 0) * 100);

  // Dynamic unit formatting
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

  const getDomainLabel = (target: string): string => {
    const t = target.toLowerCase();
    if (t.startsWith('sentinel')) return 'SENTINEL (Cybersecurity & Governance)';
    if (t.startsWith('cortex')) return 'CORTEX (Cognitive Reasoning & Task Planning)';
    if (t.startsWith('forge')) return 'FORGE (Autonomous CI/CD & Deployment)';
    if (t.startsWith('memora')) return 'MEMORA (Persistent Associative Memory)';
    if (t.startsWith('inference')) return 'INFERENCE GATEWAY (GPU & Token Serving)';
    if (t.startsWith('intelx')) return 'INTELX (Deep Research & Exogenous Intelligence)';
    if (t.startsWith('market') || t.includes('crypto') || t.startsWith('stratex')) return 'STRATEX (Algorithmic Trading Execution)';
    if (t.startsWith('friday')) return 'FRIDAY CORE (Desktop OS & Ecosystem Orchestration)';
    return 'OPERATIONAL INFRASTRUCTURE';
  };

  const getPlainExplanation = (target: string, prob: number): string => {
    const t = target.toLowerCase();
    const ratio = `~${Math.round(prob / 10)} in 10 chance`;
    if (t.includes('threat')) return `Plain language: ${ratio} of detecting unauthorized security anomaly activity.`;
    if (t.includes('ratelimit')) return `Plain language: ${ratio} of public or gateway endpoints experiencing API rate limit exhaustion.`;
    if (t.includes('sla')) return `Plain language: ${ratio} of cognitive task execution exceeding timeout SLA.`;
    if (t.includes('thrashing')) return `Plain language: ${ratio} of subagent recursion deadlock or context thrashing.`;
    if (t.includes('pipeline')) return `Plain language: ${ratio} of CI/CD build suite failure on pending pull requests.`;
    if (t.includes('regression')) return `Plain language: ${ratio} of production release regression requiring canary rollback.`;
    if (t.includes('storage') || t.includes('exhaustion')) return `Plain language: Storage projected to reach operational capacity limit in ~${forecast.prediction.toFixed(1)} days.`;
    if (t.includes('latency')) return `Plain language: ${ratio} of vector similarity search queries exceeding 200ms latency threshold.`;
    if (t.includes('oom')) return `Plain language: ${ratio} of GPU VRAM allocation exhaustion under peak reasoning load.`;
    if (t.includes('queue') || t.includes('starvation')) return `Plain language: ${ratio} of worker queues exceeding 5-second wait time SLA.`;
    if (t.includes('volatility') || t.includes('btcusdt') || t.includes('ethusdt')) return `Plain language: ${ratio} of market experiencing acute price volatility spike over 24h.`;
    if (t.includes('drawdown')) return `Plain language: ${ratio} of trading portfolio suffering drawdown exceeding 5% threshold.`;
    if (t.includes('health')) return `Plain language: Ecosystem global health projected at nominal ${forecast.prediction.toFixed(1)} / 100 score.`;
    return `Plain language: ${ratio} of demand exceeding capacity envelope.`;
  };

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <Link to="/" className="inline-flex items-center space-x-1 text-sm text-slate-500 hover:text-slate-800">
        <ArrowLeft className="w-4 h-4" />
        <span>Back to Universe Workspace</span>
      </Link>

      {/* Header Summary */}
      <div className="bg-white p-6 rounded-lg border border-slate-200 shadow-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="text-xs font-bold text-indigo-600 font-mono tracking-wider uppercase">
            {getDomainLabel(forecast.target)}
          </div>
          <div className="flex items-center space-x-3 mt-1">
            <h1 className="text-xl font-bold text-slate-900 font-mono">{forecast.target}</h1>
            <span className="px-2 py-0.5 rounded text-xs uppercase font-semibold bg-emerald-100 text-emerald-800">
              {forecast.status}
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-1 font-mono">Forecast ID: {forecast.forecast_id}</p>
        </div>
        <div className="text-left md:text-right">
          <div className="text-2xl font-bold text-slate-900">
            {formatPrediction(forecast.target, forecast.prediction)}
          </div>
          <div className="text-xs text-slate-500 font-mono">
            Range: {formatRange(forecast.target, forecast.range.lower, forecast.range.upper)}
          </div>
        </div>
      </div>

      {/* Probability vs Confidence Interpretation Banner */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-indigo-50 p-5 rounded-lg border border-indigo-200">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-indigo-900 uppercase tracking-wider">Event Likelihood</span>
            <Activity className="w-4 h-4 text-indigo-600" />
          </div>
          <div className="text-3xl font-extrabold text-indigo-950 mt-2">{probPct}% Chance</div>
          <p className="text-xs text-indigo-700 mt-1">{getPlainExplanation(forecast.target, probPct)}</p>
        </div>

        <div className="bg-emerald-50 p-5 rounded-lg border border-emerald-200">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-emerald-900 uppercase tracking-wider">Meta-Confidence</span>
            <CheckCircle className="w-4 h-4 text-emerald-600" />
          </div>
          <div className="text-3xl font-extrabold text-emerald-950 mt-2 uppercase">{forecast.confidence}</div>
          <p className="text-xs text-emerald-700 mt-1">
            Independent calibration metric: Model shows high empirical historical reliability across FRIDAY Universe.
          </p>
        </div>
      </div>

      {/* Explanatory Drivers */}
      <div className="bg-white p-6 rounded-lg border border-slate-200 shadow-sm space-y-4">
        <h2 className="text-md font-bold text-slate-900 flex items-center space-x-2">
          <Cpu className="w-4 h-4 text-slate-600" />
          <span>Causal Feature Drivers & Research Anchors</span>
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
          {forecast.drivers.map((d, i) => (
            <div key={i} className="p-3 bg-slate-50 border border-slate-200 rounded-md">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-slate-800 truncate" title={d.name}>
                  {d.name}
                </span>
                <span className="text-slate-500 uppercase font-mono text-[10px] ml-1 flex-shrink-0">
                  {d.leading_or_lagging}
                </span>
              </div>
              <div className="mt-2 flex items-center justify-between text-xs text-slate-600 font-mono">
                <span>Impact: {d.direction}</span>
                <span>Strength: {(d.strength * 100).toFixed(0)}%</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Decision Support Advisory */}
      <div className="bg-amber-50 p-6 rounded-lg border border-amber-200 space-y-3">
        <div className="flex items-center space-x-2 text-amber-900 font-bold text-sm">
          <ShieldAlert className="w-4 h-4 text-amber-700" />
          <span>Operational Advisory & Governance Guardrail</span>
        </div>
        <p className="text-xs text-amber-800 leading-relaxed">
          Prediction does not equal automated action authorization. All high-impact scaling, hedging, rollback, and
          subagent termination mitigations strictly require human governance approval prior to dispatch.
        </p>
      </div>
    </div>
  );
};