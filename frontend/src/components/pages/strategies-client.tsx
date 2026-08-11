"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Dashboard,
  EnsembleItem,
  STRATEGY_LABELS,
  api,
  symbolLabel,
} from "@/lib/api";
import { ChartCaption } from "@/components/chart-caption";
import { SymbolChips } from "@/components/symbol-chips";

const BASIS_SECTIONS = [
  {
    title: "估值定投",
    body: "先算估值分位 p（核心用 PE 近5年分位；成长代理用 0.55×PE + 0.45×PB）。再按分位落档：低估加码、偏高减额、达到暂停线则倍数为 0。各标的档位与暂停线见配置。",
  },
  {
    title: "均线过滤",
    body: "用收盘价相对 MA60 / MA120 判定多头、偏多、夹层、空头，再查表取倍数。核心仓破位仅降频；成长仓空头可硬停，极端低估可超跌解封小额吸纳。",
  },
  {
    title: "分批止盈",
    body: "不参与买入倍数加权。估值达武装线后追踪峰值回撤；回撤达标建议减半仓，达清仓线建议清仓级减持。列上：触发减仓为 0，否则为 1。",
  },
  {
    title: "合成倍数",
    body: "仅估值与均线加权：核心约 70%/30%，成长约 55%/45%。硬否决（估值 pause 或成长空头硬停未解封）时合成为 0；超跌解封时直接用解封倍数。金额 = 本期基准额 × 目标权重 × 合成。",
  },
] as const;

function signalOf(item: EnsembleItem, key: string) {
  return item.strategies.find((s) => s.strategy === key);
}

function multLabel(item: EnsembleItem, key: string): string {
  const s = signalOf(item, key);
  if (!s) return key === "valuation" ? "不适用" : "—";
  return Number(s.multiplier.toFixed(2)).toString();
}

export default function StrategiesPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [symbol, setSymbol] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [basisOpen, setBasisOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    api
      .dashboard()
      .then((d) => {
        setData(d);
        if (d.items[0]) setSymbol(d.items[0].symbol);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"))
      .finally(() => setLoading(false));
  }, []);

  const chartData = useMemo(() => {
    const item = data?.items.find((i) => i.symbol === symbol);
    if (!item) return [];
    return item.strategies.map((s) => ({
      name: STRATEGY_LABELS[s.strategy] || s.strategy,
      multiplier: Number(s.multiplier.toFixed(2)),
      final: Number(item.multiplier.toFixed(2)),
    }));
  }, [data, symbol]);

  const selectedItem = data?.items.find((i) => i.symbol === symbol);

  return (
    <main>
      <h1 className="font-display text-2xl text-ink sm:text-3xl">策略对比</h1>
      <p className="mt-1 text-sm leading-relaxed text-ink/60">
        分角色差异化：核心估值70%/趋势30%，成长55%/45%；代理标的用 PE+PB
        复合；止盈为估值追踪回撤
        {data ? ` · 信号日期（T-1） ${data.date}` : ""}
        {data?.pool_factor != null
          ? ` · 现金池×${data.pool_factor.toFixed(2)}`
          : ""}
      </p>

      {loading && !data && (
        <p className="mt-4 text-ink/60">加载策略信号中…</p>
      )}
      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

      {data && (
        <>
          <section className="mt-6 rounded-xl border border-ink/10 bg-white/70">
            <button
              type="button"
              onClick={() => setBasisOpen((v) => !v)}
              className="flex min-h-11 w-full items-center justify-between gap-3 px-4 py-3 text-left"
              aria-expanded={basisOpen}
            >
              <span className="font-display text-lg text-ink">
                计算依据（估值 / 均线 / 止盈 / 合成）
              </span>
              <span className="shrink-0 text-sm text-steel">
                {basisOpen ? "收起" : "展开"}
              </span>
            </button>
            {basisOpen && (
              <div className="grid gap-3 border-t border-ink/10 px-4 py-4 sm:grid-cols-2">
                {BASIS_SECTIONS.map((section) => (
                  <div
                    key={section.title}
                    className="rounded-lg border border-ink/8 bg-paper/60 p-3"
                  >
                    <p className="text-sm font-semibold text-ink">
                      {section.title}
                    </p>
                    <p className="mt-1 text-sm leading-relaxed text-ink/65">
                      {section.body}
                    </p>
                  </div>
                ))}
                <p className="text-xs text-ink/45 sm:col-span-2">
                  下方表格倍数为 T-1 信号日结果；点击行可查看各子策略当日
                  reason。规则细节以配置与 PRD 为准。
                </p>
              </div>
            )}
          </section>

          <SymbolChips
            items={data.items.map((i) => ({
              id: i.symbol,
              label: symbolLabel(i.name, i.etf_code, i.symbol),
            }))}
            value={symbol}
            onChange={setSymbol}
          />

          {selectedItem && (
            <div className="mt-4 rounded-xl border border-ink/10 bg-white/70 p-4">
              <p className="text-sm font-semibold text-ink">
                {symbolLabel(
                  selectedItem.name,
                  selectedItem.etf_code,
                  selectedItem.symbol
                )}{" "}
                · 合成依据
              </p>
              <p className="mt-1 text-sm leading-relaxed text-ink/75">
                {selectedItem.reason}
              </p>
              {selectedItem.hard_veto && (
                <p className="mt-2 text-xs font-medium text-clay">
                  当前触发硬否决，买入倍数为 0
                </p>
              )}
            </div>
          )}

          <div className="mt-6 flex h-64 flex-col overflow-hidden rounded-xl border border-ink/10 bg-white/70 p-3 sm:h-96 sm:p-4">
            <ChartCaption
              title="子策略倍数对比"
              note="选中标的在 T-1 信号日下，估值 / 均线 / 止盈等子策略各自建议的定投倍数（0=暂停）。可与下方表格对照，理解最终合成倍数从何而来。"
            />
            <div className="min-h-0 flex-1">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={chartData}
                  margin={{ top: 8, right: 8, left: 0, bottom: 8 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#0f1c2e22" />
                  <XAxis
                    dataKey="name"
                    tick={{ fill: "#0f1c2e99", fontSize: 11 }}
                    interval={0}
                    angle={-20}
                    textAnchor="end"
                    height={48}
                  />
                  <YAxis
                    width={36}
                    tick={{ fill: "#0f1c2e99", fontSize: 11 }}
                    domain={[0, "auto"]}
                  />
                  <Tooltip />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar
                    dataKey="multiplier"
                    name="策略倍数"
                    fill="#1f6f5b"
                    radius={4}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="mt-8">
            <p className="mb-2 text-xs text-ink/45 sm:hidden">
              左右滑动查看倍数；点击行展开当日依据
            </p>
            <div className="-mx-4 overflow-x-auto overscroll-x-contain border-y border-ink/10 bg-white/70 sm:mx-0 sm:rounded-xl sm:border">
              <table className="w-full min-w-[36rem] text-left text-sm">
                <thead className="border-b border-ink/10 bg-ink/5 text-ink/70">
                  <tr>
                    <th className="sticky left-0 z-10 w-36 max-w-[9rem] bg-ink/5 px-3 py-3 font-medium shadow-[2px_0_6px_-2px_rgba(15,28,46,0.15)] sm:static sm:w-auto sm:max-w-none sm:px-4 sm:shadow-none">
                      标的
                    </th>
                    <th className="whitespace-nowrap px-3 py-3 font-medium sm:px-4">
                      估值
                    </th>
                    <th className="whitespace-nowrap px-3 py-3 font-medium sm:px-4">
                      均线
                    </th>
                    <th className="whitespace-nowrap px-3 py-3 font-medium sm:px-4">
                      止盈
                    </th>
                    <th className="whitespace-nowrap px-3 py-3 font-medium sm:px-4">
                      合成
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item) => {
                    const isOpen = expanded === item.symbol;
                    const label = symbolLabel(
                      item.name,
                      item.etf_code,
                      item.symbol
                    );
                    return (
                      <Fragment key={item.symbol}>
                        <tr
                          className="cursor-pointer border-b border-ink/5 hover:bg-ink/[0.03]"
                          onClick={() => {
                            setExpanded(isOpen ? null : item.symbol);
                            setSymbol(item.symbol);
                          }}
                          aria-expanded={isOpen}
                        >
                          <td
                            className="sticky left-0 z-10 max-w-[9rem] truncate bg-white px-3 py-3 font-medium shadow-[2px_0_6px_-2px_rgba(15,28,46,0.12)] sm:static sm:max-w-none sm:whitespace-normal sm:bg-transparent sm:px-4 sm:shadow-none"
                            title={label}
                          >
                            {label}
                          </td>
                          <td className="whitespace-nowrap px-3 py-3 tabular-nums sm:px-4">
                            {multLabel(item, "valuation")}
                          </td>
                          <td className="whitespace-nowrap px-3 py-3 tabular-nums sm:px-4">
                            {multLabel(item, "trend")}
                          </td>
                          <td className="whitespace-nowrap px-3 py-3 tabular-nums sm:px-4">
                            {multLabel(item, "profit_taking")}
                          </td>
                          <td className="whitespace-nowrap px-3 py-3 font-semibold tabular-nums text-moss sm:px-4">
                            {Number(item.multiplier.toFixed(2))}
                          </td>
                        </tr>
                        {isOpen && (
                          <tr className="border-b border-ink/5 bg-paper/50">
                            <td colSpan={5} className="px-3 py-3 sm:px-4">
                              <p className="text-sm font-medium text-ink">
                                合成：{item.reason}
                              </p>
                              <ul className="mt-2 grid gap-2 sm:grid-cols-3">
                                {item.strategies.map((s) => (
                                  <li
                                    key={s.strategy}
                                    className="rounded-lg border border-ink/8 bg-white/80 p-3 text-sm"
                                  >
                                    <p className="font-semibold text-ink">
                                      {STRATEGY_LABELS[s.strategy] ||
                                        s.strategy}{" "}
                                      <span className="font-normal tabular-nums text-ink/60">
                                        ×{s.multiplier.toFixed(2)}
                                      </span>
                                    </p>
                                    <p className="mt-1 leading-relaxed text-ink/65">
                                      {s.reason}
                                    </p>
                                  </li>
                                ))}
                              </ul>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </main>
  );
}
