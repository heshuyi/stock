"use client";

import { useEffect, useState } from "react";
import {
  Portfolio,
  SymbolInfo,
  UserSettings,
  ApiError,
  api,
  symbolLabel,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const DEFAULT_SYMBOLS = ["HS300", "ZZ500", "CYB200", "KCB50", "SZ50"];

export default function SettingsPage() {
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    Promise.all([api.symbols(), api.settings(), api.portfolio()])
      .then(([sym, set, port]) => {
        setSymbols(sym.symbols);
        setSettings({
          ...set,
          buy_frequency: set.buy_frequency || "monthly",
          weekly_weekday: set.weekly_weekday ?? 1,
          monthly_day: set.monthly_day ?? 1,
          cash_pool_enabled: set.cash_pool_enabled ?? false,
        });
        const ids = sym.symbols.map((s) => s.id);
        const holdings = (ids.length ? ids : DEFAULT_SYMBOLS).map((id) => {
          const existing = port.holdings.find((h) => h.symbol === id);
          return (
            existing || {
              symbol: id,
              shares: 0,
              cost_price: 0,
              market_value: null,
              take_profit_stage: 0,
              trend_state: null,
              trailing_armed: false,
              trail_peak_price: null,
            }
          );
        });
        setPortfolio({ ...port, holdings });
      })
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"));
  }, []);

  async function saveAll() {
    if (!settings || !portfolio) return;
    setMessage(null);
    setError(null);
    const localErrors: Record<string, string> = {};
    if (settings.ma_short >= settings.ma_long) {
      localErrors.ma_range = "短均线周期必须小于长均线周期";
    }
    if (
      settings.valuation_reduce_percentile >=
      settings.valuation_exit_percentile
    ) {
      localErrors.valuation_range = "估值武装线必须低于估值清仓线";
    }
    const targetWeights = settings.target_weights || {};
    const weightTotal = Object.values(targetWeights).reduce(
      (sum, weight) => sum + weight,
      0
    );
    if (Math.abs(weightTotal - 1) > 1e-6) {
      localErrors.target_weights = `目标权重合计必须为 1，当前为 ${weightTotal.toFixed(6)}`;
    }
    setFieldErrors(localErrors);
    if (Object.keys(localErrors).length) {
      setError("请修正标注的设置后再保存");
      return;
    }
    try {
      await api.saveSettings(settings);
      await api.savePortfolio(portfolio);
      setMessage("已保存设置与持仓（写入本地 user_state.json，重启后仍保留）");
    } catch (e) {
      if (e instanceof ApiError) setFieldErrors(e.fieldErrors);
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  if (!settings || !portfolio) {
    return (
      <main>
        <h1 className="font-display text-2xl sm:text-3xl">持仓与设置</h1>
        <p className="mt-4 text-ink/60">{error || "加载中…"}</p>
      </main>
    );
  }

  const weights =
    settings.target_weights ||
    Object.fromEntries(symbols.map((s) => [s.id, s.target_weight]));

  return (
    <main>
      <h1 className="font-display text-2xl text-ink sm:text-3xl">持仓与设置</h1>
      <p className="mt-1 text-sm text-ink/60">
        月预算 + 定投频率（系统自动折算本期金额）、可支配储备与持仓
      </p>

      <section className="mt-6 rounded-xl border border-ink/10 bg-white/70 p-4 sm:mt-8 sm:p-5">
        <h2 className="font-display text-xl">全局参数</h2>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="base-amount">月定投总预算（元）</Label>
            <Input
              id="base-amount"
              type="number"
              min={0}
              max={1000000000}
              aria-invalid={Boolean(fieldErrors.base_amount)}
              className="h-11 bg-white"
              value={settings.base_amount}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  base_amount: Number(e.target.value),
                })
              }
            />
            <p className="text-xs text-ink/45">
              不随频率改变含义；每日/每周会按当月交易日或周数拆分
            </p>
            {fieldErrors.base_amount && (
              <p className="text-xs text-red-600">{fieldErrors.base_amount}</p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="buy-frequency">定投频率</Label>
            <Select
              value={settings.buy_frequency}
              onValueChange={(value) =>
                setSettings({
                  ...settings,
                  buy_frequency: value as "daily" | "weekly" | "monthly",
                })
              }
            >
              <SelectTrigger id="buy-frequency" className="h-11 w-full bg-white">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="daily">每日（每个交易日）</SelectItem>
                <SelectItem value="weekly">每周</SelectItem>
                <SelectItem value="monthly">每月</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {settings.buy_frequency === "weekly" && (
            <div className="space-y-1.5">
              <Label htmlFor="weekly-weekday">每周定投日</Label>
              <Select
                value={String(settings.weekly_weekday)}
                onValueChange={(value) =>
                  setSettings({
                    ...settings,
                    weekly_weekday: Number(value),
                  })
                }
              >
                <SelectTrigger
                  id="weekly-weekday"
                  className="h-11 w-full bg-white"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="1">周一</SelectItem>
                  <SelectItem value="2">周二</SelectItem>
                  <SelectItem value="3">周三</SelectItem>
                  <SelectItem value="4">周四</SelectItem>
                  <SelectItem value="5">周五</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-ink/45">遇休市顺延至本周下一交易日</p>
            </div>
          )}

          {settings.buy_frequency === "monthly" && (
            <div className="space-y-1.5">
              <Label htmlFor="monthly-day">每月定投日（1–28）</Label>
              <Input
                id="monthly-day"
                type="number"
                min={1}
                max={28}
                className="h-11 bg-white"
                value={settings.monthly_day}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    monthly_day: Number(e.target.value),
                  })
                }
              />
              <p className="text-xs text-ink/45">遇休市顺延至本月下一交易日</p>
            </div>
          )}

          <label className="flex min-h-11 items-center gap-3 text-sm sm:col-span-2">
            <input
              type="checkbox"
              className="h-5 w-5 shrink-0 accent-moss"
              checked={settings.cash_pool_enabled}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  cash_pool_enabled: e.target.checked,
                })
              }
            />
            启用现金池弹药调节（启用后，现金余额会直接缩放定投额度）
          </label>

          <div className="space-y-1.5">
            <Label htmlFor="cash">可支配定投储备（元）</Label>
            <Input
              id="cash"
              type="number"
              min={0}
              max={1000000000000}
              disabled={!settings.cash_pool_enabled}
              aria-invalid={Boolean(fieldErrors.cash)}
              className="h-11 bg-white"
              value={portfolio.cash}
              onChange={(e) =>
                setPortfolio({
                  ...portfolio,
                  cash: Number(e.target.value),
                })
              }
            />
            <p className="text-xs text-ink/45">
              以 36 个月预算为满额基准；启用时 0 表示现金池为空，最低按 0.35× 缩放
            </p>
            {fieldErrors.cash && (
              <p className="text-xs text-red-600">{fieldErrors.cash}</p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="buy-cap">买入预算上限倍数</Label>
            <Input
              id="buy-cap"
              type="number"
              step="0.1"
              min={0}
              max={10}
              aria-invalid={Boolean(fieldErrors.normalize_buy_cap)}
              className="h-11 bg-white"
              value={settings.normalize_buy_cap}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  normalize_buy_cap: Number(e.target.value),
                })
              }
            />
            {fieldErrors.normalize_buy_cap && (
              <p className="text-xs text-red-600">
                {fieldErrors.normalize_buy_cap}
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ma-short">短均线（MA）</Label>
            <Input
              id="ma-short"
              type="number"
              min={1}
              max={2000}
              aria-invalid={Boolean(fieldErrors.ma_short || fieldErrors.ma_range)}
              className="h-11 bg-white"
              value={settings.ma_short}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  ma_short: Number(e.target.value),
                })
              }
            />
            {fieldErrors.ma_short && (
              <p className="text-xs text-red-600">{fieldErrors.ma_short}</p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ma-long">长均线（MA）</Label>
            <Input
              id="ma-long"
              type="number"
              min={2}
              max={2000}
              aria-invalid={Boolean(fieldErrors.ma_long || fieldErrors.ma_range)}
              className="h-11 bg-white"
              value={settings.ma_long}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  ma_long: Number(e.target.value),
                })
              }
            />
            {fieldErrors.ma_long && (
              <p className="text-xs text-red-600">{fieldErrors.ma_long}</p>
            )}
          </div>

          <p className="text-xs text-ink/50 sm:col-span-2">
            修改均线参数后需重新「同步行情」才会重算入库指标。
          </p>
          {(fieldErrors.ma_range || fieldErrors._form) && (
            <p className="text-xs text-red-600 sm:col-span-2">
              {fieldErrors.ma_range || fieldErrors._form}
            </p>
          )}

          <label className="flex min-h-11 items-center gap-3 text-sm sm:col-span-2">
            <input
              type="checkbox"
              className="h-5 w-5 shrink-0 accent-moss"
              checked={settings.hard_veto_enabled}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  hard_veto_enabled: e.target.checked,
                })
              }
            />
            启用硬否决（关闭后：估值 pause 与成长仓空头硬停均可被突破，买入侧风控变弱）
          </label>

          <label className="flex min-h-11 items-center gap-3 text-sm sm:col-span-2">
            <input
              type="checkbox"
              className="h-5 w-5 shrink-0 accent-moss"
              checked={settings.profit_take_enabled}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  profit_take_enabled: e.target.checked,
                })
              }
            />
            启用估值武装 + 追踪回撤止盈
          </label>

          {settings.profit_take_enabled && (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="val-reduce">估值武装线（PE 复合分位 %）</Label>
                <Input
                  id="val-reduce"
                  type="number"
                  min={50}
                  max={99}
                  step={1}
                  aria-invalid={Boolean(
                    fieldErrors.valuation_reduce_percentile ||
                      fieldErrors.valuation_range
                  )}
                  className="h-11 bg-white"
                  value={Math.round(settings.valuation_reduce_percentile * 100)}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      valuation_reduce_percentile: Number(e.target.value) / 100,
                    })
                  }
                />
                <p className="text-xs text-ink/45">
                  全局唯一武装线（覆盖所有标的）；默认 80%
                </p>
                {fieldErrors.valuation_reduce_percentile && (
                  <p className="text-xs text-red-600">
                    {fieldErrors.valuation_reduce_percentile}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="val-exit">估值清仓线（PE 复合分位 %）</Label>
                <Input
                  id="val-exit"
                  type="number"
                  min={80}
                  max={100}
                  step={1}
                  aria-invalid={Boolean(
                    fieldErrors.valuation_exit_percentile ||
                      fieldErrors.valuation_range
                  )}
                  className="h-11 bg-white"
                  value={Math.round(settings.valuation_exit_percentile * 100)}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      valuation_exit_percentile: Number(e.target.value) / 100,
                    })
                  }
                />
                <p className="text-xs text-ink/45">
                  全局唯一清仓线；默认 90%。回撤幅度按角色模板（核心
                  10% / 成长 8%），不在此页配置
                </p>
                {fieldErrors.valuation_exit_percentile && (
                  <p className="text-xs text-red-600">
                    {fieldErrors.valuation_exit_percentile}
                  </p>
                )}
              </div>
              {fieldErrors.valuation_range && (
                <p className="text-xs text-red-600 sm:col-span-2">
                  {fieldErrors.valuation_range}
                </p>
              )}
            </>
          )}

          <p className="text-xs text-ink/50 sm:col-span-2">
            买入仅在所选频率的执行日产生金额；止盈每日检查。不使用账户收益率硬触发。
            核心仓破位软降频；成长仓空头排列硬停，极端低估可 0.25× 解封。
          </p>
        </div>

        <h3 className="mt-6 text-sm font-semibold text-ink/70">目标权重</h3>
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          {symbols.map((s) => (
            <div key={s.id} className="space-y-1.5">
              <Label htmlFor={`weight-${s.id}`}>
                {symbolLabel(s.name, s.etf_code, s.id)}（
                {((weights[s.id] ?? s.target_weight) * 100).toFixed(0)}%）
              </Label>
              <Input
                id={`weight-${s.id}`}
                type="number"
                step="0.01"
                min={0}
                max={1}
                aria-invalid={Boolean(
                  fieldErrors[`target_weights.${s.id}`] ||
                    fieldErrors.target_weights
                )}
                className="h-11 bg-white"
                value={weights[s.id] ?? s.target_weight}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    target_weights: {
                      ...weights,
                      [s.id]: Number(e.target.value),
                    },
                  })
                }
              />
              {fieldErrors[`target_weights.${s.id}`] && (
                <p className="text-xs text-red-600">
                  {fieldErrors[`target_weights.${s.id}`]}
                </p>
              )}
            </div>
          ))}
        </div>
        {fieldErrors.target_weights && (
          <p className="mt-2 text-xs text-red-600">
            {fieldErrors.target_weights}
          </p>
        )}
      </section>

      <section className="mt-6 rounded-xl border border-ink/10 bg-white/70 p-4 sm:p-5">
        <h2 className="font-display text-xl">持仓录入</h2>
        <p className="mt-1 text-xs text-ink/50">
          份额与成本价使用 ETF 真实收盘价计算收益；执行止盈后请更新止盈阶段
        </p>
        <div className="mt-4 space-y-3">
          {portfolio.holdings.map((h, idx) => {
            const sym = symbols.find((s) => s.id === h.symbol);
            const name = symbolLabel(
              sym?.name || h.symbol,
              sym?.etf_code,
              h.symbol
            );
            return (
              <div
                key={h.symbol}
                className="grid grid-cols-2 gap-2 rounded-lg border border-ink/8 bg-paper/40 p-3 sm:grid-cols-4"
              >
                <p className="col-span-2 text-sm font-medium sm:col-span-4">
                  {name}
                </p>
                <div className="space-y-1.5">
                  <Label htmlFor={`shares-${h.symbol}`}>份额</Label>
                  <Input
                    id={`shares-${h.symbol}`}
                    type="number"
                    min={0}
                    max={1000000000000}
                    aria-invalid={Boolean(fieldErrors[`holdings.${idx}.shares`])}
                    className="h-11 bg-white"
                    value={h.shares}
                    onChange={(e) => {
                      const next = [...portfolio.holdings];
                      next[idx] = { ...h, shares: Number(e.target.value) };
                      setPortfolio({ ...portfolio, holdings: next });
                    }}
                  />
                  {fieldErrors[`holdings.${idx}.shares`] && (
                    <p className="text-xs text-red-600">
                      {fieldErrors[`holdings.${idx}.shares`]}
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`cost-${h.symbol}`}>成本价</Label>
                  <Input
                    id={`cost-${h.symbol}`}
                    type="number"
                    min={0}
                    max={10000000}
                    aria-invalid={Boolean(
                      fieldErrors[`holdings.${idx}.cost_price`]
                    )}
                    className="h-11 bg-white"
                    value={h.cost_price}
                    onChange={(e) => {
                      const next = [...portfolio.holdings];
                      next[idx] = { ...h, cost_price: Number(e.target.value) };
                      setPortfolio({ ...portfolio, holdings: next });
                    }}
                  />
                  {fieldErrors[`holdings.${idx}.cost_price`] && (
                    <p className="text-xs text-red-600">
                      {fieldErrors[`holdings.${idx}.cost_price`]}
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`mv-${h.symbol}`}>市值（可选）</Label>
                  <Input
                    id={`mv-${h.symbol}`}
                    type="number"
                    min={0}
                    max={1000000000000}
                    aria-invalid={Boolean(
                      fieldErrors[`holdings.${idx}.market_value`]
                    )}
                    className="h-11 bg-white"
                    value={h.market_value ?? ""}
                    onChange={(e) => {
                      const next = [...portfolio.holdings];
                      const v = e.target.value;
                      next[idx] = {
                        ...h,
                        market_value: v === "" ? null : Number(v),
                      };
                      setPortfolio({ ...portfolio, holdings: next });
                    }}
                  />
                  {fieldErrors[`holdings.${idx}.market_value`] && (
                    <p className="text-xs text-red-600">
                      {fieldErrors[`holdings.${idx}.market_value`]}
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`tp-${h.symbol}`}>已执行止盈阶段</Label>
                  <Select
                    value={String(h.take_profit_stage)}
                    onValueChange={(value) => {
                      const next = [...portfolio.holdings];
                      next[idx] = {
                        ...h,
                        take_profit_stage: Number(value),
                      };
                      setPortfolio({ ...portfolio, holdings: next });
                    }}
                  >
                    <SelectTrigger
                      id={`tp-${h.symbol}`}
                      className="h-11 w-full bg-white"
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="0">未止盈</SelectItem>
                      <SelectItem value="1">已执行第一档 50%</SelectItem>
                      <SelectItem value="2">已清仓</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <div
        className="sticky bottom-14 z-30 mt-6 flex flex-col gap-2 rounded-xl border border-ink/10 bg-paper/95 p-3 shadow-sm backdrop-blur sm:static sm:bottom-auto sm:flex-row sm:items-center sm:gap-4 sm:border-0 sm:bg-transparent sm:p-0 sm:shadow-none sm:backdrop-blur-none"
        style={{ marginBottom: "env(safe-area-inset-bottom, 0px)" }}
      >
        <Button
          type="button"
          onClick={saveAll}
          className="h-11 w-full bg-moss text-white hover:bg-moss/90 sm:w-auto"
        >
          保存
        </Button>
        {message && <p className="text-sm text-moss">{message}</p>}
        {error && <p className="text-sm text-red-600">{error}</p>}
      </div>
    </main>
  );
}
