from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.models import AppConfig, Portfolio, UserSettings

_client: Any = None


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
    path = Path(get_settings().config_path)
    with path.open(encoding="utf-8") as f:
        return AppConfig.model_validate(json.load(f))


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


async def seed_symbols_and_settings() -> None:
    db = get_db()
    cfg = load_app_config()
    for sym in cfg.symbols:
        await db.symbols.update_one(
            {"id": sym.id},
            {"$set": sym.model_dump()},
            upsert=True,
        )
    existing = await db.settings.find_one({"_id": "default"})
    if not existing:
        defaults = UserSettings(
            base_amount=cfg.defaults.base_amount,
            hard_veto_enabled=cfg.defaults.hard_veto_enabled,
            normalize_buy_cap=cfg.defaults.normalize_buy_cap,
            ma_short=cfg.defaults.ma_short,
            ma_long=cfg.defaults.ma_long,
            buy_frequency=cfg.defaults.buy_frequency,
            profit_take_enabled=cfg.defaults.profit_take_enabled,
            profit_take_return=cfg.defaults.profit_take_return,
            valuation_reduce_percentile=cfg.defaults.valuation_reduce_percentile,
            valuation_exit_percentile=cfg.defaults.valuation_exit_percentile,
            target_weights={s.id: s.target_weight for s in cfg.symbols},
        )
        await db.settings.insert_one(
            {"_id": "default", **defaults.model_dump()}
        )
    portfolio = await db.portfolio.find_one({"_id": "default"})
    if not portfolio:
        await db.portfolio.insert_one(
            {"_id": "default", **Portfolio().model_dump()}
        )


async def get_user_settings() -> UserSettings:
    db = get_db()
    doc = await db.settings.find_one({"_id": "default"})
    if not doc:
        cfg = load_app_config()
        return UserSettings(
            base_amount=cfg.defaults.base_amount,
            hard_veto_enabled=cfg.defaults.hard_veto_enabled,
            normalize_buy_cap=cfg.defaults.normalize_buy_cap,
            buy_frequency=cfg.defaults.buy_frequency,
            profit_take_enabled=cfg.defaults.profit_take_enabled,
            profit_take_return=cfg.defaults.profit_take_return,
            valuation_reduce_percentile=cfg.defaults.valuation_reduce_percentile,
            valuation_exit_percentile=cfg.defaults.valuation_exit_percentile,
            target_weights={s.id: s.target_weight for s in cfg.symbols},
        )
    doc.pop("_id", None)
    cfg = load_app_config()
    weights = dict(doc.get("target_weights") or {})
    # One-time compatibility for the CYB (399006) -> CYB200 (399019)
    # replacement. Holdings are intentionally not migrated because they are
    # different ETFs, but the user's allocation preference can carry over.
    if "CYB200" not in weights and "CYB" in weights:
        weights["CYB200"] = weights["CYB"]
    # The user approved adding KCB50 at 10% and reducing HS300/ZZ500 to
    # 30% each. Apply that complete allocation once for pre-KCB50 settings.
    if "KCB50" not in weights:
        weights = {symbol.id: symbol.target_weight for symbol in cfg.symbols}
    doc["target_weights"] = {
        symbol.id: float(weights.get(symbol.id, symbol.target_weight))
        for symbol in cfg.symbols
    }
    return UserSettings.model_validate(doc)


async def save_user_settings(settings: UserSettings) -> UserSettings:
    db = get_db()
    await db.settings.update_one(
        {"_id": "default"},
        {"$set": settings.model_dump()},
        upsert=True,
    )
    return settings


async def get_portfolio() -> Portfolio:
    db = get_db()
    doc = await db.portfolio.find_one({"_id": "default"})
    if not doc:
        return Portfolio()
    doc.pop("_id", None)
    return Portfolio.model_validate(doc)


async def save_portfolio(portfolio: Portfolio) -> Portfolio:
    db = get_db()
    await db.portfolio.update_one(
        {"_id": "default"},
        {"$set": portfolio.model_dump()},
        upsert=True,
    )
    return portfolio
