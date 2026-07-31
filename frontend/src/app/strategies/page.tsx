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
} from "@/lib/api";

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
        symbol: item.name,
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
      <h1 className="font-display text-3xl text-ink">策略对比</h1>
      <p className="mt-1 text-sm text-ink/60">
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
          <div className="mt-6 flex flex-wrap gap-2">
            {data.items.map((i) => (
              <button
                key={i.symbol}
                type="button"
                onClick={() => setSymbol(i.symbol)}
                className={`rounded-md px-3 py-1.5 text-sm ${
                  symbol === i.symbol
                    ? "bg-ink text-paper"
                    : "bg-white/70 text-ink/80 hover:bg-white"
                }`}
              >
                {i.name}
              </button>
            ))}
          </div>

          <div className="mt-6 h-80 rounded-xl border border-ink/10 bg-white/70 p-4">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#0f1c2e22" />
                <XAxis dataKey="name" tick={{ fill: "#0f1c2e99", fontSize: 12 }} />
                <YAxis tick={{ fill: "#0f1c2e99", fontSize: 12 }} domain={[0, 2]} />
                <Tooltip />
                <Legend />
                <Bar dataKey="multiplier" name="策略倍数" fill="#1f6f5b" radius={4} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="mt-8 overflow-x-auto rounded-xl border border-ink/10 bg-white/70">
            <table className="min-w-full text-left text-sm">
              <thead className="border-b border-ink/10 bg-ink/5 text-ink/70">
                <tr>
                  <th className="px-4 py-3 font-medium">标的</th>
                  <th className="px-4 py-3 font-medium">估值</th>
                  <th className="px-4 py-3 font-medium">均线</th>
                  <th className="px-4 py-3 font-medium">止盈</th>
                  <th className="px-4 py-3 font-medium">合成</th>
                </tr>
              </thead>
              <tbody>
                {compareRows.map((row) => (
                  <tr key={String(row.symbol)} className="border-b border-ink/5">
                    <td className="px-4 py-3 font-medium">{row.symbol}</td>
                    <td className="px-4 py-3 tabular-nums">{row.valuation ?? "不适用"}</td>
                    <td className="px-4 py-3 tabular-nums">{row.trend}</td>
                    <td className="px-4 py-3 tabular-nums">{row.profit_taking}</td>
                    <td className="px-4 py-3 tabular-nums font-semibold text-moss">
                      {row.final}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </main>
  );
}
