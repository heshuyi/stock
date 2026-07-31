from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Action = Literal["buy", "pause", "reduce", "hold"]


class SymbolConfig(BaseModel):
    id: str
    name: str
    etf_code: str
    index_code: str
    akshare_symbol: str
    target_weight: float
    role: str = ""
    pe_symbol: str | None = None
    pb_symbol: str | None = None
    valuation_enabled: bool = True
    valuation_proxy: bool = False
    valuation_proxy_label: str | None = None


class StrategyDefaults(BaseModel):
    base_amount: float = 10000
    ma_short: int = 60
    ma_long: int = 120
    hard_veto_enabled: bool = True
    normalize_buy_cap: float = 1.5
    minimum_invest_ratio: float = 0.0
    buy_frequency: Literal["weekly"] = "weekly"
    profit_take_enabled: bool = True
    profit_take_return: float = 0.30
    valuation_reduce_percentile: float = 0.80
    valuation_exit_percentile: float = 0.90
    strategy_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "valuation": 0.6,
            "trend": 0.4,
        }
    )


class AppConfig(BaseModel):
    symbols: list[SymbolConfig]
    defaults: StrategyDefaults


class StrategySignal(BaseModel):
    strategy: str
    symbol: str
    action: Action
    multiplier: float
    confidence: float = 1.0
    reason: str
    reduce_ratio: float | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class EnsembleResult(BaseModel):
    symbol: str
    name: str
    etf_code: str
    target_weight: float
    action: Action
    multiplier: float
    amount: float
    reduce_ratio: float | None = None
    reason: str
    strategies: list[StrategySignal]
    hard_veto: bool = False


class Holding(BaseModel):
    symbol: str
    shares: float = 0
    cost_price: float = 0
    market_value: float | None = None
    take_profit_stage: int = Field(default=0, ge=0, le=2)


class Portfolio(BaseModel):
    holdings: list[Holding] = Field(default_factory=list)
    cash: float = 0


class UserSettings(BaseModel):
    base_amount: float = 10000
    hard_veto_enabled: bool = True
    normalize_buy_cap: float = 1.5
    target_weights: dict[str, float] | None = None
    ma_short: int = 60
    ma_long: int = 120
    buy_frequency: Literal["weekly"] = "weekly"
    profit_take_enabled: bool = True
    profit_take_return: float = 0.30
    valuation_reduce_percentile: float = 0.80
    valuation_exit_percentile: float = 0.90


class DashboardResponse(BaseModel):
    date: str
    base_amount: float
    total_buy_amount: float
    normalized: bool
    items: list[EnsembleResult]
    warning: str | None = None
    disclaimer: str = (
        "策略信号仅供学习与个人研究参考，不构成投资建议；"
        "历史分位与均线不能预测未来收益。"
    )
