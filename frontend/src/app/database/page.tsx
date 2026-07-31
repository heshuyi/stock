"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  DatabaseOverview,
  DatabaseRowsPage,
  SymbolInfo,
  api,
} from "@/lib/api";

const EMPTY_PAGE: DatabaseRowsPage = {
  items: [],
  next_cursor: null,
  has_more: false,
  limit: 100,
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(2)} MB`;
}

function formatNumber(value: number | null, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function StatCard({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="rounded-xl border border-ink/10 bg-white/70 p-4 shadow-sm">
      <p className="text-xs uppercase tracking-wider text-ink/45">{label}</p>
      <p className="mt-1 font-display text-3xl text-ink">{value}</p>
      <p className="mt-1 text-xs text-ink/55">{detail}</p>
    </div>
  );
}

export default function DatabasePage() {
  const [overview, setOverview] = useState<DatabaseOverview | null>(null);
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [page, setPage] = useState<DatabaseRowsPage>(EMPTY_PAGE);
  const [symbol, setSymbol] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [limit, setLimit] = useState(100);
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([]);
  const [loading, setLoading] = useState(true);
  const [rowsLoading, setRowsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  async function loadOverview() {
    setLoading(true);
    setError(null);
    try {
      const [summary, symbolResult] = await Promise.all([
        api.databaseOverview(),
        api.symbols(),
      ]);
      setOverview(summary);
      setSymbols(symbolResult.symbols);
    } catch (e) {
      setError(e instanceof Error ? e.message : "数据库概览加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadOverview();
  }, []);

  useEffect(() => {
    const id = ++requestId.current;
    setRowsLoading(true);
    setError(null);
    api
      .databaseRows({
        symbol: symbol || undefined,
        dateFrom: dateFrom || undefined,
        dateTo: dateTo || undefined,
        cursor: cursor || undefined,
        limit,
      })
      .then((result) => {
        if (id === requestId.current) setPage(result);
      })
      .catch((e) => {
        if (id === requestId.current) {
          setError(e instanceof Error ? e.message : "数据明细加载失败");
        }
      })
      .finally(() => {
        if (id === requestId.current) setRowsLoading(false);
      });
  }, [cursor, dateFrom, dateTo, limit, symbol]);

  function resetCursor() {
    setCursor(null);
    setCursorHistory([]);
  }

  function nextPage() {
    if (!page.next_cursor) return;
    setCursorHistory((history) => [...history, cursor]);
    setCursor(page.next_cursor);
  }

  function previousPage() {
    if (!cursorHistory.length) return;
    const previous = cursorHistory[cursorHistory.length - 1] ?? null;
    setCursorHistory((history) => history.slice(0, -1));
    setCursor(previous);
  }

  const monthly = useMemo(() => overview?.monthly ?? [], [overview]);
  const quality = overview?.overall.quality_score ?? 0;
  const pageNumber = cursorHistory.length + 1;

  return (
    <main>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl text-ink">数据库监控</h1>
          <p className="mt-1 text-sm text-ink/60">
            服务端聚合与游标分页；浏览器不会一次加载全部行情数据
          </p>
        </div>
        <button
          type="button"
          onClick={loadOverview}
          disabled={loading}
          className="rounded-lg border border-ink/15 bg-white/80 px-4 py-2 text-sm font-medium hover:bg-white disabled:opacity-50"
        >
          {loading ? "刷新中…" : "刷新概览"}
        </button>
      </div>

      {error && (
        <div className="mt-5 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {overview && (
        <>
          <section className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="总行数"
              value={overview.overall.total_rows.toLocaleString("zh-CN")}
              detail={`${overview.overall.symbol_count} 个标的`}
            />
            <StatCard
              label="数据库大小"
              value={formatBytes(overview.db_size_bytes)}
              detail="SQLite 持久化行情仓"
            />
            <StatCard
              label="覆盖区间"
              value={`${overview.overall.earliest_date?.slice(0, 4) ?? "—"}–${overview.overall.latest_date?.slice(0, 4) ?? "—"}`}
              detail={`${overview.overall.earliest_date ?? "—"} 至 ${overview.overall.latest_date ?? "—"}`}
            />
            <StatCard
              label="数据质量"
              value={`${quality.toFixed(2)}%`}
              detail={`缺 PE ${overview.overall.missing_pe} · 缺 PB ${overview.overall.missing_pb}`}
            />
          </section>

          <section className="mt-6 grid gap-4 lg:grid-cols-[1.25fr_1fr]">
            <div className="h-72 rounded-xl border border-ink/10 bg-white/70 p-4">
              <div className="mb-3 flex items-center justify-between">
                <p className="font-semibold text-ink">近 36 个月数据密度</p>
                <span className="text-xs text-ink/45">按月聚合，非明细全量加载</span>
              </div>
              <ResponsiveContainer width="100%" height="88%">
                <BarChart data={monthly}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#0f1c2e18" />
                  <XAxis
                    dataKey="month"
                    minTickGap={30}
                    tick={{ fill: "#0f1c2e99", fontSize: 10 }}
                  />
                  <YAxis tick={{ fill: "#0f1c2e99", fontSize: 10 }} />
                  <Tooltip />
                  <Bar dataKey="rows" name="行数" fill="#1f6f5b" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>

            <div className="overflow-hidden rounded-xl border border-ink/10 bg-white/70">
              <div className="border-b border-ink/10 px-4 py-3">
                <p className="font-semibold text-ink">标的覆盖</p>
              </div>
              <div className="max-h-72 overflow-auto">
                <table className="w-full text-left text-sm">
                  <thead className="sticky top-0 bg-paper text-xs text-ink/55">
                    <tr>
                      <th className="px-4 py-2 font-medium">标的</th>
                      <th className="px-4 py-2 text-right font-medium">行数</th>
                      <th className="px-4 py-2 font-medium">最新日期</th>
                      <th className="px-4 py-2 font-medium">来源</th>
                    </tr>
                  </thead>
                  <tbody>
                    {overview.symbols.map((item) => (
                      <tr key={item.symbol} className="border-t border-ink/5">
                        <td className="px-4 py-2 font-semibold">{item.symbol}</td>
                        <td className="px-4 py-2 text-right tabular-nums">
                          {item.rows.toLocaleString("zh-CN")}
                        </td>
                        <td className="px-4 py-2 tabular-nums">{item.latest_date}</td>
                        <td className="px-4 py-2">
                          <span className="rounded bg-moss/10 px-2 py-0.5 text-xs text-moss">
                            {item.sources || "unknown"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        </>
      )}

      <section className="mt-6 rounded-xl border border-ink/10 bg-white/70">
        <div className="border-b border-ink/10 p-4">
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs text-ink/55">
              标的
              <select
                value={symbol}
                onChange={(e) => {
                  setSymbol(e.target.value);
                  resetCursor();
                }}
                className="mt-1 block rounded-md border border-ink/15 bg-white px-3 py-2 text-sm text-ink"
              >
                <option value="">全部</option>
                {symbols.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}（{item.id}）
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs text-ink/55">
              开始日期
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => {
                  setDateFrom(e.target.value);
                  resetCursor();
                }}
                className="mt-1 block rounded-md border border-ink/15 bg-white px-3 py-2 text-sm text-ink"
              />
            </label>
            <label className="text-xs text-ink/55">
              结束日期
              <input
                type="date"
                value={dateTo}
                onChange={(e) => {
                  setDateTo(e.target.value);
                  resetCursor();
                }}
                className="mt-1 block rounded-md border border-ink/15 bg-white px-3 py-2 text-sm text-ink"
              />
            </label>
            <label className="text-xs text-ink/55">
              每页
              <select
                value={limit}
                onChange={(e) => {
                  setLimit(Number(e.target.value));
                  resetCursor();
                }}
                className="mt-1 block rounded-md border border-ink/15 bg-white px-3 py-2 text-sm text-ink"
              >
                {[50, 100, 200].map((value) => (
                  <option key={value} value={value}>
                    {value} 行
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => {
                setSymbol("");
                setDateFrom("");
                setDateTo("");
                resetCursor();
              }}
              className="rounded-md px-3 py-2 text-sm text-steel hover:bg-ink/5"
            >
              清除筛选
            </button>
            <div className="ml-auto text-right text-xs text-ink/50">
              <p>第 {pageNumber} 页 · 当前仅持有 {page.items.length} 行</p>
              <p>信号日 {overview?.signal_date ?? "—"}</p>
            </div>
          </div>
        </div>

        <div className="relative max-h-[620px] overflow-auto">
          {rowsLoading && (
            <div className="absolute inset-x-0 top-0 z-20 bg-steel/90 py-1 text-center text-xs text-white">
              服务端查询中…
            </div>
          )}
          <table className="min-w-[1500px] w-full text-left text-xs">
            <thead className="sticky top-0 z-10 bg-ink text-paper">
              <tr>
                {[
                  "日期",
                  "标的",
                  "收盘",
                  "ETF收盘",
                  "开盘",
                  "最高",
                  "最低",
                  "成交量",
                  "MA60",
                  "MA120",
                  "回撤",
                  "PE",
                  "PB",
                  "PE分位",
                  "PB分位",
                  "来源",
                ].map((label) => (
                  <th key={label} className="whitespace-nowrap px-3 py-2 font-medium">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {page.items.map((row) => (
                <tr
                  key={`${row.symbol}-${row.date}`}
                  className="border-b border-ink/5 odd:bg-white/30 hover:bg-moss/5"
                >
                  <td className="whitespace-nowrap px-3 py-2 tabular-nums">{row.date}</td>
                  <td className="px-3 py-2 font-semibold">{row.symbol}</td>
                  <td className="px-3 py-2 tabular-nums">{formatNumber(row.close, 3)}</td>
                  <td className="px-3 py-2 tabular-nums">{formatNumber(row.etf_close, 4)}</td>
                  <td className="px-3 py-2 tabular-nums">{formatNumber(row.open, 3)}</td>
                  <td className="px-3 py-2 tabular-nums">{formatNumber(row.high, 3)}</td>
                  <td className="px-3 py-2 tabular-nums">{formatNumber(row.low, 3)}</td>
                  <td className="px-3 py-2 tabular-nums">{formatNumber(row.volume, 0)}</td>
                  <td className="px-3 py-2 tabular-nums">{formatNumber(row.ma_short)}</td>
                  <td className="px-3 py-2 tabular-nums">{formatNumber(row.ma_long)}</td>
                  <td className="px-3 py-2 tabular-nums">
                    {row.drawdown == null ? "—" : `${(row.drawdown * 100).toFixed(1)}%`}
                  </td>
                  <td className="px-3 py-2 tabular-nums">{formatNumber(row.pe)}</td>
                  <td className="px-3 py-2 tabular-nums">{formatNumber(row.pb)}</td>
                  <td className="px-3 py-2 tabular-nums">
                    {row.pe_percentile == null
                      ? "—"
                      : `${(row.pe_percentile * 100).toFixed(1)}%`}
                  </td>
                  <td className="px-3 py-2 tabular-nums">
                    {row.pb_percentile == null
                      ? "—"
                      : `${(row.pb_percentile * 100).toFixed(1)}%`}
                  </td>
                  <td className="px-3 py-2">{row.source || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!rowsLoading && !page.items.length && (
            <p className="py-12 text-center text-sm text-ink/50">没有匹配数据</p>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-ink/10 px-4 py-3">
          <button
            type="button"
            onClick={previousPage}
            disabled={!cursorHistory.length || rowsLoading}
            className="rounded-md border border-ink/15 px-4 py-2 text-sm disabled:opacity-35"
          >
            上一页
          </button>
          <span className="text-xs text-ink/45">
            游标分页避免大表 OFFSET 扫描，单页上限 200 行
          </span>
          <button
            type="button"
            onClick={nextPage}
            disabled={!page.has_more || rowsLoading}
            className="rounded-md bg-ink px-4 py-2 text-sm text-paper disabled:opacity-35"
          >
            下一页
          </button>
        </div>
      </section>
    </main>
  );
}
