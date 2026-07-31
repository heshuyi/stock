"""Disk-backed settings/portfolio so memory Mongo survives process restarts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.models import Portfolio, UserSettings


def user_state_path() -> Path:
    market_db = Path(get_settings().market_db_path)
    return market_db.parent / "user_state.json"


def load_user_state() -> dict[str, Any] | None:
    path = user_state_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def save_user_state(
    *,
    settings: UserSettings | None = None,
    portfolio: Portfolio | None = None,
) -> None:
    path = user_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = load_user_state() or {}
    if settings is not None:
        current["settings"] = settings.model_dump()
    if portfolio is not None:
        current["portfolio"] = portfolio.model_dump()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(current, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)
