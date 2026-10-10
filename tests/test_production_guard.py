"""Regression tests for the production credential guard (bug H4).

The guard was a no-op by default: it recognised only the spelling ``prod`` and
returned early unless ``STRICT_PRODUCTION_SECRETS=true`` was set as well.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from futuris.infra.config import Settings
from futuris.upgrade.safe_config import (
    forbid_placeholder_secret,
    is_production_environment,
    production_env_guard,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("env", ["prod", "production", "PROD", " Production "])
def test_every_production_spelling_is_recognised(env: str):
    assert is_production_environment(env) is True


@pytest.mark.parametrize("env", ["dev", "development", "test", "staging", "", None])
def test_non_production_environments_are_not_guarded(env):
    assert is_production_environment(env) is False


@pytest.mark.parametrize("env", ["prod", "production"])
def test_missing_secret_fails_closed(env: str):
    with pytest.raises(RuntimeError, match="must be configured in production"):
        production_env_guard(env, {"FUTURIS_API_KEY": None})


def test_placeholder_and_short_secrets_are_rejected():
    with pytest.raises(RuntimeError, match="production"):
        production_env_guard("production", {"FUTURIS_API_KEY": "changeme"})
    with pytest.raises(RuntimeError, match="production"):
        production_env_guard("production", {"FUTURIS_API_KEY": "short"})
    with pytest.raises(RuntimeError):
        forbid_placeholder_secret("INFERENCE_API_KEY", "your_api_key")


def test_guard_cannot_be_disabled_by_an_opt_out_flag(monkeypatch: pytest.MonkeyPatch):
    """STRICT_PRODUCTION_SECRETS no longer downgrades the guard."""
    monkeypatch.setenv("STRICT_PRODUCTION_SECRETS", "false")
    with pytest.raises(RuntimeError):
        production_env_guard("production", {"FUTURIS_API_KEY": None})


def test_real_secrets_pass():
    production_env_guard(
        "production",
        {"FUTURIS_API_KEY": "a" * 40, "INFERENCE_API_KEY": "b" * 40},
    )


def test_default_environment_is_not_production():
    """A fresh checkout must not claim to be production."""
    assert Settings().APP_ENV == "dev"


def test_production_startup_requires_credentials():
    """Importing the config in production without secrets must fail closed."""
    script = textwrap.dedent(
        """
        import futuris.infra.config  # noqa: F401
        print("STARTED")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "APP_ENV": "production",
            "PYTHONPATH": str(REPO_ROOT),
        },
        check=False,
    )
    assert result.returncode != 0
    assert "must be configured in production" in result.stderr
    assert "STARTED" not in result.stdout


def test_production_startup_with_demo_credentials_is_refused():
    script = textwrap.dedent(
        """
        import futuris.infra.config  # noqa: F401
        print("STARTED")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "APP_ENV": "production",
            "ALLOW_DEMO_CREDENTIALS": "true",
            "FUTURIS_API_KEY": "m" * 40,
            "FUTURIS_FRIDAY_API_KEY": "f" * 40,
            "MEMORA_API_KEY": "x" * 40,
            "INTELX_API_KEY": "i" * 40,
            "INFERENCE_API_KEY": "n" * 40,
            "STRATEX_API_KEY": "s" * 40,
            "PYTHONPATH": str(REPO_ROOT),
        },
        check=False,
    )
    assert result.returncode != 0
    assert "demo credentials cannot be enabled in production" in result.stderr


def test_production_refuses_api_keys_disabled():
    """API_KEYS_ENABLED=false in production would make every request an admin (B20)."""
    from futuris.infra.config import Settings

    settings_obj = Settings(
        APP_ENV="production",
        API_KEYS_ENABLED=False,
        FUTURIS_API_KEY="a" * 40,
        FUTURIS_FRIDAY_API_KEY="f" * 40,
        INFERENCE_API_KEY="b" * 40,
        MEMORA_API_KEY="c" * 40,
        STRATEX_API_KEY="d" * 40,
        INTELX_API_KEY="e" * 40,
    )
    with pytest.raises(RuntimeError, match="API_KEYS_ENABLED"):
        settings_obj.validate_production_safety()
