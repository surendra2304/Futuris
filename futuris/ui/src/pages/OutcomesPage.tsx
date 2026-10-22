import React from 'react';
import { CheckCircle2 } from 'lucide-react';

export const OutcomesPage: React.FC = () => {
  const outcomes = [
    {
      id: 'out-1',
      target: 'service:checkout:capacity_exceedance_24h',
      observed: 4120.0,
      predicted_prob: 0.75,
      occurred: true,
      correct: true,
      method: 'automatic',
      date: '2026-08-28 14:00 UTC',
    },
    {
      id: 'out-2',
      target: 'service:payments:capacity_exceedance_24h',
      observed: 2800.0,
      predicted_prob: 0.20,
      occurred: false,
      correct: true,
      method: 'automatic',
      date: '2026-08-27 12:00 UTC',
    },
  ];

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Outcomes Resolution Scoreboard</h1>
        <p className="text-sm text-slate-500">Ground-truth verification against immutable evidence snapshots</p>
      </div>

      <div className="bg-white border border-slate-200 rounded-lg shadow-sm overflow-hidden">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50 text-slate-600 font-medium">
            <tr>
              <th className="px-4 py-3 text-left">Target</th>
              <th className="px-4 py-3 text-left">Observed Peak</th>
              <th className="px-4 py-3 text-left">Predicted Prob</th>
              <th className="px-4 py-3 text-left">Exceedance Realized</th>
              <th className="px-4 py-3 text-left">Directional Call</th>
              <th className="px-4 py-3 text-left">Method</th>
              <th className="px-4 py-3 text-right">Resolved At</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200">
            {outcomes.map((o) => (
              <tr key={o.id} className="hover:bg-slate-50">
                <td className="px-4 py-3 font-mono text-xs text-slate-900">{o.target}</td>
                <td className="px-4 py-3 font-medium text-slate-800">{o.observed.toFixed(1)} rpm</td>
                <td className="px-4 py-3 font-mono text-xs">{(o.predicted_prob * 100).toFixed(0)}%</td>
                <td className="px-4 py-3">
                  {o.occurred ? (
                    <span className="px-2 py-0.5 rounded text-xs font-semibold bg-rose-100 text-rose-800">Exceeded</span>
                  ) : (
                    <span className="px-2 py-0.5 rounded text-xs font-semibold bg-emerald-100 text-emerald-800">Within Cap</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center space-x-1.5">
                    <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                    <span className="text-xs font-medium text-slate-700">{o.correct ? 'Correct' : 'Incorrect'}</span>
                  </div>
                </td>
                <td className="px-4 py-3 font-mono text-xs uppercase text-slate-500">{o.method}</td>
                <td className="px-4 py-3 text-right text-xs text-slate-400 font-mono">{o.date}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};