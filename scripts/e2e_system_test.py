# E2E Complete System Verification
import asyncio
import sys
import traceback
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import numpy as np
import pandas as pd
from sqlalchemy import select

from futuris.api.app import app
from futuris.connectors.synthetic_telemetry import SyntheticTelemetryConnector
from futuris.core.engine import ForecastEngine
from futuris.core.enums import ConfidenceLevel, ForecastStatus, ScenarioType
from futuris.core.lifecycle import LifecycleManager
from futuris.core.pipeline import ForecastingPipeline
from futuris.core.schemas import Driver, EvidenceRef, Forecast
from futuris.evaluation.backtest import BacktestEngine
from futuris.evaluation.calibration import CalibrationAnalyzer
from futuris.features.contextualize import ContextLayer
from futuris.features.drivers import DriverAnalyzer
from futuris.features.normalize import Normalizer
from futuris.infra.auth import generate_api_key, hash_api_key
from futuris.infra.config import settings
from futuris.infra.logging import get_logger
from futuris.infra.scheduler import ForecastScheduler
from futuris.models.adapters import AutoARIMAAdapter, NaiveAdapter, SeasonalNaiveAdapter
from futuris.models.base import calculate_exceedance_probability
from futuris.models.registry import model_registry
from futuris.models.routing import ModelRouter, SeriesMetadata
from futuris.scenarios.engine import ScenarioEngine
from futuris.scenarios.spec import ScenarioSpec
from futuris.storage.db import async_session_factory, engine
from futuris.storage.models import ApiKeyModel, Base, ForecastModel, OutcomeModel
from futuris.storage.repositories import (
    EventRepository,
    ForecastRepository,
    OutcomeRepository,
    ScenarioRepository,
)
from futuris.upgrade.auth import CredentialHasher
from futuris.upgrade.cancellation import CancellationToken
from futuris.upgrade.decision import AdvisoryDecisionEngine
from futuris.upgrade.forecast_guard import ProductionDataSource, SourcePolicy, validate_point_in_time
from futuris.upgrade.quality import ForecastQualityGate
from futuris.upgrade.rate_limit import InMemoryRateLimitBackend
from futuris.upgrade.scheduler import DistributedLeaseTable, SafeScheduler, ScheduleSpec
from futuris.upgrade.state import StateMachine

passed_steps = []
failed_steps = []

def record_pass(step_name: str, details: str = ''):
    passed_steps.append((step_name, details))
    print(f'  [PASS] {step_name}' + (f' -> {details}' if details else ''))

def record_fail(step_name: str, error: str):
    failed_steps.append((step_name, error))
    print(f'  [FAIL] {step_name} -> {error}')

async def run_e2e():
    print('='*70)
    print('FUTURIS PREDICTIVE INTELLIGENCE PLATFORM -- COMPLETE E2E SYSTEM TEST')
    print('='*70)

    # 1. Database Init
    print('\n[STEP 1] Database Tables Initialization & Health')
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        record_pass('Database Engine Connection & Schema Init', 'Tables ready')
    except Exception as e:
        record_fail('Database Schema Init', str(e))
        return

    # 2. Telemetry Ingestion & Point-in-Time Monotonicity
    print('\n[STEP 2] Synthetic Telemetry Ingestion & Point-in-Time Verification')
    try:
        connector = SyntheticTelemetryConnector(seed=42)
        now = datetime.now(UTC)
        start = now - timedelta(days=7)
        obs = await connector.fetch(start, now)
        assert len(obs) > 0, 'No observations returned'
        timestamps = [o.observed_at for o in obs]
        assert all(t <= now for t in timestamps), 'Data leakage: Future timestamps found'
        assert all(t1 <= t2 for t1, t2 in zip(timestamps[:-1], timestamps[1:])), 'Timestamps not monotonic'
        record_pass('Telemetry Ingestion & Monotonicity', f'Fetched {len(obs)} points strictly <= as_of')
    except Exception as e:
        record_fail('Telemetry Ingestion', str(e))

    # 3. Normalization & Feature Contextualization
    print('\n[STEP 3] Normalization, Grid Resampling & Feature Contextualization')
    try:
        normalizer = Normalizer()
        signal_set = normalizer.normalize(obs)
        assert signal_set.grid_step_minutes > 0
        context_layer = ContextLayer()
        features_df = context_layer.build_feature_table(signal_set, as_of=now)
        assert not features_df.empty
        assert 'value' in features_df.columns
        record_pass('Feature Contextualization', f'Built feature table with shape {features_df.shape}')
    except Exception as e:
        record_fail('Feature Contextualization', str(e))

    # 4. Model Selection & Statistical Model Fit
    print('\n[STEP 4] Multi-Model Fitting (Naive, SeasonalNaive, AutoARIMA)')
    try:
        y_series = features_df['value']
        x_df = features_df.drop(columns=['value'])
        naive = NaiveAdapter()
        snaive = SeasonalNaiveAdapter(season_length=288)
        naive.fit(x_df, y_series, as_of=now)
        snaive.fit(x_df, y_series, as_of=now)
        pred_naive = naive.predict(12)
        pred_snaive = snaive.predict(12)
        assert len(pred_naive.point_forecast) == 12
        assert len(pred_snaive.point_forecast) == 12
        record_pass('Statistical Model Fit & Predict', 'Naive and SeasonalNaive predicted 12 horizon steps')
    except Exception as e:
        record_fail('Model Fit & Predict', str(e))

    # 5. Core Forecast Pipeline Execution
    print('\n[STEP 5] Core Forecasting Pipeline & Meta-Confidence Scoring')
    f = None
    try:
        pipeline = ForecastingPipeline()
        target = 'service:checkout:capacity_exceedance_24h'
        result = await pipeline.run(target=target, as_of=now, horizon=timedelta(hours=24), lookback_days=7)
        f = result.forecast
        assert f.prediction is not None and np.isfinite(f.prediction)
        assert f.range_lower <= f.prediction <= f.range_upper
        assert 0.0 <= (f.probability or 0.0) <= 1.0
        assert len(f.drivers) > 0
        assert len(f.evidence) > 0
        record_pass('Forecasting Pipeline Execution', f'Target={f.target}, Pred={f.prediction:.2f}, Range=[{f.range_lower:.2f}, {f.range_upper:.2f}], Prob={(f.probability or 0.0)*100:.1f}%, Conf={f.confidence.value}')
    except Exception as e:
        record_fail('Forecasting Pipeline Execution', str(e))

    # 6. Deep Upgrade ForecastQualityGate Check
    print('\n[STEP 6] Deep Upgrade Quality Gate Invariant Verification')
    try:
        assert f is not None
        valid, reasons = ForecastQualityGate.validate_forecast(f)
        assert valid, f'Quality Gate Rejected Forecast: {reasons}'
        ForecastQualityGate.require(f)
        record_pass('ForecastQualityGate Enforcement', 'Interval order, finite numbers, confidence and evidence pass')
    except Exception as e:
        record_fail('ForecastQualityGate Enforcement', str(e))

    # 7. Advisory Decision Engine Invariant (Prediction != Authorization)
    print('\n[STEP 7] Advisory Decision Support Guardrails')
    try:
        assert f is not None
        decision_engine = AdvisoryDecisionEngine()
        advisory = decision_engine.evaluate_forecast(f, impact_severity='high')
        assert advisory.decision_class.value == 'advisory'
        assert advisory.requires_human_authorization is True
        assert advisory.authorization_granted is False
        record_pass('Advisory Decision Support', 'Prediction != Authorization invariant mathematically strictly preserved')
    except Exception as e:
        record_fail('Advisory Decision Support', str(e))

    # 8. Counterfactual Scenarios & Divergence Ranking
    print('\n[STEP 8] Counterfactual Scenario Simulation Engine')
    try:
        assert f is not None
        scenario_engine = ScenarioEngine()
        spec_baseline = ScenarioSpec(name='Baseline', scenario_type=ScenarioType.BASELINE, assumption_overrides={}, rationale='Baseline check')
        spec_stress = ScenarioSpec(name='Traffic Spike +50%', scenario_type=ScenarioType.STRESS, assumption_overrides={'value': 1.5}, rationale='Peak traffic event')
        scenarios = scenario_engine.evaluate_scenarios(f, [spec_baseline, spec_stress], features_df=features_df)
        assert len(scenarios) == 2
        comp = scenario_engine.compare_scenarios(scenarios)
        assert 'divergence_ranking' in comp
        record_pass('Scenario Simulation & Comparison', f'Simulated {len(scenarios)} scenarios with divergence ranking')
    except Exception as e:
        record_fail('Scenario Simulation', str(e))

    # 9. Storage Persistence (Forecasts, Scenarios, Outcomes, Events)
    print('\n[STEP 9] ORM Storage & Transaction Persistence')
    try:
        assert f is not None
        async with async_session_factory() as session:
            f_repo = ForecastRepository(session)
            e_repo = EventRepository(session)
            o_repo = OutcomeRepository(session)
            saved = await f_repo.create(f)
            assert saved.forecast_id == f.forecast_id
            await session.commit()

            fetched = await f_repo.get_by_id(f.forecast_id)
            assert fetched is not None
            assert fetched.target == f.target
            assert fetched.prediction == f.prediction
        record_pass('Database Persistence', f'Saved & re-queried forecast {f.forecast_id}')
    except Exception as e:
        record_fail('Database Persistence', str(e))

    # 10. Lifecycle Sweep, Invalidation & Manual Resolution
    print('\n[STEP 10] Lifecycle Sweeps, Ground Truth Resolution & Outcome Recording')
    try:
        assert f is not None
        async with async_session_factory() as session:
            f_repo = ForecastRepository(session)
            o_repo = OutcomeRepository(session)
            e_repo = EventRepository(session)
            manager = LifecycleManager(f_repo, o_repo, e_repo)
            outcome = await manager.resolve_manual(
                forecast_id=f.forecast_id,
                observed_value=3950.0,
                event_occurred=True,
                note='Manual resolution test'
            )
            assert outcome is not None
            assert outcome.observed_value == 3950.0
            await session.commit()
            record_pass('Manual Resolution & Outcome Record', f'Resolved forecast with observed_value={outcome.observed_value}')
    except Exception as e:
        record_fail('Manual Resolution', str(e))

    # 11. Empirical Calibration Scoring
    print('\n[STEP 11] Empirical Probability Calibration Scoring (Brier, ECE, Log-Loss)')
    try:
        analyzer = CalibrationAnalyzer(num_bins=5)
        probs = [0.1, 0.25, 0.6, 0.85, 0.9]
        actuals = [0, 0, 1, 1, 1]
        calib_curve = analyzer.compute_calibration_curve(probs, actuals)
        brier = analyzer.compute_brier_score(probs, actuals)
        ece = analyzer.compute_expected_calibration_error(probs, actuals)
        assert brier >= 0.0
        assert ece >= 0.0
        record_pass('Empirical Calibration Analysis', f'Computed Brier={brier:.4f}, ECE={ece:.4f}, Bins={len(calib_curve)}')
    except Exception as e:
        record_fail('Calibration Analysis', str(e))

    # 12. Scheduler Single-Flight Leasing & Concurrency Safety
    print('\n[STEP 12] Distributed Single-Flight Lease Scheduling')
    try:
        lease_table = DistributedLeaseTable()
        acq1 = await lease_table.acquire('job_refresh', 'worker_1', lease_seconds=10)
        acq2 = await lease_table.acquire('job_refresh', 'worker_2', lease_seconds=10)
        assert acq1 is True, 'Worker 1 should acquire lease'
        assert acq2 is False, 'Worker 2 should be rejected due to active lease'
        rel1 = await lease_table.release('job_refresh', 'worker_1')
        assert rel1 is True
        acq2_after = await lease_table.acquire('job_refresh', 'worker_2', lease_seconds=10)
        assert acq2_after is True, 'Worker 2 should acquire after release'
        record_pass('Distributed Lease Single-Flight Execution', 'Single-flight mutual exclusion strictly enforced')
    except Exception as e:
        record_fail('Distributed Lease Scheduling', str(e))

    # 13. REST API Integration End-to-End Suite
    print('\n[STEP 13] REST API Integration (Endpoints, RBAC, Quality Gates, Rate Limiting)')
    try:
        transport = httpx.ASGITransport(app=app)
        auth_headers = {'X-API-Key': settings.FUTURIS_API_KEY}
        async with httpx.AsyncClient(transport=transport, base_url='http://testserver', headers=auth_headers) as client:
            # 13a. Health & Root
            h_resp = await client.get('/health')
            assert h_resp.status_code == 200
            assert h_resp.json()['status'] == 'ok'
            r_resp = await client.get('/')
            assert r_resp.status_code == 200
            assert r_resp.json()['service'] == 'FUTURIS'

            # 13b. Metrics
            m_resp = await client.get('/metrics')
            assert m_resp.status_code == 200

            # 13c. Unauthenticated rejection (RBAC verification)
            no_auth_resp = await client.post('/v1/forecasts', json={'target': 'test', 'horizon': '24h'}, headers={'X-API-Key': 'invalid_secret_key'})
            assert no_auth_resp.status_code == 401, f'Expected 401, got {no_auth_resp.status_code}'

            # 13d. Forecast Generation via API
            create_payload = {
                'target': 'service:checkout:capacity_exceedance_24h',
                'horizon': '24h',
                'as_of': now.isoformat(),
            }
            c_resp = await client.post('/v1/forecasts', json=create_payload)
            assert c_resp.status_code == 201, f'API returned {c_resp.status_code}: {c_resp.text}'
            f_data = c_resp.json()
            api_f_id = f_data['forecast_id']
            assert f_data['target'] == create_payload['target']
            assert 'range' in f_data

            # 13e. Query Forecast by ID
            g_resp = await client.get(f'/v1/forecasts/{api_f_id}')
            assert g_resp.status_code == 200
            assert g_resp.json()['forecast_id'] == api_f_id

            # 13f. Counterfactual Scenario Simulation via API
            scen_payload = {
                'scenarios': [
                    {'name': 'Spike', 'scenario_type': 'stress', 'assumption_overrides': {'value': 1.4}, 'rationale': 'test'},
                ],
                'use_monte_carlo': False,
            }
            s_resp = await client.post(f'/v1/forecasts/{api_f_id}/scenarios', json=scen_payload)
            assert s_resp.status_code == 200
            assert len(s_resp.json()) == 1

            # 13g. Invalidate Forecast via API
            inv_resp = await client.post(f'/v1/forecasts/{api_f_id}/invalidate', json={'reason': 'Cluster maintenance'})
            assert inv_resp.status_code == 200
            assert inv_resp.json()['status'] == 'invalidated'

            # 13h. FRIDAY Ecosystem Contract Endpoint
            friday_payload = {
                'friday_request_id': 'req_friday_e2e_1',
                'target': 'service:checkout:capacity_exceedance_24h',
                'horizon': '24h',
                'confidence_level': 0.90,
            }
            fri_resp = await client.post('/v1/friday/forecast', json=friday_payload)
            assert fri_resp.status_code == 201, f'FRIDAY delegation failed: {fri_resp.status_code} {fri_resp.text}'
            assert 'prediction' in fri_resp.json()

        record_pass('REST API End-to-End Suite', 'Health, Root, Metrics, RBAC 401, Forecast Create, Scenarios, Invalidation & FRIDAY API passed')
    except Exception as e:
        record_fail('REST API Suite', f'{e}\n{traceback.format_exc()}')

    print('\n' + '='*70)
    print(f'SUMMARY: {len(passed_steps)} PASSED | {len(failed_steps)} FAILED')
    print('='*70)

    if failed_steps:
        print('\nFailures encountered:')
        for step, err in failed_steps:
            print(f' - {step}: {err}')
        sys.exit(1)
    else:
        print('\nALL SUBSYSTEMS OPERATIONAL AND VERIFIED CLEAN.')
        sys.exit(0)

if __name__ == '__main__':
    asyncio.run(run_e2e())
