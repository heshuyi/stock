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
  symbolLabel,
} from "@/lib/api";
import { ChartCaption } from "@/components/chart-caption";
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

const ALL_SYMBOLS = "__all__";

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
      <p className="mt-1 font-display text-2xl text-ink sm:text-3xl">{value}</p>
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
      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between sm:gap-4">
        <div className="min-w-0">
          <h1 className="font-display text-2xl text-ink sm:text-3xl">数据库监控</h1>
          <p className="mt-1 text-sm text-ink/60">
            服务端聚合与游标分页；浏览器不会一次加载全部行情数据
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="lg"
          className="h-11 w-full bg-white sm:w-auto"
          onClick={loadOverview}
          disabled={loading}
        >
          {loading ? "刷新中…" : "刷新概览"}
        </Button>
      </div>

      {error && (
        <div className="mt-5 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {overview && (
        <>
          <section className="mt-6 grid grid-cols-2 gap-3 xl:grid-cols-4">
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

          {(overview.signal_date || overview.calendar_t1) && (
            <p className="mt-3 text-sm text-ink/60">
              信号日（仓内）{overview.signal_date ?? "—"}
              {overview.calendar_t1
                ? ` · 日历 T-1 ${overview.calendar_t1}`
                : ""}
              {overview.warehouse_fresh === false ? (
                <span className="text-clay"> · 行情落后，请同步</span>
              ) : overview.warehouse_fresh ? (
                <span className="text-moss"> · 已对齐 T-1</span>
              ) : null}
            </p>
          )}

          <section className="mt-6 grid gap-4 lg:grid-cols-[1.25fr_1fr]">
            <div className="flex h-64 flex-col overflow-hidden rounded-xl border border-ink/10 bg-white/70 p-3 sm:h-80 sm:p-4">
              <ChartCaption
                title="近 36 个月数据密度"
                note="按月统计 SQLite 行情仓入库行数，用来检查同步是否完整、有没有缺月或某段数据偏少；不是策略信号，仅作数据质量监控。"
              />
              <div className="min-h-0 flex-1">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={monthly} margin={{ top: 8, right: 4, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#0f1c2e18" />
                  <XAxis
                    dataKey="month"
                    minTickGap={40}
                    tick={{ fill: "#0f1c2e99", fontSize: 10 }}
                  />
                  <YAxis width={36} tick={{ fill: "#0f1c2e99", fontSize: 10 }} />
                  <Tooltip />
                  <Bar dataKey="rows" name="行数" fill="#1f6f5b" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
              </div>
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
                    {overview.symbols.map((item) => {
                      const meta = symbols.find((s) => s.id === item.symbol);
                      return (
                        <tr key={item.symbol} className="border-t border-ink/5">
                          <td className="px-4 py-2 font-semibold">
                            {symbolLabel(
                              meta?.name || item.symbol,
                              meta?.etf_code,
                              item.symbol
                            )}
                          </td>
                          <td className="px-4 py-2 text-right tabular-nums">
                            {item.rows.toLocaleString("zh-CN")}
                          </td>
                          <td className="px-4 py-2 tabular-nums">
                            {item.latest_date}
                          </td>
                          <td className="px-4 py-2">
                            <span className="rounded bg-moss/10 px-2 py-0.5 text-xs text-moss">
                              {item.sources || "unknown"}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        </>
      )}

      <section className="mt-6 rounded-xl border border-ink/10 bg-white/80 shadow-sm backdrop-blur">
        <div className="border-b border-ink/10 p-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,1.4fr)_repeat(3,minmax(0,1fr))_auto]">
            <div className="space-y-1.5 sm:col-span-2 lg:col-span-1">
              <Label htmlFor="db-symbol">标的</Label>
              <Select
                value={symbol || ALL_SYMBOLS}
                onValueChange={(value) => {
                  setSymbol(value === ALL_SYMBOLS ? "" : value);
                  resetCursor();
                }}
              >
                <SelectTrigger
                  id="db-symbol"
                  aria-label="标的"
                  className="h-11 w-full bg-white"
                >
                  <SelectValue placeholder="全部标的" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_SYMBOLS}>全部标的</SelectItem>
                  {symbols.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.name}（{item.etf_code}）
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="db-date-from">开始日期</Label>
              <Input
                id="db-date-from"
                type="date"
                className="h-11 bg-white"
                value={dateFrom}
                onChange={(e) => {
                  setDateFrom(e.target.value);
                  resetCursor();
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="db-date-to">结束日期</Label>
              <Input
                id="db-date-to"
                type="date"
                className="h-11 bg-white"
                value={dateTo}
                onChange={(e) => {
                  setDateTo(e.target.value);
                  resetCursor();
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="db-limit">每页</Label>
              <Select
                value={String(limit)}
                onValueChange={(value) => {
                  setLimit(Number(value));
                  resetCursor();
                }}
              >
                <SelectTrigger
                  id="db-limit"
                  aria-label="每页行数"
                  className="h-11 w-full bg-white"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {[50, 100, 200].map((value) => (
                    <SelectItem key={value} value={String(value)}>
                      {value} 行
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col justify-end gap-2 sm:col-span-2 lg:col-span-1">
              <Button
                type="button"
                variant="outline"
                size="lg"
                className="h-11 w-full bg-white lg:w-auto"
                onClick={() => {
                  setSymbol("");
                  setDateFrom("");
                  setDateTo("");
                  resetCursor();
                }}
              >
                清除筛选
              </Button>
            </div>
            <div className="text-xs text-muted-foreground sm:col-span-2 lg:col-span-5 lg:text-right">
              <p>
                第 {pageNumber} 页 · 当前仅持有 {page.items.length} 行
              </p>
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

        <div className="flex items-center justify-between gap-2 border-t border-ink/10 px-3 py-3 sm:px-4">
          <Button
            type="button"
            variant="outline"
            size="lg"
            className="h-11 bg-white"
            onClick={previousPage}
            disabled={!cursorHistory.length || rowsLoading}
          >
            上一页
          </Button>
          <span className="hidden text-center text-xs text-muted-foreground sm:inline">
            游标分页避免大表 OFFSET 扫描，单页上限 200 行
          </span>
          <Button
            type="button"
            size="lg"
            className="h-11"
            onClick={nextPage}
            disabled={!page.has_more || rowsLoading}
          >
            下一页
          </Button>
        </div>
      </section>
    </main>
  );
}
