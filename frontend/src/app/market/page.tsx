"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { MarketPoint, SymbolInfo, api } from "@/lib/api";

export default function MarketPage() {
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [symbol, setSymbol] = useState("HS300");
  const [series, setSeries] = useState<MarketPoint[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api
      .symbols()
      .then((r) => {
        setSymbols(r.symbols);
        if (r.symbols[0]) setSymbol(r.symbols[0].id);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"));
  }, []);

  useEffect(() => {
    if (!symbol) return;
    setLoading(true);
    setError(null);
    setSeries([]);
    api
      .market(symbol, 365)
      .then((r) => setSeries(r.series))
      .catch((e) => setError(e instanceof Error ? e.message : "行情加载失败"))
      .finally(() => setLoading(false));
  }, [symbol]);

  const chart = useMemo(
    () =>
      series.map((p) => ({
        date: p.date.slice(5),
        close: p.close,
        ma60: p.ma_short ?? undefined,
        ma120: p.ma_long ?? undefined,
        drawdown:
          p.drawdown != null ? Number((p.drawdown * 100).toFixed(2)) : undefined,
        pePct:
          p.pe_percentile != null
            ? Number((p.pe_percentile * 100).toFixed(1))
            : undefined,
      })),
    [series]
  );

  const latest = series[series.length - 1];
  const current = symbols.find((s) => s.id === symbol);

  return (
    <main>
      <h1 className="font-display text-3xl text-ink">标的详情</h1>
      <p className="mt-1 text-sm text-ink/60">
        价格、均线、估值分位与近一年回撤
        {latest ? ` · 数据截至 ${latest.date}（非盘中实时）` : ""}
      </p>

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}
      {loading && <p className="mt-4 text-ink/60">加载行情中…</p>}

      <div className="mt-6 flex flex-wrap gap-2">
        {symbols.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => setSymbol(s.id)}
            className={`rounded-md px-3 py-1.5 text-sm ${
              symbol === s.id ? "bg-ink text-paper" : "bg-white/70 hover:bg-white"
            }`}
          >
            {s.name}
          </button>
        ))}
      </div>

      {current && latest && !loading && (
        <div className="mt-6 grid gap-3 sm:grid-cols-4">
          <Stat label={`收盘（${latest.date}）`} value={latest.close.toFixed(2)} />
          <Stat
            label="估值分位(PE)"
            value={
              latest.pe_percentile != null
                ? `${(latest.pe_percentile * 100).toFixed(0)}%`
                : "—"
            }
          />
          <Stat
            label="近1年回撤"
            value={
              latest.drawdown != null
                ? `${(latest.drawdown * 100).toFixed(1)}%`
                : "—"
            }
          />
          <Stat label="角色" value={current.role} />
        </div>
      )}

      <div className="mt-6 h-80 rounded-xl border border-ink/10 bg-white/70 p-4">
        <p className="mb-2 text-sm font-medium text-ink/70">价格与均线</p>
        <ResponsiveContainer width="100%" height="90%">
          <LineChart data={chart}>
            <CartesianGrid strokeDasharray="3 3" stroke="#0f1c2e22" />
            <XAxis dataKey="date" minTickGap={40} tick={{ fontSize: 11 }} />
            <YAxis domain={["auto", "auto"]} tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend />
            <Line type="monotone" dataKey="close" name="收盘" stroke="#0f1c2e" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="ma60" name="MA60" stroke="#1f6f5b" dot={false} />
            <Line type="monotone" dataKey="ma120" name="MA120" stroke="#c45c26" dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <div className="h-64 rounded-xl border border-ink/10 bg-white/70 p-4">
          <p className="mb-2 text-sm font-medium text-ink/70">回撤 %</p>
          <ResponsiveContainer width="100%" height="85%">
            <LineChart data={chart}>
              <CartesianGrid strokeDasharray="3 3" stroke="#0f1c2e22" />
              <XAxis dataKey="date" minTickGap={40} tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Line type="monotone" dataKey="drawdown" name="回撤%" stroke="#3d5a80" dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="h-64 rounded-xl border border-ink/10 bg-white/70 p-4">
          <p className="mb-2 text-sm font-medium text-ink/70">PE 历史分位 %</p>
          <ResponsiveContainer width="100%" height="85%">
            <LineChart data={chart}>
              <CartesianGrid strokeDasharray="3 3" stroke="#0f1c2e22" />
              <XAxis dataKey="date" minTickGap={40} tick={{ fontSize: 11 }} />
              <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} />
              <Tooltip />
              <Line type="monotone" dataKey="pePct" name="PE分位%" stroke="#1f6f5b" dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-ink/10 bg-white/70 p-4">
      <p className="text-xs uppercase tracking-wider text-ink/45">{label}</p>
      <p className="mt-1 font-display text-2xl text-ink">{value}</p>
    </div>
  );
}
