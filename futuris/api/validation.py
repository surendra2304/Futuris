"""Shared request-validation helpers for the API routers.

Caller-supplied ``context`` dictionaries reach numeric code paths deep inside
the pipeline. Validating them once, here, keeps every route that accepts a
context honest about what it accepts: a non-numeric ``point_estimate`` is a
422 at the door, not a 500 from ``float("not-a-number")`` inside the engine.
"""

from __future__ import annotations

from typing import Any

#: Context keys the forecasting path coerces to float. Anything else in
#: ``context`` is carried through as a labelled driver and never coerced.
NUMERIC_CONTEXT_KEYS = ("point_estimate", "current_value", "probability")


def validate_context_numeric_keys(value: dict[str, Any]) -> dict[str, Any]:
    """Reject non-numeric caller estimates with a ValueError (-> 422).

    Strings that parse as floats are coerced; booleans, NaN, infinities and
    unparseable strings are rejected. ``probability`` must additionally be in
    [0, 1].
    """
    cleaned: dict[str, Any] = dict(value)
    for key in NUMERIC_CONTEXT_KEYS:
        raw = cleaned.get(key)
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise ValueError(f"context.{key} must be a number")
        try:
            cleaned[key] = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"context.{key} must be a number, got {raw!r}") from exc
        if key == "probability" and not 0.0 <= cleaned[key] <= 1.0:
            raise ValueError(
                f"context.probability must be between 0.0 and 1.0, got {cleaned[key]}"
            )
        if cleaned[key] != cleaned[key] or cleaned[key] in (float("inf"), float("-inf")):
            raise ValueError(f"context.{key} must be a finite number")
    return cleaned
