"""
Futuris/predict/predictive_maintenance.py — Futuris Predictive Maintenance Engine.

Predicts equipment failures, optimizes maintenance schedules, analyzes sensor
data for anomalies, and generates maintenance recommendations with
reliability analysis and failure mode tracking.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class Equipment:
    """A piece of equipment."""
    equipment_id: str
    name: str
    type: str = ""
    location: str = ""
    install_date: str = ""
    criticality: str = "medium"  # low, medium, high, critical
    sensors: list[str] = field(default_factory=list)
    status: str = "operational"  # operational, maintenance, offline, failed
    last_maintenance: float = 0.0
    next_maintenance: float = 0.0


@dataclass
class SensorReading:
    """A sensor reading."""
    reading_id: str
    equipment_id: str
    sensor_type: str  # vibration, temperature, pressure, flow, current, voltage
    value: float = 0.0
    unit: str = ""
    threshold_min: float = 0.0
    threshold_max: float = 0.0
    timestamp: float = 0.0
    anomaly: bool = False


@dataclass
class FailurePrediction:
    """A failure prediction."""
    prediction_id: str
    equipment_id: str
    failure_type: str = ""  # mechanical, electrical, thermal, wear, corrosion
    probability: float = 0.0
    predicted_time_to_failure_hours: float = 0.0
    confidence: float = 0.0
    contributing_factors: list[str] = field(default_factory=list)
    recommended_action: str = ""
    timestamp: float = 0.0


@dataclass
class MaintenanceSchedule:
    """A maintenance schedule."""
    schedule_id: str
    equipment_id: str
    maintenance_type: str  # preventive, predictive, corrective, emergency
    scheduled_time: str = ""
    estimated_duration_hours: float = 0.0
    technician: str = ""
    parts_required: list[str] = field(default_factory=list)
    status: str = "scheduled"  # scheduled, in_progress, completed, cancelled
    priority: str = "normal"
    created_at: float = 0.0


@dataclass
class ReliabilityAnalysis:
    """Reliability analysis for equipment."""
    equipment_id: str
    mtbf: float = 0.0  # Mean Time Between Failures
    mttr: float = 0.0  # Mean Time To Repair
    availability: float = 0.0
    failure_rate: float = 0.0
    reliability_curve: list[dict] = field(default_factory=list)
    failure_modes: list[dict] = field(default_factory=list)


@dataclass
class AnomalyDetection:
    """A detected sensor anomaly."""
    anomaly_id: str
    equipment_id: str
    sensor_type: str
    value: float
    expected_range: list[float]
    deviation: float
    severity: str = "medium"
    detected_at: float = 0.0
    description: str = ""


@dataclass
class MaintenanceReport:
    """Report on predictive maintenance."""
    total_equipment: int = 0
    total_predictions: int = 0
    total_schedules: int = 0
    equipment_at_risk: int = 0
    avg_predicted_failure_time: float = 0.0
    upcoming_maintenance: int = 0
    predictive_coverage: float = 0.0


class PredictiveMaintenanceEngine:
    """Predictive maintenance engine."""

    def __init__(self):
        self._equipment: dict[str, Equipment] = {}
        self._readings: list[SensorReading] = []
        self._predictions: dict[str, FailurePrediction] = {}
        self._schedules: dict[str, MaintenanceSchedule] = {}
        self._reliability: dict[str, ReliabilityAnalysis] = {}
        self._anomalies: list[AnomalyDetection] = []

    def register_equipment(
        self,
        name: str,
        equipment_type: str = "",
        location: str = "",
        criticality: str = "medium",
        sensors: list[str] | None = None,
    ) -> Equipment:
        """Register equipment."""
        equipment = Equipment(
            equipment_id=hashlib.md5(
                f"equipment:{name}:{time.time()}".encode()
            ).hexdigest()[:12],
            name=name,
            type=equipment_type,
            location=location,
            criticality=criticality,
            sensors=sensors or ["vibration", "temperature", "pressure"],
            last_maintenance=time.time(),
            next_maintenance=time.time() + 86400 * 30,
        )
        self._equipment[equipment.equipment_id] = equipment
        return equipment

    def add_reading(
        self,
        equipment_id: str,
        sensor_type: str,
        value: float,
        threshold_min: float = 0.0,
        threshold_max: float = 100.0,
    ) -> SensorReading:
        """Add a sensor reading."""
        equipment = self._equipment.get(equipment_id)
        if not equipment:
            raise HTTPException(404, f"Equipment {equipment_id} not found")

        anomaly = value < threshold_min or value > threshold_max

        reading = SensorReading(
            reading_id=hashlib.md5(
                f"reading:{equipment_id}:{sensor_type}:{time.time()}".encode()
            ).hexdigest()[:12],
            equipment_id=equipment_id,
            sensor_type=sensor_type,
            value=value,
            unit="units",
            threshold_min=threshold_min,
            threshold_max=threshold_max,
            timestamp=time.time(),
            anomaly=anomaly,
        )

        self._readings.append(reading)

        if anomaly:
            anomaly_detection = AnomalyDetection(
                anomaly_id=hashlib.md5(
                    f"anomaly:{equipment_id}:{sensor_type}:{time.time()}".encode()
                ).hexdigest()[:12],
                equipment_id=equipment_id,
                sensor_type=sensor_type,
                value=value,
                expected_range=[threshold_min, threshold_max],
                deviation=abs(value - (threshold_min + threshold_max) / 2),
                severity="high" if value > threshold_max * 1.2 or value < threshold_min * 0.8 else "medium",
                detected_at=time.time(),
                description=f"Sensor {sensor_type} reading {value} outside expected range",
            )
            self._anomalies.append(anomaly_detection)

        return reading

    def predict_failure(self, equipment_id: str) -> FailurePrediction:
        """Predict failure for equipment."""
        equipment = self._equipment.get(equipment_id)
        if not equipment:
            raise HTTPException(404, f"Equipment {equipment_id} not found")

        # Analyze recent readings
        recent_readings = [
            r for r in self._readings
            if r.equipment_id == equipment_id
        ][-100:]

        anomaly_count = sum(1 for r in recent_readings if r.anomaly)

        # Calculate failure probability
        base_prob = 0.1
        if anomaly_count > 5:
            base_prob += 0.3
        if equipment.criticality in ("high", "critical"):
            base_prob += 0.1
        if time.time() - equipment.last_maintenance > 86400 * 90:  # 90+ days
            base_prob += 0.2

        failure_prob = min(0.95, base_prob + (anomaly_count * 0.02))

        # Time to failure
        time_to_failure = 168 / max(0.1, failure_prob)  # hours

        # Determine failure type
        sensor_types = set(r.sensor_type for r in recent_readings if r.anomaly)
        if "temperature" in sensor_types:
            failure_type = "thermal"
        elif "vibration" in sensor_types:
            failure_type = "mechanical"
        elif "current" in sensor_types:
            failure_type = "electrical"
        else:
            failure_type = "wear"

        # Recommended action
        if failure_prob > 0.7:
            action = "Schedule emergency maintenance immediately"
        elif failure_prob > 0.4:
            action = "Schedule preventive maintenance within 24 hours"
        else:
            action = "Monitor closely, schedule routine maintenance"

        prediction = FailurePrediction(
            prediction_id=hashlib.md5(
                f"prediction:{equipment_id}:{time.time()}".encode()
            ).hexdigest()[:12],
            equipment_id=equipment_id,
            failure_type=failure_type,
            probability=failure_prob,
            predicted_time_to_failure_hours=time_to_failure,
            confidence=0.7 + failure_prob * 0.2,
            contributing_factors=self._get_contributing_factors(equipment, recent_readings),
            recommended_action=action,
            timestamp=time.time(),
        )

        self._predictions[prediction.prediction_id] = prediction
        return prediction

    def _get_contributing_factors(
        self,
        equipment: Equipment,
        readings: list[SensorReading],
    ) -> list[str]:
        """Get contributing factors to failure."""
        factors = []

        anomaly_count = sum(1 for r in readings if r.anomaly)
        if anomaly_count > 3:
            factors.append(f"{anomaly_count} anomalous sensor readings")

        days_since_maintenance = (time.time() - equipment.last_maintenance) / 86400
        if days_since_maintenance > 60:
            factors.append(f"Overdue maintenance ({days_since_maintenance:.0f} days)")

        temp_readings = [r for r in readings if r.sensor_type == "temperature"]
        if temp_readings:
            avg_temp = sum(r.value for r in temp_readings) / len(temp_readings)
            if avg_temp > 80:
                factors.append(f"Elevated temperature ({avg_temp:.0f}°C)")

        vib_readings = [r for r in readings if r.sensor_type == "vibration"]
        if vib_readings:
            avg_vib = sum(r.value for r in vib_readings) / len(vib_readings)
            if avg_vib > 10:
                factors.append(f"High vibration ({avg_vib:.1f} mm/s)")

        return factors[:5]

    def schedule_maintenance(
        self,
        equipment_id: str,
        maintenance_type: str = "predictive",
        scheduled_time: str = "",
        technician: str = "",
        priority: str = "normal",
    ) -> MaintenanceSchedule:
        """Schedule maintenance."""
        equipment = self._equipment.get(equipment_id)
        if not equipment:
            raise HTTPException(404, f"Equipment {equipment_id} not found")

        schedule = MaintenanceSchedule(
            schedule_id=hashlib.md5(
                f"schedule:{equipment_id}:{time.time()}".encode()
            ).hexdigest()[:12],
            equipment_id=equipment_id,
            maintenance_type=maintenance_type,
            scheduled_time=scheduled_time,
            estimated_duration_hours=4.0,
            technician=technician,
            priority=priority,
            created_at=time.time(),
        )

        self._schedules[schedule.schedule_id] = schedule
        return schedule

    def reliability_analysis(self, equipment_id: str) -> ReliabilityAnalysis:
        """Analyze equipment reliability."""
        equipment = self._equipment.get(equipment_id)
        if not equipment:
            raise HTTPException(404, f"Equipment {equipment_id} not found")

        # Simulate reliability metrics
        predictions = [p for p in self._predictions.values() if p.equipment_id == equipment_id]
        anomalies = [a for a in self._anomalies if a.equipment_id == equipment_id]

        mtbf = 8760 / max(0.1, 1 + len(anomalies) * 0.1)  # hours
        mttr = 4.0 + len(predictions) * 0.5
        availability = mtbf / (mtbf + mttr) * 100
        failure_rate = 1 / max(1, mtbf)

        # Generate reliability curve
        reliability_curve = []
        for month in range(12):
            reliability_curve.append({
                "month": month + 1,
                "reliability": max(0.1, 1 - (month / 12) * (1 / max(1, mtbf / 720))),
            })

        analysis = ReliabilityAnalysis(
            equipment_id=equipment_id,
            mtbf=mtbf,
            mttr=mttr,
            availability=availability,
            failure_rate=failure_rate,
            reliability_curve=reliability_curve,
            failure_modes=[
                {"mode": "mechanical_wear", "probability": 0.3},
                {"mode": "thermal_failure", "probability": 0.2},
                {"mode": "electrical_fault", "probability": 0.15},
                {"mode": "corrosion", "probability": 0.1},
            ],
        )

        self._reliability[equipment_id] = analysis
        return analysis

    def get_report(self) -> dict:
        """Get predictive maintenance report."""
        equipment_at_risk = sum(
            1 for p in self._predictions.values()
            if p.probability > 0.5 and p.equipment_id in self._equipment
        )

        avg_failure_time = sum(
            p.predicted_time_to_failure_hours for p in self._predictions.values()
        ) / max(1, len(self._predictions))

        return {
            "total_equipment": len(self._equipment),
            "total_predictions": len(self._predictions),
            "total_schedules": len(self._schedules),
            "total_anomalies": len(self._anomalies),
            "total_readings": len(self._readings),
            "equipment_at_risk": equipment_at_risk,
            "avg_predicted_failure_time_hours": avg_failure_time,
            "upcoming_maintenance": sum(
                1 for s in self._schedules.values() if s.status == "scheduled"
            ),
            "predictive_coverage": len(self._equipment) / max(1, len(self._equipment)),
            "equipment": [
                {
                    "name": e.name,
                    "type": e.type,
                    "criticality": e.criticality,
                    "status": e.status,
                    "sensors": e.sensors,
                }
                for e in self._equipment.values()
            ],
        }


# Module-level singleton
maintenance_engine = PredictiveMaintenanceEngine()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/futuris/maintenance/equipment")
async def register_equipment(payload: dict):
    """Register equipment."""
    equipment = maintenance_engine.register_equipment(
        name=payload["name"],
        equipment_type=payload.get("type", ""),
        location=payload.get("location", ""),
        criticality=payload.get("criticality", "medium"),
        sensors=payload.get("sensors", []),
    )
    return {"equipment_id": equipment.equipment_id, "name": equipment.name, "status": equipment.status}


@router.post("/api/v1/futuris/maintenance/equipment/{equipment_id}/readings")
async def add_reading(equipment_id: str, payload: dict):
    """Add a sensor reading."""
    reading = maintenance_engine.add_reading(
        equipment_id=equipment_id,
        sensor_type=payload["sensor_type"],
        value=payload["value"],
        threshold_min=payload.get("threshold_min", 0.0),
        threshold_max=payload.get("threshold_max", 100.0),
    )
    return {"reading_id": reading.reading_id, "sensor_type": reading.sensor_type, "anomaly": reading.anomaly}


@router.get("/api/v1/futuris/maintenance/equipment/{equipment_id}/predict")
async def predict_failure(equipment_id: str):
    """Predict failure for equipment."""
    prediction = maintenance_engine.predict_failure(equipment_id)
    return {
        "prediction_id": prediction.prediction_id,
        "failure_type": prediction.failure_type,
        "probability": prediction.probability,
        "time_to_failure_hours": prediction.predicted_time_to_failure_hours,
        "confidence": prediction.confidence,
        "contributing_factors": prediction.contributing_factors,
        "recommended_action": prediction.recommended_action,
    }


@router.post("/api/v1/futuris/maintenance/equipment/{equipment_id}/schedules")
async def schedule_maintenance(equipment_id: str, payload: dict):
    """Schedule maintenance."""
    schedule = maintenance_engine.schedule_maintenance(
        equipment_id=equipment_id,
        maintenance_type=payload.get("maintenance_type", "predictive"),
        scheduled_time=payload.get("scheduled_time", ""),
        technician=payload.get("technician", ""),
        priority=payload.get("priority", "normal"),
    )
    return {"schedule_id": schedule.schedule_id, "equipment_id": schedule.equipment_id, "status": schedule.status}


@router.get("/api/v1/futuris/maintenance/equipment/{equipment_id}/reliability")
async def reliability_analysis(equipment_id: str):
    """Analyze equipment reliability."""
    analysis = maintenance_engine.reliability_analysis(equipment_id)
    return {
        "equipment_id": analysis.equipment_id,
        "mtbf_hours": analysis.mtbf,
        "mttr_hours": analysis.mttr,
        "availability_pct": analysis.availability,
        "failure_rate": analysis.failure_rate,
        "failure_modes": analysis.failure_modes,
    }


@router.get("/api/v1/futuris/maintenance/report")
async def get_report():
    """Get predictive maintenance report."""
    return maintenance_engine.get_report()
