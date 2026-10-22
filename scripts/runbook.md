# FUTURIS Platform Runbook

Welcome to the **FUTURIS** Operational Capacity & Predictive Intelligence Platform.

## 🚀 5-Minute Quickstart

### 1. Start Infrastructure
Launch PostgreSQL and required services:
```bash
docker compose up -d
```

### 2. Run Database Migrations
Apply the initial schema migrations:
```bash
python -m alembic upgrade head
```

### 3. Bootstrap Deterministic Demo Environment
Seed 180 days of realistic capacity telemetry, execute walk-forward backtests, generate live forecasts with scenarios, and run a lifecycle resolution sweep:
```bash
python -m futuris.cli demo
```

### 4. Create an Admin API Key
Generate a root admin key with full platform permissions:
```bash
python -m futuris.cli create-admin-key --label "ops-lead"
```

### 5. Start API Server & React Dashboard
Launch the FastAPI server and UI bundle:
```bash
python -m futuris.cli serve --host 127.0.0.1 --port 8000
```
Open your browser and navigate to:
- **Interactive UI Dashboard**: [http://127.0.0.1:8000/ui](http://127.0.0.1:8000/ui)
- **OpenAPI Documentation**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Prometheus Metrics**: [http://127.0.0.1:8000/metrics](http://127.0.0.1:8000/metrics)

---

## 🧭 Key Workspaces to Explore

1. **Calibration Honesty Dashboard (`/ui/calibration`)**:
   - Inspect the empirical reliability diagram comparing predicted probability against observed frequency against the $45^\circ$ diagonal.
   - Note the Expected Calibration Error ($ECE$) and hierarchical shrinkage adjustments.

2. **Forecast Workspace (`/ui/`)**:
   - View active forecasts with visually distinct **Probability** (risk severity progress bars) vs **Meta-Confidence** (labeled calibration badges).
   - Click **View Details** to examine explanatory drivers and advisory decision recommendations (`requires_approval=True`).

3. **Outcomes Scoreboard (`/ui/outcomes`)**:
   - Track ground-truth resolution outcomes verified against immutable Parquet evidence snapshots.

4. **Triggering Manual Lifecyle Sweeps & Refreshes**:
```bash
python -m futuris.cli sweep
python -m futuris.cli forecast --target "service:checkout:capacity_exceedance_24h" --horizon 24h
```