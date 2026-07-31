from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Action = Literal["buy", "pause", "reduce", "hold"]
TrendState = Literal["bull", "mild_bull", "sandwich", "bear"]
ValuationMode = Literal["pe", "pe_pb_composite"]
PercentileWindow = Literal["5y", "full"]


class TrendMults(BaseModel):
    bull: float = 1.0
    mild_bull: float = 0.85
    sandwich: float = 0.55
    bear: float = 0.35


class StrategyProfile(BaseModel):
    valuation_mode: ValuationMode = "pe"
    pe_weight: float = 0.55
    pb_weight: float = 0.45
    percentile_window: PercentileWindow = "5y"
    pause_percentile: float = 0.80
    # multipliers for p bands: <20%, 20-40%, 40-60%, 60-pause
    tier_mults: list[float] = Field(default_factory=lambda: [2.0, 1.5, 1.0, 0.5])
    trend_hard_veto: bool = False
    trend_hysteresis: bool = False
    trend_mults: TrendMults = Field(default_factory=TrendMults)
    oversold_unlock: bool = False
    oversold_p: float = 0.15
    oversold_bias: float = -0.12
    oversold_mult: float = 0.25
    strategy_weights: dict[str, float] = Field(
        default_factory=lambda: {"valuation": 0.7, "trend": 0.3}
    )
    trail_arm_percentile: float = 0.80
    trail_drawdown: float = 0.08
    trail_exit_percentile: float = 0.90
    trail_disarm_gap: float = 0.05


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
    strategy_profile: StrategyProfile = Field(default_factory=StrategyProfile)


class StrategyDefaults(BaseModel):
    base_amount: float = 10000
    ma_short: int = 60
    ma_long: int = 120
    hard_veto_enabled: bool = True
    normalize_buy_cap: float = 1.5
    minimum_invest_ratio: float = 0.0
    buy_frequency: Literal["daily", "weekly"] = "daily"
    profit_take_enabled: bool = True
    profit_take_return: float = 0.30
    valuation_reduce_percentile: float = 0.80
    valuation_exit_percentile: float = 0.90
    cash_reserve_months: int = 36
    strategy_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "valuation": 0.7,
            "trend": 0.3,
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
    trend_state: TrendState | None = None
    trailing_armed: bool = False
    trail_peak_price: float | None = None


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
    buy_frequency: Literal["daily", "weekly"] = "daily"
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
    pool_factor: float | None = None
    disclaimer: str = (
        "策略信号仅供学习与个人研究参考，不构成投资建议；"
        "历史分位与均线不能预测未来收益。"
    )
