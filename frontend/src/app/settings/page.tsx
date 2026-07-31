"use client";

import { useEffect, useState } from "react";
import {
  Portfolio,
  SymbolInfo,
  UserSettings,
  api,
} from "@/lib/api";

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
        setSettings(set);
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
      setMessage("已保存设置与持仓");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }

  if (!settings || !portfolio) {
    return (
      <main>
        <h1 className="font-display text-3xl">持仓与设置</h1>
        <p className="mt-4 text-ink/60">{error || "加载中…"}</p>
      </main>
    );
  }

  const weights = settings.target_weights || Object.fromEntries(
    symbols.map((s) => [s.id, s.target_weight])
  );

  return (
    <main>
      <h1 className="font-display text-3xl text-ink">持仓与设置</h1>
      <p className="mt-1 text-sm text-ink/60">
        修改月定投基准额、目标分配比例与当前持仓
      </p>

      <section className="mt-8 rounded-xl border border-ink/10 bg-white/70 p-5">
        <h2 className="font-display text-xl">全局参数</h2>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <label className="block text-sm">
            <span className="text-ink/60">月定投基准额（元）</span>
            <input
              type="number"
              className="mt-1 w-full rounded-md border border-ink/15 bg-paper/50 px-3 py-2"
              value={settings.base_amount}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  base_amount: Number(e.target.value),
                })
              }
            />
          </label>
          <label className="block text-sm">
            <span className="text-ink/60">买入预算上限倍数</span>
            <input
              type="number"
              step="0.1"
              className="mt-1 w-full rounded-md border border-ink/15 bg-paper/50 px-3 py-2"
              value={settings.normalize_buy_cap}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  normalize_buy_cap: Number(e.target.value),
                })
              }
            />
          </label>
          <label className="block text-sm">
            <span className="text-ink/60">短均线（MA）</span>
            <input
              type="number"
              className="mt-1 w-full rounded-md border border-ink/15 bg-paper/50 px-3 py-2"
              value={settings.ma_short}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  ma_short: Number(e.target.value),
                })
              }
            />
          </label>
          <label className="block text-sm">
            <span className="text-ink/60">长均线（MA）</span>
            <input
              type="number"
              className="mt-1 w-full rounded-md border border-ink/15 bg-paper/50 px-3 py-2"
              value={settings.ma_long}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  ma_long: Number(e.target.value),
                })
              }
            />
          </label>
          <p className="text-xs text-ink/50 sm:col-span-2">
            修改均线参数后需重新「同步行情」才会重算入库指标。
          </p>
          <label className="flex items-center gap-2 text-sm sm:col-span-2">
            <input
              type="checkbox"
              checked={settings.hard_veto_enabled}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  hard_veto_enabled: e.target.checked,
                })
              }
            />
            启用硬否决（估值高估或趋势破位时强制暂停买入）
          </label>
          <label className="flex items-center gap-2 text-sm sm:col-span-2">
            <input
              type="checkbox"
              checked={settings.profit_take_enabled}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  profit_take_enabled: e.target.checked,
                })
              }
            />
            启用估值与持仓收益双止盈
          </label>
          <label className="block text-sm">
            <span className="text-ink/60">收益率止盈线</span>
            <input
              type="number"
              step="0.01"
              min={0}
              max={2}
              value={settings.profit_take_return}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  profit_take_return: Number(e.target.value),
                })
              }
              className="mt-1 w-full rounded-md border border-ink/15 bg-paper/50 px-3 py-2"
            />
          </label>
          <label className="block text-sm">
            <span className="text-ink/60">第一档 PE 分位</span>
            <input
              type="number"
              step="0.01"
              min={0}
              max={1}
              value={settings.valuation_reduce_percentile}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  valuation_reduce_percentile: Number(e.target.value),
                })
              }
              className="mt-1 w-full rounded-md border border-ink/15 bg-paper/50 px-3 py-2"
            />
          </label>
          <label className="block text-sm">
            <span className="text-ink/60">清仓 PE 分位</span>
            <input
              type="number"
              step="0.01"
              min={0}
              max={1}
              value={settings.valuation_exit_percentile}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  valuation_exit_percentile: Number(e.target.value),
                })
              }
              className="mt-1 w-full rounded-md border border-ink/15 bg-paper/50 px-3 py-2"
            />
          </label>
          <p className="self-end pb-2 text-xs text-ink/50">
            买入频率：每周首个交易日；止盈信号每日检查。
          </p>
        </div>

        <h3 className="mt-6 text-sm font-semibold text-ink/70">目标权重</h3>
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          {symbols.map((s) => (
            <label key={s.id} className="block text-sm">
              <span className="text-ink/60">
                {s.name}（{(weights[s.id] ?? s.target_weight) * 100}%）
              </span>
              <input
                type="number"
                step="0.01"
                min={0}
                max={1}
                className="mt-1 w-full rounded-md border border-ink/15 bg-paper/50 px-3 py-2"
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
            </label>
          ))}
        </div>
      </section>

      <section className="mt-6 rounded-xl border border-ink/10 bg-white/70 p-5">
        <h2 className="font-display text-xl">持仓录入</h2>
        <p className="mt-1 text-xs text-ink/50">
          份额与成本价使用 ETF 真实收盘价计算收益；执行止盈后请更新止盈阶段
        </p>
        <div className="mt-4 space-y-3">
          {portfolio.holdings.map((h, idx) => {
            const name = symbols.find((s) => s.id === h.symbol)?.name || h.symbol;
            return (
              <div
                key={h.symbol}
                className="grid gap-2 rounded-lg border border-ink/8 bg-paper/40 p-3 sm:grid-cols-4"
              >
                <p className="text-sm font-medium sm:col-span-4">{name}</p>
                <label className="text-xs text-ink/60">
                  份额
                  <input
                    type="number"
                    className="mt-1 w-full rounded border border-ink/15 px-2 py-1.5 text-sm"
                    value={h.shares}
                    onChange={(e) => {
                      const next = [...portfolio.holdings];
                      next[idx] = { ...h, shares: Number(e.target.value) };
                      setPortfolio({ ...portfolio, holdings: next });
                    }}
                  />
                </label>
                <label className="text-xs text-ink/60">
                  成本价
                  <input
                    type="number"
                    className="mt-1 w-full rounded border border-ink/15 px-2 py-1.5 text-sm"
                    value={h.cost_price}
                    onChange={(e) => {
                      const next = [...portfolio.holdings];
                      next[idx] = { ...h, cost_price: Number(e.target.value) };
                      setPortfolio({ ...portfolio, holdings: next });
                    }}
                  />
                </label>
                <label className="text-xs text-ink/60">
                  市值（可选）
                  <input
                    type="number"
                    className="mt-1 w-full rounded border border-ink/15 px-2 py-1.5 text-sm"
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
                </label>
                <label className="text-xs text-ink/60">
                  已执行止盈阶段
                  <select
                    value={h.take_profit_stage}
                    onChange={(e) => {
                      const next = [...portfolio.holdings];
                      next[idx] = {
                        ...h,
                        take_profit_stage: Number(e.target.value),
                      };
                      setPortfolio({ ...portfolio, holdings: next });
                    }}
                    className="mt-1 w-full rounded border border-ink/15 px-2 py-1.5 text-sm"
                  >
                    <option value={0}>未止盈</option>
                    <option value={1}>已执行第一档 50%</option>
                    <option value={2}>已清仓</option>
                  </select>
                </label>
              </div>
            );
          })}
        </div>
      </section>

      <div className="mt-6 flex items-center gap-4">
        <button
          type="button"
          onClick={saveAll}
          className="rounded-lg bg-moss px-5 py-2.5 text-sm font-semibold text-white hover:bg-moss/90"
        >
          保存
        </button>
        {message && <p className="text-sm text-moss">{message}</p>}
        {error && <p className="text-sm text-red-600">{error}</p>}
      </div>
    </main>
  );
}
