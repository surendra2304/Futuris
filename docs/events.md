# FUTURIS Domain Events & Webhook Contracts

FUTURIS publishes typed domain events throughout the predictive lifecycle. External consumers can subscribe via `POST /v1/webhooks` to receive real-time webhook deliveries verified via `HMAC-SHA256`.

---

## 🔐 Webhook Signature Verification

Each webhook request contains an `X-Futuris-Signature` header computed as:
```
signature = HMAC_SHA256(secret_key, request_body_json)
```

---

## 📡 Event Types & Payload Schemas

### 1. `forecast_created`
Emitted immediately when a new forecast is created and anchored to frozen evidence.
```json
{
  "event_id": "93a17e08-cf52-47d3-982d-bf1bc44b8061",
  "forecast_id": "b667d615-d27d-48ee-9278-020d748d6392",
  "event_type": "forecast_created",
  "payload": {
    "target": "service:checkout:capacity_exceedance_24h",
    "prediction": 3850.5,
    "probability": 0.72,
    "confidence": "high",
    "model_version": "auto_arima@v1"
  },
  "emitted_at": "2026-08-28T12:00:00Z"
}
```

### 2. `forecast_updated`
Emitted during scheduled refreshes only if probability ($\Delta \ge 5\%$) or prediction ($\Delta \ge 50$ rpm) exceeds noise suppression thresholds.
```json
{
  "event_id": "48b19e21-5a02-45e6-81c4-72deef961e05",
  "forecast_id": "b667d615-d27d-48ee-9278-020d748d6392",
  "event_type": "forecast_updated",
  "payload": {
    "target": "service:checkout:capacity_exceedance_24h",
    "previous_probability": 0.72,
    "new_probability": 0.89,
    "previous_prediction": 3850.5,
    "new_prediction": 4120.0
  },
  "emitted_at": "2026-08-28T13:00:00Z"
}
```

### 3. `forecast_threshold_crossed`
Emitted when a forecast probability breaches configured risk thresholds (e.g. $P > 80\%$).
```json
{
  "event_id": "a3f56b10-6c91-4d33-bc99-1a48e716d912",
  "forecast_id": "b667d615-d27d-48ee-9278-020d748d6392",
  "event_type": "forecast_threshold_crossed",
  "payload": {
    "target": "service:checkout:capacity_exceedance_24h",
    "threshold": 0.80,
    "observed_probability": 0.89
  },
  "emitted_at": "2026-08-28T13:00:00Z"
}
```

### 4. `forecast_invalidated`
Emitted when a forecast is invalidated due to assumption violation or infrastructure changes.
```json
{
  "event_id": "31b69d44-aa02-45e6-81c4-72deef961e99",
  "forecast_id": "b667d615-d27d-48ee-9278-020d748d6392",
  "event_type": "forecast_invalidated",
  "payload": {
    "reason": "Cluster resized from 8 to 16 nodes"
  },
  "emitted_at": "2026-08-28T14:30:00Z"
}
```

### 5. `forecast_outcome_recorded`
Emitted when ground truth is observed and verified against the evidence snapshot.
```json
{
  "event_id": "52d9a112-bb03-4f71-92d1-81beef961122",
  "forecast_id": "b667d615-d27d-48ee-9278-020d748d6392",
  "event_type": "forecast_outcome_recorded",
  "payload": {
    "observed_value": 4150.0,
    "event_occurred": true,
    "resolution_method": "automatic"
  },
  "emitted_at": "2026-08-29T12:00:00Z"
}
```

### 6. `model_promoted` & `model_degraded`
Emitted upon statistical model promotion or statistical process control drift detection.