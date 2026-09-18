"""Shared rolling health-probe statistics.

Extracted from the near-identical window logic in ``reputation.py`` and
``download_intel.py`` — one implementation, same 30-day window.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from omnisource.domain import today_date
from omnisource.utils.dates import parse_date

WINDOW_DAYS = 30


def probe_window(app_state: dict[str, Any]) -> tuple[int, int, float | None]:
    """Return ``(total, reachable, average_latency_ms)`` over the rolling window.

    ``app_state`` is the per-app pipeline state block (``state.json``) that
    carries ``healthHistory``.
    """
    history = app_state.get("healthHistory") or []
    if not isinstance(history, list):
        return 0, 0, None
    cutoff = today_date() - timedelta(days=WINDOW_DAYS)
    total = 0
    reachable = 0
    latencies: list[int] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        when = parse_date(item.get("date") or item.get("at"))
        if when is None or when < cutoff:
            continue
        total += 1
        if item.get("reachable") is True:
            reachable += 1
        latency = item.get("latencyMs")
        if isinstance(latency, (int, float)):
            latencies.append(int(latency))
    average = sum(latencies) / len(latencies) if latencies else None
    return total, reachable, average
