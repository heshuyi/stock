"use client";

import { useEffect, useMemo, useState } from "react";
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
  STRATEGY_LABELS,
  api,
  symbolLabel,
} from "@/lib/api";
import { ChartCaption } from "@/components/chart-caption";
import { SymbolChips } from "@/components/symbol-chips";

export default function StrategiesPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [symbol, setSymbol] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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

  const compareRows = useMemo(() => {
    if (!data) return [];
    return data.items.map((item) => {
      const row: Record<string, string | number> = {
        symbol: symbolLabel(item.name, item.etf_code, item.symbol),
        final: Number(item.multiplier.toFixed(2)),
      };
      for (const s of item.strategies) {
        row[s.strategy] = Number(s.multiplier.toFixed(2));
      }
      return row;
    });
  }, [data]);

  return (
    <main>
      <h1 className="font-display text-2xl text-ink sm:text-3xl">策略对比</h1>
      <p className="mt-1 text-sm leading-relaxed text-ink/60">
        分角色差异化：核心估值70%/趋势30%，成长55%/45%；代理标的用 PE+PB
        复合；止盈为估值追踪回撤
        {data ? ` · 信号日期（T-1） ${data.date}` : ""}
        {data?.pool_factor != null ? ` · 现金池×${data.pool_factor.toFixed(2)}` : ""}
      </p>

      {loading && !data && (
        <p className="mt-4 text-ink/60">加载策略信号中…</p>
      )}
      {error && (
        <p className="mt-4 text-sm text-red-600">{error}</p>
      )}

      {data && (
        <>
          <SymbolChips
            items={data.items.map((i) => ({
              id: i.symbol,
              label: symbolLabel(i.name, i.etf_code, i.symbol),
            }))}
            value={symbol}
            onChange={setSymbol}
          />

          <div className="mt-6 flex h-64 flex-col overflow-hidden rounded-xl border border-ink/10 bg-white/70 p-3 sm:h-96 sm:p-4">
            <ChartCaption
              title="子策略倍数对比"
              note="选中标的在 T-1 信号日下，估值 / 均线 / 止盈等子策略各自建议的定投倍数（0=暂停）。可与下方表格对照，理解最终合成倍数从何而来。"
            />
            <div className="min-h-0 flex-1">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
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
                <Bar dataKey="multiplier" name="策略倍数" fill="#1f6f5b" radius={4} />
              </BarChart>
            </ResponsiveContainer>
            </div>
          </div>

          <div className="mt-8">
            <p className="mb-2 text-xs text-ink/45 sm:hidden">
              左右滑动查看估值 / 均线 / 止盈 / 合成
            </p>
            {/* Scroll on the card itself — nested overflow-hidden was clipping columns */}
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
                  {compareRows.map((row) => (
                    <tr
                      key={String(row.symbol)}
                      className="border-b border-ink/5"
                    >
                      <td
                        className="sticky left-0 z-10 max-w-[9rem] truncate bg-white px-3 py-3 font-medium shadow-[2px_0_6px_-2px_rgba(15,28,46,0.12)] sm:static sm:max-w-none sm:whitespace-normal sm:px-4 sm:shadow-none sm:bg-transparent"
                        title={String(row.symbol)}
                      >
                        {row.symbol}
                      </td>
                      <td className="whitespace-nowrap px-3 py-3 tabular-nums sm:px-4">
                        {row.valuation ?? "不适用"}
                      </td>
                      <td className="whitespace-nowrap px-3 py-3 tabular-nums sm:px-4">
                        {row.trend}
                      </td>
                      <td className="whitespace-nowrap px-3 py-3 tabular-nums sm:px-4">
                        {row.profit_taking}
                      </td>
                      <td className="whitespace-nowrap px-3 py-3 font-semibold tabular-nums text-moss sm:px-4">
                        {row.final}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </main>
  );
}
