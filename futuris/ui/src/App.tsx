import { useCallback, useEffect, useState } from 'react';
import {
  Activity, ArrowDownRight, ArrowUpRight, Clock3, ExternalLink, Gauge,
  Layers3, Menu, Radio, RefreshCw, ShieldAlert, Sparkles,
} from 'lucide-react';
import { fetchForecasts, fetchHealth, fetchPeers } from './api/client';
import { countProvenance } from './utils/provenance';
import { EcosystemOverview, Forecast } from './api/types';

type Health = { status: string; version: string };
type LoadState<T> = { data: T | null; loading: boolean; error: string | null };

const initial = <T,>(): LoadState<T> => ({ data: null, loading: true, error: null });

const agentCopy: Record<string, string> = {
  Futuris: 'Forecasting & predictive intelligence',
};

function formatTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function EmptyPanel({ title, detail, icon: Icon = Activity }: { title: string; detail: string; icon?: typeof Activity }) {
  return <div className="empty-panel"><span className="empty-icon"><Icon size={18} /></span><div><strong>{title}</strong><p>{detail}</p></div></div>;
}

function App() {
  const [health, setHealth] = useState<LoadState<Health>>(initial);
  const [peers, setPeers] = useState<LoadState<EcosystemOverview>>(initial);
  const [forecasts, setForecasts] = useState<LoadState<Forecast[]>>(initial);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  const refresh = useCallback(async (manual = false) => {
    if (manual) setRefreshing(true);
    const results = await Promise.allSettled([fetchHealth(), fetchPeers(), fetchForecasts()]);
    const [healthResult, peerResult, forecastResult] = results;
    setHealth(healthResult.status === 'fulfilled'
      ? { data: healthResult.value, loading: false, error: null }
      : { data: null, loading: false, error: errorText(healthResult.reason) });
    setPeers(peerResult.status === 'fulfilled'
      ? { data: peerResult.value, loading: false, error: null }
      : { data: null, loading: false, error: errorText(peerResult.reason) });
    setForecasts(forecastResult.status === 'fulfilled'
      ? { data: forecastResult.value, loading: false, error: null }
      : { data: null, loading: false, error: errorText(forecastResult.reason) });
    setLastUpdated(new Date());
    setRefreshing(false);
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 45_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const peerList = peers.data?.peers ?? [];
  const services = [
    {
      name: 'Futuris', role: agentCopy.Futuris, status: health.data?.status === 'ok' ? 'online' : health.error ? 'unknown' : 'checking',
      latency: null as number | null, version: health.data?.version,
    },
    ...peerList.map((peer) => ({
      name: peer.name, role: peer.role, status: peer.status, latency: peer.latency_ms, version: undefined,
    })),
  ];
  const onlineCount = services.filter((item) => item.status === 'online').length;
  // The API labels every forecast with its evidence class: live/derived numbers
  // are measured or computed from persisted records; synthetic/demo numbers are
  // generated or caller-supplied. Verified = measured, not "withheld".
  const list = forecasts.data ?? [];
  const { measured: verifiedForecastCount, synthetic: syntheticCount } = countProvenance(list);

  return (
    <div className="console-shell">
      <aside className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`}>
        <div className="brand"><span className="brand-mark"><Sparkles size={17} /></span><span>FUTURIS</span><span className="brand-tag">INTELLIGENCE</span></div>
        <div className="workspace-label">FRIDAY UNIVERSE</div>
        <nav className="side-nav" aria-label="Main navigation">
          <a className="nav-item nav-active" href="#overview" onClick={() => setMenuOpen(false)}><Layers3 size={17} />Overview</a>
          <a className="nav-item" href="#agents" onClick={() => setMenuOpen(false)}><Radio size={17} />Agent network<span className="nav-count">9</span></a>
          <a className="nav-item" href="#predictions" onClick={() => setMenuOpen(false)}><Gauge size={17} />Predictions</a>
          <a className="nav-item" href="#research" onClick={() => setMenuOpen(false)}><ShieldAlert size={17} />IntelX research</a>
        </nav>
        <div className="sidebar-foot"><span className="side-dot" />Read-only console<span className="side-foot-muted">API-backed views</span></div>
      </aside>
      {menuOpen && <button aria-label="Close navigation" className="sidebar-scrim" onClick={() => setMenuOpen(false)} />}

      <main className="main-area" id="overview">
        <header className="topbar">
          <button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMenuOpen(true)}><Menu size={19} /></button>
          <div className="breadcrumb"><span>FRIDAY Universe</span><span className="crumb-slash">/</span><strong>Futuris</strong></div>
          <div className="topbar-right"><span className={`live-indicator api-${health.data?.status === 'ok' ? 'healthy' : health.loading ? 'checking' : 'unavailable'}`}><i />{health.data?.status === 'ok' ? 'API healthy' : health.loading ? 'Checking API' : 'API unavailable'}</span><button className="refresh-button" onClick={() => void refresh(true)} disabled={refreshing}><RefreshCw size={15} className={refreshing ? 'spin' : ''} /><span>{refreshing ? 'Refreshing' : 'Refresh'}</span></button></div>
        </header>

        <div className="page-content">
          <section className="welcome-row">
            <div><div className="eyebrow"><span className="eyebrow-line" />PREDICTIVE INTELLIGENCE</div><h1>Universe overview</h1><p>Live service checks and evidence-aware forecasting across the FRIDAY network.</p></div>
            <div className="updated-at"><Clock3 size={14} />Last checked {formatTime(lastUpdated?.toISOString())}</div>
          </section>

          {(health.error || peers.error || forecasts.error) && <div role="alert" className="error-banner"><Activity size={16} /><span>{[health.error && `Futuris: ${health.error}`, peers.error && `Agent probes: ${peers.error}`, forecasts.error && `Forecast records: ${forecasts.error}`].filter(Boolean).join(' · ')}</span><button onClick={() => void refresh(true)}>Retry</button></div>}

          <section className="metric-grid" aria-label="Current service summary">
            <article className="metric-card metric-primary"><div className="metric-top"><span>Services responding</span><span className="metric-icon"><Radio size={16} /></span></div><div className="metric-value">{peers.loading || health.loading || peers.error ? '—' : `${onlineCount}`}<small> / 9</small></div><div className="metric-foot"><span className="metric-status"><i />{peers.error ? 'Peer probe data unavailable' : peers.loading ? 'Probing services' : `${9 - onlineCount} not confirmed online`}</span><span className="metric-arrow"><ArrowUpRight size={15} /></span></div></article>
            <article className="metric-card"><div className="metric-top"><span>Verified forecasts</span><span className="metric-icon accent-blue"><Activity size={16} /></span></div><div className="metric-value">{forecasts.loading ? '—' : verifiedForecastCount}</div><div className="metric-foot"><span>Measured (live/derived) evidence class</span><ArrowDownRight className="muted-arrow" size={15} /></div></article>
            <article className="metric-card"><div className="metric-top"><span>Active forecast records</span><span className="metric-icon accent-violet"><Layers3 size={16} /></span></div><div className="metric-value">{forecasts.loading ? '—' : forecasts.data?.length ?? '—'}</div><div className="metric-foot"><span>{syntheticCount} synthetic/demo · labelled per record</span><span className="unverified-mark">Labelled</span></div></article>
            <article className="metric-card"><div className="metric-top"><span>IntelX feed</span><span className="metric-icon accent-amber"><ShieldAlert size={16} /></span></div><div className="metric-value metric-word">Not exposed</div><div className="metric-foot"><span>No read-only news route in Futuris API</span><ArrowDownRight className="muted-arrow" size={15} /></div></article>
          </section>

          <section className="content-grid">
            <article className="panel agent-panel" id="agents">
              <div className="panel-header"><div><div className="panel-kicker">NETWORK STATUS</div><h2>FRIDAY agents</h2></div><span className="probe-badge"><span />Probe snapshot</span></div>
              <p className="panel-description">Each status is from the latest health probe. “Unknown” means no successful response was available.</p>
              {peers.loading && !peers.data ? <div className="loading-state"><span className="loader" />Probing configured agent endpoints…</div> : peers.error && !peers.data ? <EmptyPanel title="Agent probes failed" detail={peers.error} icon={Radio} /> : (
                <div className="agent-list">
                  {services.map((service) => <div className="agent-row" key={service.name}>
                    <div className={`agent-monogram monogram-${service.name.toLowerCase()}`}>{service.name.slice(0, 1)}</div>
                    <div className="agent-copy"><div className="agent-title">{service.name}<span className={`service-pill pill-${service.status}`}>{service.status === 'unknown' || service.status === 'checking' ? service.status : service.status}</span></div><div className="agent-role">{service.role}</div></div>
                    <div className="agent-meta">{service.latency !== null ? <><span className="latency-value">{service.latency.toFixed(0)} ms</span><span className="latency-label">probe latency</span></> : service.version ? <><span className="latency-value">v{service.version}</span><span className="latency-label">API version</span></> : <><span className="latency-value">—</span><span className="latency-label">probe latency</span></>}</div>
                  </div>)}
                  {!peers.loading && peers.data?.peers.length === 0 && <EmptyPanel title="No peer probe results" detail="The ecosystem endpoint returned no peer services." icon={Radio} />}
                </div>
              )}
              <div className="panel-footer"><span>Futuris self-check plus peers returned by the ecosystem API</span><a href="/docs" target="_blank" rel="noreferrer">API docs <ExternalLink size={13} /></a></div>
            </article>

            <div className="right-column">
              <article className="panel prediction-panel" id="predictions">
                <div className="panel-header"><div><div className="panel-kicker">FORECAST WORKSPACE</div><h2>Prediction integrity</h2></div><span className="integrity-icon"><Gauge size={17} /></span></div>
                {forecasts.loading && !forecasts.data ? <div className="loading-state compact"><span className="loader" />Checking forecast records…</div> : forecasts.error ? <EmptyPanel title="Forecast API unavailable" detail={forecasts.error} icon={Activity} /> : list.length === 0 ? <EmptyPanel title="No forecast records yet" detail="Create a forecast through the API or run the demo seed to populate the workspace." icon={Activity} /> : <div className="forecast-preview-list">
                  {list.slice(0, 5).map((f) => (
                    <div className="forecast-preview-row" key={f.forecast_id}>
                      <div className="forecast-preview-main">
                        <span className="forecast-preview-target">{f.target}</span>
                        <span className="forecast-preview-value">{f.prediction.toFixed(1)} <small>[{f.range.lower.toFixed(0)} – {f.range.upper.toFixed(0)}]</small></span>
                      </div>
                      <span className={`evidence-pill evidence-${f.evidence_class ?? 'synthetic'}`}>{f.evidence_class ?? 'synthetic'}</span>
                    </div>
                  ))}
                  <div className="notice-count">{list.length} record{list.length === 1 ? '' : 's'} · {verifiedForecastCount} measured (live/derived) · {syntheticCount} synthetic/demo — every value is labelled, none is presented as measured unless it is</div>
                </div>}
              </article>
              <article className="panel research-panel" id="research">
                <div className="panel-header"><div><div className="panel-kicker">EXOGENOUS SIGNALS</div><h2>IntelX research</h2></div><span className="research-state">Unavailable</span></div>
                <EmptyPanel title="Research feed not available here" detail="Futuris currently has no read-only endpoint for IntelX stories. A forecast driver mentioning IntelX is not enough to verify or display a research item." icon={ShieldAlert} />
                <div className="research-foot"><span className="source-mark">IX</span><span>Source status: no feed contract</span></div>
              </article>
            </div>
          </section>

          <footer className="page-footer"><span>FUTURIS <b>·</b> FRIDAY UNIVERSE</span><span>Probe data refreshes every 45 seconds <i /> {health.data ? `API ${health.data.version}` : 'API version unavailable'}</span></footer>
        </div>
      </main>
    </div>
  );
}

function errorText(reason: unknown) {
  return reason instanceof Error ? reason.message : 'Request failed';
}

export default App;
