import React, { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';

export const SubscriptionsPage: React.FC = () => {
  const [subs] = useState([
    {
      target: 'service:checkout:capacity_exceedance_24h',
      horizon: '24h',
      refresh_min: 60,
      delta_prob: 5,
      delta_pred: 50,
      enabled: true,
    },
    {
      target: 'service:payments:capacity_exceedance_24h',
      horizon: '24h',
      refresh_min: 60,
      delta_prob: 5,
      delta_pred: 50,
      enabled: true,
    },
  ]);

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Forecast Subscriptions</h1>
          <p className="text-sm text-slate-500">Autonomous scheduled refresh configurations and noise suppression limits</p>
        </div>
        <button className="flex items-center space-x-1.5 px-3 py-1.5 bg-indigo-600 text-white rounded-md text-sm hover:bg-indigo-700 font-medium">
          <Plus className="w-4 h-4" />
          <span>New Subscription</span>
        </button>
      </div>

      <div className="bg-white border border-slate-200 rounded-lg shadow-sm overflow-hidden">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50 text-slate-600 font-medium">
            <tr>
              <th className="px-4 py-3 text-left">Target</th>
              <th className="px-4 py-3 text-left">Horizon</th>
              <th className="px-4 py-3 text-left">Interval</th>
              <th className="px-4 py-3 text-left">Noise Suppression Deltas</th>
              <th className="px-4 py-3 text-left">Status</th>
              <th className="px-4 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200">
            {subs.map((s, i) => (
              <tr key={i} className="hover:bg-slate-50">
                <td className="px-4 py-3 font-mono text-xs font-semibold text-slate-900">{s.target}</td>
                <td className="px-4 py-3 font-mono text-xs text-slate-600">{s.horizon}</td>
                <td className="px-4 py-3 text-xs text-slate-600">Every {s.refresh_min} min</td>
                <td className="px-4 py-3 text-xs text-slate-500 font-mono">
                  ΔProb ≥ {s.delta_prob}%, ΔPred ≥ {s.delta_pred} rpm
                </td>
                <td className="px-4 py-3">
                  <span className="px-2 py-0.5 rounded text-xs font-semibold bg-emerald-100 text-emerald-800">
                    Active
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  <button className="text-rose-600 hover:text-rose-800">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};