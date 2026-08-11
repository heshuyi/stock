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
import { MarketPoint, SymbolInfo, api, symbolLabel } from "@/lib/api";
import { ChartCaption } from "@/components/chart-caption";
import { SymbolChips } from "@/components/symbol-chips";

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
      <h1 className="font-display text-2xl text-ink sm:text-3xl">标的详情</h1>
      <p className="mt-1 text-sm leading-relaxed text-ink/60">
        价格、均线、估值分位与近一年回撤
        {latest ? ` · 数据截至 ${latest.date}（非盘中实时）` : ""}
      </p>

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}
      {loading && <p className="mt-4 text-ink/60">加载行情中…</p>}

      <SymbolChips
        items={symbols.map((s) => ({
          id: s.id,
          label: symbolLabel(s.name, s.etf_code, s.id),
        }))}
        value={symbol}
        onChange={setSymbol}
      />

      {current && latest && !loading && (
        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
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

      <div className="mt-6 flex h-64 flex-col overflow-hidden rounded-xl border border-ink/10 bg-white/70 p-3 sm:h-96 sm:p-4">
        <ChartCaption
          title="价格与均线"
          note="指数收盘价与 MA60、MA120 的相对位置，用于判断趋势多空；均线策略在价位于长均线下方时会降频或暂停定投。"
        />
        <div className="min-h-0 flex-1">
          <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chart} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#0f1c2e22" />
            <XAxis dataKey="date" minTickGap={48} tick={{ fontSize: 10 }} />
            <YAxis width={40} domain={["auto", "auto"]} tick={{ fontSize: 10 }} />
            <Tooltip />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line type="monotone" dataKey="close" name="收盘" stroke="#0f1c2e" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="ma60" name="MA60" stroke="#1f6f5b" dot={false} />
            <Line type="monotone" dataKey="ma120" name="MA120" stroke="#c45c26" dot={false} />
          </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <div className="flex h-56 flex-col overflow-hidden rounded-xl border border-ink/10 bg-white/70 p-3 sm:h-72 sm:p-4">
          <ChartCaption
            title="近1年回撤 %"
            note="相对近一年最高收盘价的跌幅，用于观察当前离高点有多远。当前 v3 合成策略不含网格加码，此指标仅作参考，不直接改变买入倍数。"
          />
          <div className="min-h-0 flex-1">
            <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chart} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#0f1c2e22" />
              <XAxis dataKey="date" minTickGap={48} tick={{ fontSize: 10 }} />
              <YAxis width={36} tick={{ fontSize: 10 }} />
              <Tooltip />
              <Line type="monotone" dataKey="drawdown" name="回撤%" stroke="#3d5a80" dot={false} />
            </LineChart>
          </ResponsiveContainer>
          </div>
        </div>
        <div className="flex h-56 flex-col overflow-hidden rounded-xl border border-ink/10 bg-white/70 p-3 sm:h-72 sm:p-4">
          <ChartCaption
            title="PE 历史分位 %"
            note="当前 PE 在历史样本中的百分位（0=极便宜，100=极贵）。估值策略据此调节定投：分位高则少投或暂停，分位低则多投。"
          />
          <div className="min-h-0 flex-1">
            <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chart} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#0f1c2e22" />
              <XAxis dataKey="date" minTickGap={48} tick={{ fontSize: 10 }} />
              <YAxis width={36} domain={[0, 100]} tick={{ fontSize: 10 }} />
              <Tooltip />
              <Line type="monotone" dataKey="pePct" name="PE分位%" stroke="#1f6f5b" dot={false} />
            </LineChart>
          </ResponsiveContainer>
          </div>
        </div>
      </div>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-ink/10 bg-white/70 p-4">
      <p className="text-xs uppercase tracking-wider text-ink/45">{label}</p>
      <p className="mt-1 break-all font-display text-xl text-ink sm:text-2xl">{value}</p>
    </div>
  );
}
