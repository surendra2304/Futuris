import React, { useEffect, useState } from 'react';
import { fetchCalibration } from '../api/client';
import { CalibrationCurve } from '../api/types';
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, BarChart, Bar } from 'recharts';

export const CalibrationPage: React.FC = () => {
  const [calibration, setCalibration] = useState<CalibrationCurve | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    const load = async () => {
      try {
        const calData = await fetchCalibration();
        setCalibration(calData);
      } catch (err: unknown) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  if (loading) return <div className="p-12 text-center text-slate-400 font-mono text-sm">Loading honesty metrics...</div>;

  const chartData = calibration?.bin_centers.map((c, i) => ({
    predicted: c,
    observed: calibration.observed_frequencies[i],
    count: calibration.bin_counts[i],
    ideal: c,
  })) || [];

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Calibration Honesty Dashboard</h1>
        <p className="text-sm text-slate-500">Empirical reliability diagrams, shrinkage adjustments, and model tracking</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Reliability Curve */}
        <div className="bg-white p-6 rounded-lg border border-slate-200 shadow-sm space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-slate-800">Reliability Curve (Calibration)</h2>
            <span className="text-xs font-mono font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded">
              ECE: {calibration?.expected_calibration_error.toFixed(4)}
            </span>
          </div>

          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                <XAxis dataKey="predicted" domain={[0, 1]} tick={{ fontSize: 11 }} label={{ value: 'Predicted Probability', position: 'insideBottom', offset: -5 }} />
                <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} label={{ value: 'Observed Frequency', angle: -90, position: 'insideLeft' }} />
                <Tooltip />
                <Line type="monotone" dataKey="ideal" stroke="#94a3b8" strokeDasharray="4 4" dot={false} name="Perfect Calibration" />
                <Line type="monotone" dataKey="observed" stroke="#4f46e5" strokeWidth={2} dot={{ r: 4 }} name="Model Realized" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Sample Bin Counts */}
        <div className="bg-white p-6 rounded-lg border border-slate-200 shadow-sm space-y-4">
          <h2 className="text-sm font-bold text-slate-800">Sample Counts per Probability Bin</h2>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                <XAxis dataKey="predicted" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="count" fill="#10b981" radius={[4, 4, 0, 0]} name="Sample Count" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
};