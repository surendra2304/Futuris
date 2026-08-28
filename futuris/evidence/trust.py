"""Source trust registry mapping data sources to trust levels."""

from futuris.core.enums import SourceTrust


class SourceTrustRegistry:
    """Central registry determining the trustworthiness of ingestion sources."""

    _DEFAULT_MAPPINGS: dict[str, SourceTrust] = {
        "telemetry:datadog": SourceTrust.HIGH,
        "telemetry:prometheus": SourceTrust.HIGH,
        "telemetry:synthetic": SourceTrust.HIGH,
        "historical:warehouse": SourceTrust.HIGH,
        "external:weather": SourceTrust.MEDIUM,
        "external:social": SourceTrust.LOW,
        "agent:simulation": SourceTrust.MEDIUM,
        "unverified:feed": SourceTrust.UNTRUSTED,
    }

    def __init__(self, custom_overrides: dict[str, SourceTrust] | None = None) -> None:
        self.registry = dict(self._DEFAULT_MAPPINGS)
        if custom_overrides:
            self.registry.update(custom_overrides)

    def get_trust(self, source_id: str) -> SourceTrust:
        """Resolve trust level for source identifier, prefix matching supported."""
        if source_id in self.registry:
            return self.registry[source_id]

        for prefix, trust in self.registry.items():
            if source_id.startswith(prefix):
                return trust

        return SourceTrust.LOW

    def is_eligible_for_training(self, source_id: str) -> bool:
        """Check if data source meets the trust threshold for model training."""
        trust = self.get_trust(source_id)
        return trust in (SourceTrust.HIGH, SourceTrust.MEDIUM)

    def set_trust(self, source_id: str, trust: SourceTrust) -> None:
        """Dynamically configure source trust level."""
        self.registry[source_id] = trust


source_trust_registry = SourceTrustRegistry()
