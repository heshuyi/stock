from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.models import AppConfig, Portfolio, UserSettings
from app.services.user_state import load_user_state, save_user_state

_client: Any = None
_config_cache: tuple[Path, int, AppConfig] | None = None


def get_client() -> Any:
    global _client
    if _client is None:
        settings = get_settings()
        uri = settings.mongodb_uri.strip().lower()
        if uri in {"memory", "mongomock", "mock"}:
            from mongomock_motor import AsyncMongoMockClient

            _client = AsyncMongoMockClient()
        else:
            from motor.motor_asyncio import AsyncIOMotorClient

            _client = AsyncIOMotorClient(settings.mongodb_uri)
    return _client


def get_db() -> Any:
    settings = get_settings()
    return get_client()[settings.mongodb_db]


def load_app_config() -> AppConfig:
    """Load symbols config, cached by path + mtime (cheap hot-path reads)."""
    global _config_cache
    path = Path(get_settings().config_path)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = None
    if _config_cache is not None and _config_cache[0] == path and _config_cache[1] == mtime_ns:
        return _config_cache[2]
    with path.open(encoding="utf-8") as f:
        cfg = AppConfig.model_validate(json.load(f))
    _config_cache = (path, mtime_ns, cfg)
    return cfg


async def ensure_indexes() -> None:
    db = get_db()
    await db.market_daily.create_index(
        [("symbol", 1), ("date", 1)], unique=True
    )
    await db.valuations.create_index(
        [("symbol", 1), ("date", 1)], unique=True
    )
    await db.signals_daily.create_index([("date", 1)], unique=True)
    await db.symbols.create_index([("id", 1)], unique=True)


def _default_settings(cfg: AppConfig) -> UserSettings:
    return UserSettings(
        base_amount=cfg.defaults.base_amount,
        hard_veto_enabled=cfg.defaults.hard_veto_enabled,
        normalize_buy_cap=cfg.defaults.normalize_buy_cap,
        ma_short=cfg.defaults.ma_short,
        ma_long=cfg.defaults.ma_long,
        buy_frequency=cfg.defaults.buy_frequency,
        weekly_weekday=cfg.defaults.weekly_weekday,
        monthly_day=cfg.defaults.monthly_day,
        profit_take_enabled=cfg.defaults.profit_take_enabled,
        valuation_reduce_percentile=cfg.defaults.valuation_reduce_percentile,
        valuation_exit_percentile=cfg.defaults.valuation_exit_percentile,
        cash_pool_enabled=False,
        growth_bear_policy=cfg.defaults.growth_bear_policy,
        growth_bear_mult=cfg.defaults.growth_bear_mult,
        notify_enabled=False,
        notify_url="",
        notify_on_execution=True,
        notify_on_signal_change=False,
        target_weights={s.id: s.target_weight for s in cfg.symbols},
    )


async def seed_symbols_and_settings() -> None:
    db = get_db()
    cfg = load_app_config()
    for sym in cfg.symbols:
        await db.symbols.update_one(
            {"id": sym.id},
            {"$set": sym.model_dump()},
            upsert=True,
        )

    # Restore disk snapshot first (critical for MONGODB_URI=memory restarts).
    disk = load_user_state() or {}
    disk_settings = disk.get("settings")
    disk_portfolio = disk.get("portfolio")

    existing = await db.settings.find_one({"_id": "default"})
    if disk_settings:
        try:
            restored = UserSettings.model_validate(disk_settings)
            await db.settings.update_one(
                {"_id": "default"},
                {"$set": restored.model_dump()},
                upsert=True,
            )
        except Exception:
            if not existing:
                defaults = _default_settings(cfg)
                await db.settings.insert_one(
                    {"_id": "default", **defaults.model_dump()}
                )
    elif not existing:
        defaults = _default_settings(cfg)
        await db.settings.insert_one(
            {"_id": "default", **defaults.model_dump()}
        )

    portfolio = await db.portfolio.find_one({"_id": "default"})
    if disk_portfolio:
        try:
            restored_p = Portfolio.model_validate(disk_portfolio)
            await db.portfolio.update_one(
                {"_id": "default"},
                {"$set": restored_p.model_dump()},
                upsert=True,
            )
        except Exception:
            if not portfolio:
                await db.portfolio.insert_one(
                    {"_id": "default", **Portfolio().model_dump()}
                )
    elif not portfolio:
        await db.portfolio.insert_one(
            {"_id": "default", **Portfolio().model_dump()}
        )

    # Ensure a disk file exists after first boot with current mongo docs.
    settings_doc = await db.settings.find_one({"_id": "default"})
    portfolio_doc = await db.portfolio.find_one({"_id": "default"})
    if settings_doc and portfolio_doc and not load_user_state():
        settings_doc.pop("_id", None)
        portfolio_doc.pop("_id", None)
        save_user_state(
            settings=UserSettings.model_validate(settings_doc),
            portfolio=Portfolio.model_validate(portfolio_doc),
        )


async def get_user_settings() -> UserSettings:
    db = get_db()
    doc = await db.settings.find_one({"_id": "default"})
    if not doc:
        cfg = load_app_config()
        return _default_settings(cfg)
    doc.pop("_id", None)
    cfg = load_app_config()
    weights = dict(doc.get("target_weights") or {})
    # CYB → CYB200 symbol rename: carry allocation forward only.
    if "CYB200" not in weights and "CYB" in weights:
        weights["CYB200"] = weights["CYB"]
    doc["target_weights"] = {
        symbol.id: float(weights.get(symbol.id, symbol.target_weight))
        for symbol in cfg.symbols
    }
    # Defaults for new schedule fields on older saved settings.
    if doc.get("buy_frequency") not in {"daily", "weekly", "monthly"}:
        doc["buy_frequency"] = cfg.defaults.buy_frequency
    if doc.get("weekly_weekday") is None:
        doc["weekly_weekday"] = cfg.defaults.weekly_weekday
    if doc.get("monthly_day") is None:
        doc["monthly_day"] = cfg.defaults.monthly_day
    if doc.get("cash_pool_enabled") is None:
        doc["cash_pool_enabled"] = False
    if doc.get("growth_bear_policy") not in {"hard_veto", "soft"}:
        doc["growth_bear_policy"] = cfg.defaults.growth_bear_policy
    if doc.get("growth_bear_mult") is None:
        doc["growth_bear_mult"] = cfg.defaults.growth_bear_mult
    if doc.get("notify_enabled") is None:
        doc["notify_enabled"] = False
    if doc.get("notify_url") is None:
        doc["notify_url"] = ""
    if doc.get("notify_on_execution") is None:
        doc["notify_on_execution"] = True
    if doc.get("notify_on_signal_change") is None:
        doc["notify_on_signal_change"] = False
    return UserSettings.model_validate(doc)


async def save_user_settings(settings: UserSettings) -> UserSettings:
    db = get_db()
    await db.settings.update_one(
        {"_id": "default"},
        {"$set": settings.model_dump()},
        upsert=True,
    )
    save_user_state(settings=settings)
    return settings


async def get_portfolio() -> Portfolio:
    db = get_db()
    doc = await db.portfolio.find_one({"_id": "default"})
    if not doc:
        return Portfolio()
    doc.pop("_id", None)
    return Portfolio.model_validate(doc)


async def save_portfolio(portfolio: Portfolio) -> Portfolio:
    normalized = portfolio.model_copy(deep=True)
    for holding in normalized.holdings:
        if holding.shares <= 0:
            holding.take_profit_stage = 0
            holding.trailing_armed = False
            holding.trail_peak_price = None
    db = get_db()
    await db.portfolio.update_one(
        {"_id": "default"},
        {"$set": normalized.model_dump()},
        upsert=True,
    )
    save_user_state(portfolio=normalized)
    return normalized
