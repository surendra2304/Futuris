import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchForecasts } from '../api/client';
import { Forecast, ConfidenceLevel } from '../api/types';
import { HelpCircle, AlertCircle, RefreshCw } from 'lucide-react';

export const ForecastListPage: React.FC = () => {
  const [forecasts, setForecasts] = useState<Forecast[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState<string>('');

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchForecasts(undefined, filterStatus || undefined);
      setForecasts(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load forecasts');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [filterStatus]);

  const renderConfidenceBadge = (level: ConfidenceLevel) => {
    const styles = {
      high: 'bg-emerald-100 text-emerald-800 border-emerald-300',
      medium: 'bg-amber-100 text-amber-800 border-amber-300',
      low: 'bg-rose-100 text-rose-800 border-rose-300',
    };
    return (
      <span className={`px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wider border ${styles[level]}`}>
        {level}
      </span>
    );
  };

  const renderProbabilityBar = (prob: number | null) => {
    if (prob === null) return <span className="text-slate-400">N/A</span>;
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

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Forecast Workspace</h1>
          <p className="text-sm text-slate-500">Operational capacity predictions with point-in-time calibration</p>
        </div>
        <div className="flex items-center space-x-3">
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="px-3 py-1.5 border border-slate-300 rounded-md text-sm bg-white"
          >
            <option value="">All Statuses</option>
            <option value="active">Active</option>
            <option value="resolved">Resolved</option>
            <option value="invalidated">Invalidated</option>
          </select>
          <button
            onClick={loadData}
            className="flex items-center space-x-1 px-3 py-1.5 bg-slate-800 text-white rounded-md text-sm hover:bg-slate-700"
          >
            <RefreshCw className="w-4 h-4" />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {error && (
        <div className="p-4 bg-rose-50 border border-rose-200 rounded-md flex items-center space-x-2 text-rose-700 text-sm">
          <AlertCircle className="w-5 h-5 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {loading ? (
        <div className="p-12 text-center text-slate-400 font-mono text-sm">Loading active forecasts...</div>
      ) : forecasts.length === 0 ? (
        <div className="p-12 text-center bg-white border border-dashed border-slate-300 rounded-lg text-slate-500">
          No forecasts found matching criteria.
        </div>
      ) : (
        <div className="overflow-x-auto bg-white border border-slate-200 rounded-lg shadow-sm">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50 text-slate-600 font-medium">
              <tr>
                <th className="px-4 py-3 text-left">Target Metric</th>
                <th className="px-4 py-3 text-left">
                  <div className="flex items-center space-x-1" title="Central demand projection in rpm">
                    <span>Point Prediction</span>
                    <HelpCircle className="w-3.5 h-3.5 text-slate-400" />
                  </div>
                </th>
                <th className="px-4 py-3 text-left">Uncertainty Range</th>
                <th className="px-4 py-3 text-left">
                  <div className="flex items-center space-x-1" title="Event likelihood of capacity exceedance">
                    <span className="text-indigo-600 font-semibold">Probability</span>
                    <HelpCircle className="w-3.5 h-3.5 text-indigo-400" />
                  </div>
                </th>
                <th className="px-4 py-3 text-left">
                  <div className="flex items-center space-x-1" title="Independent measurement of calibration accuracy">
                    <span className="text-emerald-600 font-semibold">Meta-Confidence</span>
                    <HelpCircle className="w-3.5 h-3.5 text-emerald-400" />
                  </div>
                </th>
                <th className="px-4 py-3 text-left">Status</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {forecasts.map((f) => (
                <tr key={f.forecast_id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-3 font-mono text-xs text-slate-900">{f.target}</td>
                  <td className="px-4 py-3 font-medium text-slate-800">{f.prediction.toFixed(1)} rpm</td>
                  <td className="px-4 py-3 text-xs text-slate-500 font-mono">
                    [{f.range.lower.toFixed(0)}, {f.range.upper.toFixed(0)}]
                  </td>
                  <td className="px-4 py-3">{renderProbabilityBar(f.probability)}</td>
                  <td className="px-4 py-3">{renderConfidenceBadge(f.confidence)}</td>
                  <td className="px-4 py-3">
                    <span className="px-2 py-0.5 rounded text-xs font-mono bg-slate-100 text-slate-700 capitalize">
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
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};