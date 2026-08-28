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

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <Link to="/" className="inline-flex items-center space-x-1 text-sm text-slate-500 hover:text-slate-800">
        <ArrowLeft className="w-4 h-4" />
        <span>Back to Workspaces</span>
      </Link>

      {/* Header Summary */}
      <div className="bg-white p-6 rounded-lg border border-slate-200 shadow-sm flex items-center justify-between">
        <div>
          <div className="flex items-center space-x-3">
            <h1 className="text-xl font-bold text-slate-900 font-mono">{forecast.target}</h1>
            <span className="px-2 py-0.5 rounded text-xs uppercase font-semibold bg-emerald-100 text-emerald-800">
              {forecast.status}
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-1 font-mono">Forecast ID: {forecast.forecast_id}</p>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold text-slate-900">{forecast.prediction.toFixed(1)} rpm</div>
          <div className="text-xs text-slate-500 font-mono">
            Range: [{forecast.range.lower.toFixed(0)} - {forecast.range.upper.toFixed(0)}]
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
          <p className="text-xs text-indigo-700 mt-1">
            Plain language: ~{Math.round(probPct / 10)} in 10 chance of demand exceeding capacity envelope.
          </p>
        </div>

        <div className="bg-emerald-50 p-5 rounded-lg border border-emerald-200">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-emerald-900 uppercase tracking-wider">Meta-Confidence</span>
            <CheckCircle className="w-4 h-4 text-emerald-600" />
          </div>
          <div className="text-3xl font-extrabold text-emerald-950 mt-2 uppercase">{forecast.confidence}</div>
          <p className="text-xs text-emerald-700 mt-1">
            Independent calibration metric: Model shows high empirical historical reliability.
          </p>
        </div>
      </div>

      {/* Explanatory Drivers */}
      <div className="bg-white p-6 rounded-lg border border-slate-200 shadow-sm space-y-4">
        <h2 className="text-md font-bold text-slate-900 flex items-center space-x-2">
          <Cpu className="w-4 h-4 text-slate-600" />
          <span>Explanatory Feature Drivers</span>
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
          {forecast.drivers.map((d, i) => (
            <div key={i} className="p-3 bg-slate-50 border border-slate-200 rounded-md">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-slate-800">{d.name}</span>
                <span className="text-slate-500 uppercase font-mono text-[10px]">{d.leading_or_lagging}</span>
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
          <span>Advisory Decision Recommendations</span>
        </div>
        <p className="text-xs text-amber-800 leading-relaxed">
          Prediction does not equal automated action authorization. All high-impact scaling and shedding mitigations
          strictly require human governance approval prior to dispatch.
        </p>
      </div>
    </div>
  );
};