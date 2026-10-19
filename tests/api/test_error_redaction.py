"""Internal exception and database details must not leak to API callers."""

import json

import pytest
from fastapi import Request
from sqlalchemy.exc import OperationalError

from futuris.api.app import app
from futuris.api.errors import storage_error_response


@pytest.mark.asyncio
async def test_unhandled_exception_response_does_not_echo_internal_message():
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/internal-test",
        "headers": [(b"x-request-id", b"fut-test-123")],
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "scheme": "http",
    })
    secret_detail = "postgresql://user:super-secret@db/private: password authentication failed"
    handler = app.exception_handlers[Exception]

    response = await handler(request, RuntimeError(secret_detail))
    body = response.body.decode()

    assert response.status_code == 500
    assert secret_detail not in body
    assert "super-secret" not in body
    payload = json.loads(body)
    assert payload["error"]["code"] == "internal_server_error"
    assert payload["error"]["details"]["request_id"] == "fut-test-123"


def test_database_error_response_does_not_leak_paths_or_credentials():
    secret_detail = "unable to open database file /home/service/.env: postgresql://u:super-secret@db/app"
    error = OperationalError("statement", {}, RuntimeError(secret_detail))

    response = storage_error_response(error)
    body = response.body.decode()

    assert response.status_code == 503
    assert secret_detail not in body
    assert "/home/service/.env" not in body
    assert "super-secret" not in body
    assert "dialect_error" not in body
