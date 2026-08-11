"use client";

import { useEffect, useState } from "react";
import {
  Portfolio,
  SymbolInfo,
  UserSettings,
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

  useEffect(() => {
    Promise.all([api.symbols(), api.settings(), api.portfolio()])
      .then(([sym, set, port]) => {
        setSymbols(sym.symbols);
        setSettings({
          ...set,
          buy_frequency: set.buy_frequency || "monthly",
          weekly_weekday: set.weekly_weekday ?? 1,
          monthly_day: set.monthly_day ?? 1,
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
    try {
      await api.saveSettings(settings);
      await api.savePortfolio(portfolio);
      setMessage("已保存设置与持仓（写入本地 user_state.json，重启后仍保留）");
    } catch (e) {
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

          <div className="space-y-1.5">
            <Label htmlFor="cash">可支配定投储备（元）</Label>
            <Input
              id="cash"
              type="number"
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
              用于 36 个月弹药深度调节；填 0 表示不启用现金池缩放
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="buy-cap">买入预算上限倍数</Label>
            <Input
              id="buy-cap"
              type="number"
              step="0.1"
              className="h-11 bg-white"
              value={settings.normalize_buy_cap}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  normalize_buy_cap: Number(e.target.value),
                })
              }
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ma-short">短均线（MA）</Label>
            <Input
              id="ma-short"
              type="number"
              className="h-11 bg-white"
              value={settings.ma_short}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  ma_short: Number(e.target.value),
                })
              }
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ma-long">长均线（MA）</Label>
            <Input
              id="ma-long"
              type="number"
              className="h-11 bg-white"
              value={settings.ma_long}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  ma_long: Number(e.target.value),
                })
              }
            />
          </div>

          <p className="text-xs text-ink/50 sm:col-span-2">
            修改均线参数后需重新「同步行情」才会重算入库指标。
          </p>

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
            启用硬否决（估值 pause；成长仓空头排列时暂停，超跌可解封）
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
                  达到该分位后开启峰值追踪；默认 80%，全局覆盖各标的 profile
                </p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="val-exit">估值清仓线（PE 复合分位 %）</Label>
                <Input
                  id="val-exit"
                  type="number"
                  min={80}
                  max={100}
                  step={1}
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
                  达到该分位建议清仓级减持；默认 90%。追踪回撤阈值仍按各标的
                  profile
                </p>
              </div>
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
            </div>
          ))}
        </div>
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
                    className="h-11 bg-white"
                    value={h.shares}
                    onChange={(e) => {
                      const next = [...portfolio.holdings];
                      next[idx] = { ...h, shares: Number(e.target.value) };
                      setPortfolio({ ...portfolio, holdings: next });
                    }}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`cost-${h.symbol}`}>成本价</Label>
                  <Input
                    id={`cost-${h.symbol}`}
                    type="number"
                    className="h-11 bg-white"
                    value={h.cost_price}
                    onChange={(e) => {
                      const next = [...portfolio.holdings];
                      next[idx] = { ...h, cost_price: Number(e.target.value) };
                      setPortfolio({ ...portfolio, holdings: next });
                    }}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`mv-${h.symbol}`}>市值（可选）</Label>
                  <Input
                    id={`mv-${h.symbol}`}
                    type="number"
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
