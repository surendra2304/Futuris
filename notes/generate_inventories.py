"""Generate the endpoint and settings inventories used in REPO_ANALYSIS.md Appendix C.

Run from the repository root with the project environment:

    .venv/bin/python notes/generate_inventories.py > /tmp/inventories.md

Output is Markdown. Secret-bearing settings are printed as "secret" with no default
value, so the output is safe to paste into a report.
"""

from __future__ import annotations

import inspect
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("APP_ENV", "dev")

from futuris.api.app import app  # noqa: E402
from futuris.infra.config import Settings  # noqa: E402

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
SECRET_PATTERN = re.compile(r"(KEY|SECRET|PASSWORD|TOKEN)", re.IGNORECASE)
AUTH_LABELS = {
    "require_admin": "admin",
    "require_analyst": "analyst",
    "require_viewer": "viewer",
    "allow_anonymous_read": "anonymous read (budget 120/min)",
    "allow_anonymous_heavy_read": "anonymous heavy read (budget 6/min)",
    "verify_friday_auth": "FRIDAY key (100/h)",
    "get_current_user": "API key resolved",
}


def iter_operations():
    """Yield (method, path, endpoint, route) for every runtime route, expanding included routers."""
    seen: set[tuple[str, str]] = set()

    def walk(route):
        candidates = getattr(route, "effective_candidates", None)
        if callable(candidates):
            for candidate in candidates():
                yield from walk(candidate)
            return
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        endpoint = getattr(route, "endpoint", None)
        if isinstance(path, str) and endpoint is not None:
            for method in sorted(methods & HTTP_METHODS):
                key = (method, path)
                if key not in seen:
                    seen.add(key)
                    yield method, path, endpoint, route

    for route in app.routes:
        yield from walk(route)


def auth_of(route) -> str:
    """Name the auth dependencies a route uses, by walking its dependency graph."""
    labels: list[str] = []

    def visit(dependant) -> None:
        call = getattr(dependant, "call", None)
        name = getattr(call, "__name__", "")
        if name in AUTH_LABELS and AUTH_LABELS[name] not in labels:
            labels.append(AUTH_LABELS[name])
        for sub in getattr(dependant, "dependencies", []) or []:
            visit(sub)

    dependant = getattr(route, "dependant", None)
    if dependant is not None:
        visit(dependant)
    return ", ".join(labels) if labels else "none"


def source_of(endpoint) -> str:
    try:
        file = Path(inspect.getsourcefile(endpoint) or "").resolve().relative_to(ROOT)
        line = inspect.getsourcelines(endpoint)[1]
        return f"`{file}:{line}`"
    except (TypeError, OSError, ValueError):
        return "n/a"


def endpoint_table() -> str:
    rows = []
    for index, (method, path, endpoint, route) in enumerate(
        sorted(iter_operations(), key=lambda t: (t[1], t[0])), start=1
    ):
        handler = getattr(endpoint, "__name__", "?")
        cells = [
            str(index),
            method,
            f"`{path}`",
            f"`{handler}`",
            source_of(endpoint),
            auth_of(route),
        ]
        rows.append("| " + " | ".join(cells) + " |")
    header = (
        "| # | Method | Path | Handler | Source | Auth / guard dependencies |\n"
        "|---|---|---|---|---|---|"
    )
    return header + "\n" + "\n".join(rows)


def settings_table() -> str:
    """Six columns, matching REPO_ANALYSIS.md Appendix C-2."""
    config_lines = (ROOT / "futuris/infra/config.py").read_text(encoding="utf-8").splitlines()
    rows = []
    for name, field in Settings.model_fields.items():
        annotation = str(field.annotation)
        for noise in ("typing.", "<class '", "'>"):
            annotation = annotation.replace(noise, "")
        # A bare "|" inside a Markdown table cell starts a new column (e.g. "str | None").
        annotation = annotation.replace("|", "\\|")
        is_secret = bool(SECRET_PATTERN.search(name))
        if is_secret:
            default = "*(not shown)*"
        elif field.default is None:
            default = "unset"
        else:
            default = f"`{field.default!r}`"
        alias = field.validation_alias
        if alias is not None and hasattr(alias, "choices"):
            env_alias = ", ".join(f"`{choice}`" for choice in alias.choices)
        else:
            env_alias = f"`{name}`"
        line_no = next(
            (i for i, line in enumerate(config_lines, start=1) if line.startswith(f"    {name}:")),
            None,
        )
        source = f"`futuris/infra/config.py:{line_no}`" if line_no else "n/a"
        rows.append(
            f"| `{name}` | {annotation} | {default} | {env_alias} | {source} | "
            f"{'yes' if is_secret else 'no'} |"
        )
    header = (
        "| Setting | Type | Default | Env alias | Source | Secret |\n"
        "|---|---|---|---|---|---|"
    )
    return header + "\n" + "\n".join(rows)


if __name__ == "__main__":
    operations = list(iter_operations())
    report = "\n".join(
        [
            f"<!-- runtime operations: {len(operations)} -->",
            endpoint_table(),
            "",
            f"<!-- settings fields: {len(Settings.model_fields)} -->",
            settings_table(),
            "",
        ]
    )
    sys.stdout.write(report)
