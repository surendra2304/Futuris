"""Security tests for connector credential fallbacks."""

from unittest.mock import Mock

import pytest

from futuris.connectors import base
from futuris.infra.config import settings


def test_default_connector_credential_warns_once_in_development(monkeypatch) -> None:
    """Preserve convenient development behavior while making the fallback visible."""
    monkeypatch.setattr(settings, "APP_ENV", "dev")
    monkeypatch.setattr(base, "_warned_default_credentials", set())
    logger = Mock()

    base.warn_if_default_credential("test-connector", "public-default", "public-default", logger)
    base.warn_if_default_credential("test-connector", "public-default", "public-default", logger)

    logger.warning.assert_called_once_with(
        "connector_using_builtin_default_credential",
        connector="test-connector",
        remediation=(
            "configure a real API key for this connector; the built-in default "
            "is publicly known and must never be accepted by a peer"
        ),
    )


@pytest.mark.parametrize("environment", ["prod", "production", " Production "])
def test_default_connector_credential_is_rejected_in_production(
    monkeypatch, environment: str
) -> None:
    """Never allow a known public placeholder to reach an outbound peer in production."""
    monkeypatch.setattr(settings, "APP_ENV", environment)
    logger = Mock()

    with pytest.raises(RuntimeError, match="built-in default credential"):
        base.warn_if_default_credential(
            "test-connector", "public-default", "public-default", logger
        )

    logger.warning.assert_not_called()


def test_explicit_connector_secret_is_allowed_in_production(monkeypatch) -> None:
    """The fail-closed policy rejects only the known fallback, not configured secrets."""
    monkeypatch.setattr(settings, "APP_ENV", "production")
    logger = Mock()

    base.warn_if_default_credential(
        "test-connector", "unique-configured-secret", "public-default", logger
    )

    logger.warning.assert_not_called()
