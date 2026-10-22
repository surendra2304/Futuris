"""CLI entry points (futuris/cli.py), which the README documents and which had no tests.

The heavy commands (ingest, forecast, backtest, sweep, demo) run the full pipeline and
are exercised by the harnesses instead; these tests cover the commands that can run
deterministically and the security property of key creation.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from typer.testing import CliRunner

from futuris import cli as cli_module
from futuris.storage.models import ApiKeyModel

runner = CliRunner()


@pytest.mark.parametrize(
    "command",
    ["create-admin-key", "serve", "demo", "forecast", "ingest", "backtest", "sweep"],
)
def test_every_documented_command_answers_help(command: str) -> None:
    result = runner.invoke(cli_module.cli, [command, "--help"])
    assert result.exit_code == 0, result.output


def test_serve_binds_to_the_requested_address(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict = {}
    monkeypatch.setattr(cli_module.uvicorn, "run", lambda app, **kw: calls.update(app=app, **kw))
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("HOST", raising=False)
    result = runner.invoke(cli_module.cli, ["serve", "--host", "127.0.0.1", "--port", "9123"])
    assert result.exit_code == 0, result.output
    assert calls == {
        "app": "futuris.api.app:app",
        "host": "127.0.0.1",
        "port": 9123,
        "reload": False,
    }


def test_serve_environment_overrides_the_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """PORT and HOST are how hosting platforms (Render, Docker) set the bind address."""
    calls: dict = {}
    monkeypatch.setattr(cli_module.uvicorn, "run", lambda app, **kw: calls.update(kw))
    monkeypatch.setenv("PORT", "8123")
    monkeypatch.setenv("HOST", "0.0.0.0")
    result = runner.invoke(cli_module.cli, ["serve", "--port", "9999"])
    assert result.exit_code == 0, result.output
    assert calls["port"] == 8123
    assert calls["host"] == "0.0.0.0"


def test_create_admin_key_prints_a_key_and_stores_only_its_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _schema_template: Path
) -> None:
    database = tmp_path / "cli.db"
    shutil.copyfile(_schema_template, database)
    # NullPool: the engine is created outside the CLI's own event loop, so it must not
    # keep connections that outlive that loop.
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}", poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(cli_module, "async_session_factory", factory)

    result = runner.invoke(cli_module.cli, ["create-admin-key", "--label", "ops-bootstrap"])
    assert result.exit_code == 0, result.output
    match = re.search(r"API Key:\s+(\S+)", result.output)
    assert match, result.output
    plain_key = match.group(1)
    assert plain_key.startswith("futuris_admin_")

    async def _stored() -> list[ApiKeyModel]:
        async with factory() as session:
            rows = await session.execute(select(ApiKeyModel))
            return list(rows.scalars())

    rows = asyncio.run(_stored())
    assert len(rows) == 1
    assert rows[0].label == "ops-bootstrap"
    assert rows[0].role == "admin"
    assert rows[0].key_hash == hashlib.sha256(plain_key.encode()).hexdigest()
    assert plain_key not in rows[0].key_hash
