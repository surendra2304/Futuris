import React from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import { ForecastListPage } from './pages/ForecastListPage';
import { ForecastDetailPage } from './pages/ForecastDetailPage';
import { CalibrationPage } from './pages/CalibrationPage';
import { OutcomesPage } from './pages/OutcomesPage';
import { SubscriptionsPage } from './pages/SubscriptionsPage';
import { Layers, Activity, CheckSquare, Bell } from 'lucide-react';

const Navigation: React.FC = () => {
  const location = useLocation();
  const navItems = [
    { path: '/', label: 'Forecasts', icon: Layers },
    { path: '/calibration', label: 'Calibration', icon: Activity },
    { path: '/outcomes', label: 'Outcomes', icon: CheckSquare },
    { path: '/subscriptions', label: 'Subscriptions', icon: Bell },
  ];

  return (
    <nav className="bg-slate-900 border-b border-slate-800 text-slate-300">
      <div className="max-w-7xl mx-auto px-6 h-14 flex items-center justify-between">
        <div className="flex items-center space-x-8">
          <Link to="/" className="flex items-center space-x-2 text-white font-bold text-lg tracking-wider">
            <span className="text-emerald-400 font-mono">FUTURIS</span>
            <span className="text-xs text-slate-500 font-normal border border-slate-700 px-1.5 py-0.5 rounded">v0.1.0</span>
          </Link>
          <div className="flex space-x-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              const active = location.pathname === item.path;
              return (
                <Link
                  key={item.path}
                  to={item.path}
                  className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                    active ? 'bg-slate-800 text-emerald-400' : 'hover:bg-slate-800/60 text-slate-400'
                  }`}
                >
                  <Icon className="w-3.5 h-3.5" />
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </div>
        </div>
      </div>
    </nav>
  );
};

export const App: React.FC = () => {
  return (
    <BrowserRouter basename="/ui">
      <div className="min-h-screen bg-slate-50 text-slate-800 font-sans">
        <Navigation />
        <main>
          <Routes>
            <Route path="/" element={<ForecastListPage />} />
            <Route path="/forecasts/:id" element={<ForecastDetailPage />} />
            <Route path="/calibration" element={<CalibrationPage />} />
            <Route path="/outcomes" element={<OutcomesPage />} />
            <Route path="/subscriptions" element={<SubscriptionsPage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
};

export default App;