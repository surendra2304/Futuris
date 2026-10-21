import pytest

from futuris.infra.config import Settings


def test_postgres_url_requires_an_explicit_password():
    settings = Settings(_env_file=None, POSTGRES_PASSWORD=None)

    with pytest.raises(RuntimeError, match="POSTGRES_PASSWORD must be configured"):
        _ = settings.postgres_async_url


def test_postgres_url_encodes_special_password_characters():
    settings = Settings(
        _env_file=None,
        POSTGRES_HOST="db.example.test",
        POSTGRES_PORT=5432,
        POSTGRES_USER="futuris",
        POSTGRES_PASSWORD="p@ss:/?#word",
        POSTGRES_DB="futuris",
    )

    url = settings.postgres_async_url
    assert url.startswith("postgresql+asyncpg://futuris:")
    assert "db.example.test:5432/futuris" in url
    assert "p%40ss%3A%2F%3F%23word" in url
    assert "p@ss:/?#word" not in url
