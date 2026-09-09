"""FRIDAY Universe domain registry defining targets, units, thresholds, and mitigations."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class UniverseDomain(str, Enum):
    FRIDAY = "friday"
    SENTINEL = "sentinel"
    CORTEX = "cortex"
    FORGE = "forge"
    MEMORA = "memora"
    INFERENCE = "inference"
    INTELX = "intelx"
    STRATEX = "stratex"
    INFRA = "infra"


class RiskLevel(str, Enum):
    NOMINAL = "NOMINAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class DomainTargetSpec:
    target: str
    name: str
    domain: UniverseDomain
    unit: str
    description: str
    high_risk_threshold: float
    critical_risk_threshold: float
    is_lower_better: bool = True  # True if higher values indicate higher risk
    default_model: str = "ensemble:universe_calibrated@v1"
    mitigation_action: str = ""
    interpretation_template: str = ""


# Full registry of supported targets across all 9 FRIDAY Universe pillars
UNIVERSE_TARGETS: dict[str, DomainTargetSpec] = {
    # 1. FRIDAY Core
    "friday:orchestration:system_health_24h": DomainTargetSpec(
        target="friday:orchestration:system_health_24h",
        name="Global System Health Index",
        domain=UniverseDomain.FRIDAY,
        unit="idx",
        description="Composite health and availability score across all FRIDAY microservices (0-100).",
        high_risk_threshold=70.0,
        critical_risk_threshold=50.0,
        is_lower_better=False,
        mitigation_action="Notify root supervisor and initialize automated node rebalancing.",
        interpretation_template="Global ecosystem health projected at {prediction:.1f} idx over next 24h.",
    ),
    "friday:eventbus:message_backlog_24h": DomainTargetSpec(
        target="friday:eventbus:message_backlog_24h",
        name="Event Bus Congestion Risk",
        domain=UniverseDomain.FRIDAY,
        unit="%",
        description="Probability of cross-agent event bus or webhook queues exceeding buffer capacity.",
        high_risk_threshold=0.40,
        critical_risk_threshold=0.70,
        mitigation_action="Throttle non-critical telemetry emissions and scale worker concurrency.",
        interpretation_template="{probability:.1f}% risk of inter-agent queue backlog breach.",
    ),

    # 2. Sentinel (Security & Governance)
    "sentinel:security:threat_anomaly_risk_24h": DomainTargetSpec(
        target="sentinel:security:threat_anomaly_risk_24h",
        name="Threat & Intrusion Anomaly Risk",
        domain=UniverseDomain.SENTINEL,
        unit="%",
        description="Calibrated likelihood of unauthorized access attempts or suspicious behavioral anomalies.",
        high_risk_threshold=0.30,
        critical_risk_threshold=0.60,
        mitigation_action="Enable strict Zero-Trust MFA validation and increase audit log sampling to 100%.",
        interpretation_template="{probability:.1f}% probability of elevated security anomaly activity.",
    ),
    "sentinel:ratelimit:api_saturation_risk_24h": DomainTargetSpec(
        target="sentinel:ratelimit:api_saturation_risk_24h",
        name="API Rate-Limit Saturation Risk",
        domain=UniverseDomain.SENTINEL,
        unit="%",
        description="Likelihood of public or gateway endpoints experiencing HTTP 429 quota exhaustion.",
        high_risk_threshold=0.35,
        critical_risk_threshold=0.65,
        mitigation_action="Enforce dynamic rate-limiting tiers and prioritize internal agent RPCs.",
        interpretation_template="{probability:.1f}% probability of hitting external or public API rate limits.",
    ),

    # 3. Cortex (Cognitive Reasoning & Subagents)
    "cortex:execution:sla_breach_probability_24h": DomainTargetSpec(
        target="cortex:execution:sla_breach_probability_24h",
        name="Cognitive Task SLA Breach Risk",
        domain=UniverseDomain.CORTEX,
        unit="%",
        description="Probability that complex multi-step reasoning goals breach execution timeout budgets.",
        high_risk_threshold=0.25,
        critical_risk_threshold=0.50,
        mitigation_action="Decompose complex subgoals and assign speculative parallel reasoning paths.",
        interpretation_template="{probability:.1f}% probability of cognitive task deadline overrun.",
    ),
    "cortex:subagent:concurrency_thrashing_risk_24h": DomainTargetSpec(
        target="cortex:subagent:concurrency_thrashing_risk_24h",
        name="Subagent Concurrency Thrashing Risk",
        domain=UniverseDomain.CORTEX,
        unit="%",
        description="Probability of circular subagent dependencies or context window saturation.",
        high_risk_threshold=0.30,
        critical_risk_threshold=0.55,
        mitigation_action="Prune conversation context and enforce maximum subagent recursion depth.",
        interpretation_template="{probability:.1f}% risk of subagent deadlock or context thrashing.",
    ),

    # 4. Forge (CI/CD, Build & Deployment)
    "forge:ci_cd:pipeline_failure_risk_24h": DomainTargetSpec(
        target="forge:ci_cd:pipeline_failure_risk_24h",
        name="CI/CD Build Pipeline Failure Risk",
        domain=UniverseDomain.FORGE,
        unit="%",
        description="Predictive failure rate on pending pull requests and automated integration test runs.",
        high_risk_threshold=0.20,
        critical_risk_threshold=0.45,
        mitigation_action="Trigger isolated pre-flight dry runs and run dependency audit sweep.",
        interpretation_template="{probability:.1f}% chance of test suite or Docker build failure.",
    ),
    "forge:deployment:regression_risk_24h": DomainTargetSpec(
        target="forge:deployment:regression_risk_24h",
        name="Production Release Rollback Risk",
        domain=UniverseDomain.FORGE,
        unit="%",
        description="Probability of runtime exceptions requiring automated canary rollback post-deployment.",
        high_risk_threshold=0.15,
        critical_risk_threshold=0.35,
        mitigation_action="Enforce canary release with 10% traffic routing and synthetic sanity checks.",
        interpretation_template="{probability:.1f}% probability of post-deployment regression.",
    ),

    # 5. Memora (Memory & Knowledge Systems)
    "memora:storage:capacity_exhaustion_days": DomainTargetSpec(
        target="memora:storage:capacity_exhaustion_days",
        name="Memory Storage Exhaustion Horizon",
        domain=UniverseDomain.MEMORA,
        unit="days",
        description="Estimated remaining days until persistent vector/relational storage hits 90% capacity.",
        high_risk_threshold=14.0,
        critical_risk_threshold=5.0,
        is_lower_better=False,
        mitigation_action="Trigger vector index vacuuming, archive cold episodic memory, and expand disk volume.",
        interpretation_template="Estimated {prediction:.1f} days of memory storage capacity remaining.",
    ),
    "memora:vector_index:retrieval_latency_spike_24h": DomainTargetSpec(
        target="memora:vector_index:retrieval_latency_spike_24h",
        name="Vector Retrieval Latency Spike Risk",
        domain=UniverseDomain.MEMORA,
        unit="%",
        description="Probability of similarity search queries breaching 200ms latency threshold.",
        high_risk_threshold=0.30,
        critical_risk_threshold=0.60,
        mitigation_action="Rebuild HNSW / IVF vector indexes and warm query cache.",
        interpretation_template="{probability:.1f}% probability of vector similarity search latency degradation.",
    ),

    # 6. Inference Gateway (LLM Compute & GPU Resources)
    "inference:gpu:vram_oom_probability_24h": DomainTargetSpec(
        target="inference:gpu:vram_oom_probability_24h",
        name="GPU VRAM Out-of-Memory Risk",
        domain=UniverseDomain.INFERENCE,
        unit="%",
        description="Probability of GPU VRAM exhaustion during concurrent multi-token generation.",
        high_risk_threshold=0.20,
        critical_risk_threshold=0.45,
        mitigation_action="Reduce max KV-cache allocation, enable PagedAttention, and fallback to quantized models.",
        interpretation_template="{probability:.1f}% likelihood of GPU out-of-memory under peak reasoning loads.",
    ),
    "inference:queue:token_starvation_risk_24h": DomainTargetSpec(
        target="inference:queue:token_starvation_risk_24h",
        name="Compute Queue Starvation Risk",
        domain=UniverseDomain.INFERENCE,
        unit="%",
        description="Probability of inference requests queueing longer than 5 seconds for available worker slots.",
        high_risk_threshold=0.30,
        critical_risk_threshold=0.60,
        mitigation_action="Spin up auxiliary inference replicas and enable request batching.",
        interpretation_template="{probability:.1f}% chance of token generation queue starvation.",
    ),

    # 7. IntelX (Autonomous Deep Intelligence)
    "intelx:research:topic_velocity_surge_24h": DomainTargetSpec(
        target="intelx:research:topic_velocity_surge_24h",
        name="Breaking Topic Emergence Velocity",
        domain=UniverseDomain.INTELX,
        unit="idx",
        description="Surge velocity index of novel exogenous market or technical research topics.",
        high_risk_threshold=1.8,
        critical_risk_threshold=2.5,
        mitigation_action="Allocate background scraping threads and trigger rapid synthesis pipelines.",
        interpretation_template="Research topic emergence velocity projected at {prediction:.2f} idx.",
    ),
    "intelx:source:rate_limit_depletion_24h": DomainTargetSpec(
        target="intelx:source:rate_limit_depletion_24h",
        name="Research Source API Depletion Risk",
        domain=UniverseDomain.INTELX,
        unit="%",
        description="Risk of depleting daily rate limits on financial news, SEC EDGAR, or web search APIs.",
        high_risk_threshold=0.35,
        critical_risk_threshold=0.65,
        mitigation_action="Switch to cached research extracts and throttle non-priority web scrapers.",
        interpretation_template="{probability:.1f}% risk of exhausting external research API quotas.",
    ),

    # 8. Stratex (Algorithmic Trading Execution)
    "market:crypto:BTCUSDT:volatility_24h": DomainTargetSpec(
        target="market:crypto:BTCUSDT:volatility_24h",
        name="BTC/USDT 24h Volatility Index",
        domain=UniverseDomain.STRATEX,
        unit="%",
        description="Calibrated expected price volatility percentage for BTC/USDT over next 24 hours.",
        high_risk_threshold=3.5,
        critical_risk_threshold=6.0,
        mitigation_action="Widen stop-loss thresholds by 1.5x and reduce futures leverage to 3x.",
        interpretation_template="Expected BTC volatility {prediction:.1f}% with {probability:.1f}% chance of volatility spike.",
    ),
    "market:crypto:ETHUSDT:volatility_24h": DomainTargetSpec(
        target="market:crypto:ETHUSDT:volatility_24h",
        name="ETH/USDT 24h Volatility Index",
        domain=UniverseDomain.STRATEX,
        unit="%",
        description="Calibrated expected price volatility percentage for ETH/USDT over next 24 hours.",
        high_risk_threshold=4.5,
        critical_risk_threshold=7.5,
        mitigation_action="Reduce altcoin exposure and rebalance portfolio toward spot collateral.",
        interpretation_template="Expected ETH volatility {prediction:.1f}% with {probability:.1f}% chance of volatility spike.",
    ),
    "market:crypto:portfolio:drawdown_risk_24h": DomainTargetSpec(
        target="market:crypto:portfolio:drawdown_risk_24h",
        name="Portfolio Maximum Drawdown Risk",
        domain=UniverseDomain.STRATEX,
        unit="%",
        description="Probability that aggregate Stratex trading portfolio experiences >5% drawdown over 24h.",
        high_risk_threshold=0.20,
        critical_risk_threshold=0.40,
        mitigation_action="Activate hedge positions on BTC/USDT perpetual contracts and pause automated entries.",
        interpretation_template="{probability:.1f}% probability of trading portfolio drawdown exceeding 5%.",
    ),

    # 9. Operational Infrastructure
    "service:checkout:capacity_exceedance_24h": DomainTargetSpec(
        target="service:checkout:capacity_exceedance_24h",
        name="Checkout Service Capacity Exceedance",
        domain=UniverseDomain.INFRA,
        unit="rpm",
        description="Projected peak transaction throughput in requests per minute against 4000 rpm envelope.",
        high_risk_threshold=3600.0,
        critical_risk_threshold=4000.0,
        mitigation_action="Trigger horizontal pod autoscaling and activate edge checkout caching.",
        interpretation_template="Projected demand at {prediction:.0f} rpm ({probability:.1f}% risk of capacity exceedance).",
    ),
}


def get_target_spec(target: str) -> DomainTargetSpec:
    """Retrieve spec for target or generate sensible default based on prefix."""
    if target in UNIVERSE_TARGETS:
        return UNIVERSE_TARGETS[target]

    # Dynamically deduce domain from prefix
    lower = target.lower()
    domain = UniverseDomain.INFRA
    unit = "idx"
    if lower.startswith("sentinel"):
        domain = UniverseDomain.SENTINEL
        unit = "%"
    elif lower.startswith("cortex"):
        domain = UniverseDomain.CORTEX
        unit = "%"
    elif lower.startswith("forge"):
        domain = UniverseDomain.FORGE
        unit = "%"
    elif lower.startswith("memora"):
        domain = UniverseDomain.MEMORA
        unit = "days" if "exhaustion" in lower or "days" in lower else "ms"
    elif lower.startswith("inference"):
        domain = UniverseDomain.INFERENCE
        unit = "%"
    elif lower.startswith("intelx"):
        domain = UniverseDomain.INTELX
        unit = "idx"
    elif lower.startswith("market") or lower.startswith("stratex") or any(c in lower for c in ["btc", "eth", "sol"]):
        domain = UniverseDomain.STRATEX
        unit = "%"
    elif lower.startswith("friday"):
        domain = UniverseDomain.FRIDAY
        unit = "%"
    elif "capacity" in lower or "rpm" in lower or "throughput" in lower:
        domain = UniverseDomain.INFRA
        unit = "rpm"

    return DomainTargetSpec(
        target=target,
        name=target.replace(":", " ").replace("_", " ").title(),
        domain=domain,
        unit=unit,
        description=f"Automated forecast target for {target}",
        high_risk_threshold=0.35,
        critical_risk_threshold=0.65,
        mitigation_action="Review telemetry drivers and verify operational safety thresholds.",
        interpretation_template="Projected value: {prediction:.2f} {unit}.",
    )


def evaluate_risk_level(spec: DomainTargetSpec, prediction: float, probability: float | None = None) -> RiskLevel:
    """Classify risk level as NOMINAL, ELEVATED, HIGH, or CRITICAL."""
    # Use probability if provided and target unit is %
    metric = probability if (probability is not None and spec.unit == "%") else prediction

    if spec.is_lower_better:
        if metric >= spec.critical_risk_threshold:
            return RiskLevel.CRITICAL
        if metric >= spec.high_risk_threshold:
            return RiskLevel.HIGH
        if metric >= spec.high_risk_threshold * 0.6:
            return RiskLevel.ELEVATED
        return RiskLevel.NOMINAL
    else:
        # Inverted: lower metric means higher risk (e.g. days remaining or health score)
        if metric <= spec.critical_risk_threshold:
            return RiskLevel.CRITICAL
        if metric <= spec.high_risk_threshold:
            return RiskLevel.HIGH
        if metric <= spec.high_risk_threshold * 1.3:
            return RiskLevel.ELEVATED
        return RiskLevel.NOMINAL
