from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)


Action = Literal["buy", "pause", "reduce", "hold"]
BuyFrequency = Literal["daily", "weekly", "monthly"]
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
    pause_percentile: float = Field(default=0.80, gt=0, le=1)
    # valuation percentile band boundaries (configurable; defaults keep legacy 20/40/60)
    band_low: float = Field(default=0.20, ge=0, le=1)
    band_mid: float = Field(default=0.40, ge=0, le=1)
    band_high: float = Field(default=0.60, ge=0, le=1)
    # multipliers for p bands: <band_low, band_low-mid, mid-high, high-pause
    tier_mults: list[float] = Field(default_factory=lambda: [2.0, 1.5, 1.0, 0.5])
    trend_hard_veto: bool = False
    trend_hysteresis: bool = False
    trend_mults: TrendMults = Field(default_factory=TrendMults)
    # optional soft-growth mode: bear regime keeps buying at this multiplier
    # instead of a hard veto (must be in (0, 1); None keeps the hard veto)
    bear_soft_mult: float | None = Field(default=None, ge=0, le=1)
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
    # after a full-exit stage, buys stay locked until valuation cools below
    # (exit_percentile - reentry_gap)
    reentry_gap: float = Field(default=0.10, ge=0, le=1)
    # when True, the weighted-average ensemble rescales so the configured
    # max_mult is actually reachable at full undervaluation + bull trend
    scale_to_cap: bool = True

    @model_validator(mode="after")
    def validate_bands(self) -> StrategyProfile:
        if not (
            0 <= self.band_low <= self.band_mid <= self.band_high
            < self.pause_percentile
        ):
            raise ValueError(
                "估值档位需满足 0 ≤ band_low ≤ band_mid ≤ band_high < pause_percentile"
            )
        if self.bear_soft_mult is not None and not 0 < self.bear_soft_mult < 1:
            raise ValueError("bear_soft_mult 必须在 (0, 1) 之间（None 表示维持硬否决）")
        if self.trail_arm_percentile >= self.trail_exit_percentile:
            raise ValueError("估值武装线必须低于估值清仓线")
        return self


class SymbolConfig(BaseModel):
    id: str
    name: str
    etf_code: str
    index_code: str
    akshare_symbol: str
    target_weight: float = Field(ge=0, le=1)
    role: str = ""
    pe_symbol: str | None = None
    pb_symbol: str | None = None
    valuation_enabled: bool = True
    valuation_proxy: bool = False
    valuation_proxy_label: str | None = None
    strategy_profile: StrategyProfile = Field(default_factory=StrategyProfile)


class StrategyDefaults(BaseModel):
    base_amount: float = Field(default=10000, ge=0, le=1_000_000_000)
    ma_short: int = Field(default=60, ge=1, le=2000)
    ma_long: int = Field(default=120, ge=2, le=2000)
    hard_veto_enabled: bool = True
    normalize_buy_cap: float = Field(default=1.5, ge=0, le=10)
    minimum_invest_ratio: float = Field(default=0.0, ge=0, le=1)
    buy_frequency: BuyFrequency = "monthly"
    weekly_weekday: int = Field(default=1, ge=1, le=5)
    monthly_day: int = Field(default=1, ge=1, le=28)
    profit_take_enabled: bool = True
    valuation_reduce_percentile: float = Field(default=0.80, ge=0, le=1)
    valuation_exit_percentile: float = Field(default=0.90, ge=0, le=1)
    cash_reserve_months: int = Field(default=36, ge=1, le=120)
    growth_bear_policy: Literal["hard_veto", "soft"] = "hard_veto"
    growth_bear_mult: float = Field(default=0.20, gt=0, lt=1)
    strategy_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "valuation": 0.7,
            "trend": 0.3,
        }
    )

    @model_validator(mode="after")
    def validate_ranges(self) -> StrategyDefaults:
        if self.ma_short >= self.ma_long:
            raise ValueError("短均线周期必须小于长均线周期")
        if self.valuation_reduce_percentile >= self.valuation_exit_percentile:
            raise ValueError("估值武装线必须低于估值清仓线")
        return self


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
    # 再平衡辅助字段（R8，仅有漂移时填充）
    actual_weight: float | None = None          # 当日实际持仓权重
    weight_drift: float | None = None           # 实际 − 目标（正=超配，负=低配）
    rebalance_reason: str | None = None         # 再平衡说明文字


class Holding(BaseModel):
    symbol: str
    shares: float = Field(default=0, ge=0, le=1_000_000_000_000)
    cost_price: float = Field(default=0, ge=0, le=10_000_000)
    market_value: float | None = Field(default=None, ge=0, le=1_000_000_000_000)
    take_profit_stage: int = Field(default=0, ge=0, le=2)
    trend_state: TrendState | None = None
    trailing_armed: bool = False
    trail_peak_price: float | None = None
    # 累计已收现金分红（把盈亏还原为含分红的总回报口径）
    dividends_received: float = Field(default=0, ge=0, le=1_000_000_000_000)

    @field_validator(
        "shares", "cost_price", "market_value", "dividends_received", mode="before"
    )
    @classmethod
    def validate_financial_value(cls, value: Any, info: ValidationInfo) -> Any:
        if value is None and info.field_name == "market_value":
            return value
        limits = {
            "shares": ("份额", 1_000_000_000_000),
            "cost_price": ("成本价", 10_000_000),
            "market_value": ("市值", 1_000_000_000_000),
            "dividends_received": ("累计分红", 1_000_000_000_000),
        }
        if isinstance(value, (int, float)):
            label, upper = limits[info.field_name]
            if value < 0 or value > upper:
                raise ValueError(f"{label}必须在 0 到 {upper:g} 之间")
        return value


TradeKind = Literal["deposit", "buy", "sell", "dividend"]


class TradeInput(BaseModel):
    """A single manual ledger entry to reconcile the tracked position."""

    symbol: str
    kind: TradeKind
    date: str | None = None
    # deposit: amount 入金；buy: amount 花费金额 + price；sell: shares 或 ratio + price
    # dividend: amount 现金分红 + reinvest 是否红利再投（再投需 price）
    amount: float | None = Field(default=None, ge=0)
    price: float | None = Field(default=None, ge=0)
    shares: float | None = Field(default=None, ge=0)
    ratio: float | None = Field(default=None, gt=0, le=1)
    reinvest: bool = False


class TradeRecord(TradeInput):
    applied_at: str = ""


class Portfolio(BaseModel):
    holdings: list[Holding] = Field(default_factory=list)
    cash: float = Field(default=0, ge=0, le=1_000_000_000_000)
    trades: list[TradeRecord] = Field(default_factory=list)

    @field_validator("cash", mode="before")
    @classmethod
    def validate_cash(cls, value: Any) -> Any:
        if isinstance(value, (int, float)) and not 0 <= value <= 1_000_000_000_000:
            raise ValueError("现金必须在 0 到 1 万亿元之间")
        return value


class UserSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    KNOWN_SYMBOL_IDS: ClassVar[frozenset[str]] = frozenset(
        {"HS300", "ZZ500", "CYB200", "KCB50", "SZ50"}
    )

    base_amount: float = Field(default=10000, ge=0, le=1_000_000_000)
    hard_veto_enabled: bool = True
    normalize_buy_cap: float = Field(default=1.5, ge=0, le=10)
    target_weights: dict[str, float] | None = None
    ma_short: int = Field(default=60, ge=1, le=2000)
    ma_long: int = Field(default=120, ge=2, le=2000)
    buy_frequency: BuyFrequency = "monthly"
    weekly_weekday: int = Field(default=1, ge=1, le=5)
    monthly_day: int = Field(default=1, ge=1, le=28)
    profit_take_enabled: bool = True
    valuation_reduce_percentile: float = Field(default=0.80, ge=0, le=1)
    valuation_exit_percentile: float = Field(default=0.90, ge=0, le=1)
    cash_pool_enabled: bool = False
    # 成长仓空头排列策略：hard_veto=防守（硬停）/ soft=追收益（按 growth_bear_mult 软降频）
    growth_bear_policy: Literal["hard_veto", "soft"] = "hard_veto"
    growth_bear_mult: float = Field(default=0.20, gt=0, lt=1)
    # 提醒推送（R7）
    notify_enabled: bool = False
    notify_url: str = ""
    notify_on_execution: bool = True
    notify_on_signal_change: bool = False

    @field_validator(
        "base_amount",
        "normalize_buy_cap",
        "ma_short",
        "ma_long",
        "valuation_reduce_percentile",
        "valuation_exit_percentile",
        mode="before",
    )
    @classmethod
    def validate_setting_bounds(cls, value: Any, info: ValidationInfo) -> Any:
        limits = {
            "base_amount": ("月定投预算", 0, 1_000_000_000),
            "normalize_buy_cap": ("买入预算上限倍数", 0, 10),
            "ma_short": ("短均线周期", 1, 2000),
            "ma_long": ("长均线周期", 2, 2000),
            "valuation_reduce_percentile": ("估值武装线", 0, 1),
            "valuation_exit_percentile": ("估值清仓线", 0, 1),
        }
        if isinstance(value, (int, float)):
            label, lower, upper = limits[info.field_name]
            if value < lower or value > upper:
                raise ValueError(f"{label}必须在 {lower:g} 到 {upper:g} 之间")
        return value

    @field_validator("target_weights")
    @classmethod
    def validate_target_weights(
        cls, weights: dict[str, float] | None
    ) -> dict[str, float] | None:
        if weights is None:
            return None
        unknown = sorted(set(weights) - cls.KNOWN_SYMBOL_IDS)
        if unknown:
            raise ValueError(f"目标权重包含未知标的：{'、'.join(unknown)}")
        if set(weights) != cls.KNOWN_SYMBOL_IDS:
            missing = sorted(cls.KNOWN_SYMBOL_IDS - set(weights))
            raise ValueError(f"目标权重缺少标的：{'、'.join(missing)}")
        invalid = [symbol for symbol, weight in weights.items() if not 0 <= weight <= 1]
        if invalid:
            raise ValueError(f"目标权重必须在 0 到 1 之间：{'、'.join(invalid)}")
        total = sum(weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"目标权重合计必须为 1，当前为 {total:.6f}")
        return weights

    @model_validator(mode="after")
    def validate_combinations(self) -> UserSettings:
        if self.ma_short >= self.ma_long:
            raise ValueError("短均线周期必须小于长均线周期")
        if self.valuation_reduce_percentile >= self.valuation_exit_percentile:
            raise ValueError("估值武装线必须低于估值清仓线")
        return self


class DashboardResponse(BaseModel):
    date: str
    base_amount: float
    period_amount: float = 0.0
    buy_frequency: BuyFrequency = "monthly"
    execution_today: bool = True
    next_execution_date: str | None = None
    total_buy_amount: float
    normalized: bool
    items: list[EnsembleResult]
    warning: str | None = None
    pool_factor: float | None = None
    disclaimer: str = (
        "策略信号仅供学习与个人研究参考，不构成投资建议；"
        "历史分位与均线不能预测未来收益。"
    )
