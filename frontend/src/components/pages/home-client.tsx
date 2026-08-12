"use client";

import { useEffect, useState } from "react";
import {
  Action,
  Dashboard,
  EnsembleItem,
  STRATEGY_LABELS,
  actionClass,
  actionLabel,
  api,
  symbolLabel,
} from "@/lib/api";

function ActionBadge({ action }: { action: Action }) {
  return (
    <span
      className={`inline-flex rounded px-2 py-0.5 text-xs font-semibold tracking-wide ${actionClass(action)}`}
    >
      {actionLabel(action)}
    </span>
  );
}

function strategyBrief(item: EnsembleItem, key: string): string | null {
  const s = item.strategies.find((x) => x.strategy === key);
  if (!s) return null;
  const label = STRATEGY_LABELS[s.strategy] || s.strategy;
  return `${label} ×${s.multiplier.toFixed(2)}`;
}

function OperationCard({ item }: { item: EnsembleItem }) {
  const [open, setOpen] = useState(false);
  const valuation = strategyBrief(item, "valuation");
  const trend = strategyBrief(item, "trend");
  const profit = strategyBrief(item, "profit_taking");

  return (
    <article className="rounded-xl border border-ink/10 bg-white/70 p-4 shadow-sm backdrop-blur sm:p-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-display text-xl text-ink sm:text-2xl">
              {symbolLabel(item.name, item.etf_code, item.symbol)}
            </h2>
            <ActionBadge action={item.action} />
            {item.hard_veto && (
              <span className="rounded bg-ink/10 px-2 py-0.5 text-xs text-ink/70">
                硬否决
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-ink/60">
            目标仓位 {(item.target_weight * 100).toFixed(0)}%
          </p>
        </div>
        <div className="text-left sm:text-right">
          {item.action === "reduce" && item.reduce_ratio != null ? (
            <>
              <p className="text-xs uppercase tracking-wider text-ink/45">建议卖出</p>
              <p className="font-display text-3xl text-clay">
                {(item.reduce_ratio * 100).toFixed(0)}%
              </p>
              <p className="text-sm text-ink/60">卖出当前 ETF 份额</p>
            </>
          ) : item.action === "buy" && item.amount > 0 ? (
            <>
              <p className="text-xs uppercase tracking-wider text-ink/45">建议买入</p>
              <p className="font-display text-3xl text-moss">
                ¥{item.amount.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}
              </p>
              <p className="text-sm text-ink/60">倍数 ×{item.multiplier.toFixed(2)}</p>
            </>
          ) : (
            <>
              <p className="text-xs uppercase tracking-wider text-ink/45">
                {item.action === "pause" ? "暂停定投" : "观望"}
              </p>
              <p className="font-display text-3xl text-ink/45">¥0</p>
              <p className="text-sm text-ink/60">
                {item.action === "pause"
                  ? "本期不新增买入"
                  : `倍数 ×${item.multiplier.toFixed(2)}`}
              </p>
            </>
          )}
        </div>
      </div>

      <div className="mt-3 rounded-lg border border-ink/8 bg-paper/70 px-3 py-2.5">
        <p className="text-xs font-medium uppercase tracking-wider text-ink/45">
          合成依据
        </p>
        <p className="mt-1 text-sm leading-relaxed text-ink/80">{item.reason}</p>
        <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink/55">
          {valuation && <span>{valuation}</span>}
          {trend && <span>{trend}</span>}
          {profit && <span>{profit}</span>}
        </p>
      </div>

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="mt-4 min-h-11 text-sm font-medium text-steel hover:underline"
      >
        {open ? "收起策略明细" : "展开估值、均线与止盈完整依据"}
      </button>
      {open && (
        <ul className="mt-3 grid gap-2 sm:grid-cols-2">
          {item.strategies.map((s) => (
            <li
              key={s.strategy}
              className="rounded-lg border border-ink/8 bg-paper/80 p-3 text-sm"
            >
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                <span className="font-semibold">
                  {STRATEGY_LABELS[s.strategy] || s.strategy}
                </span>
                <span className="tabular-nums text-ink/70">
                  ×{s.multiplier.toFixed(2)} · {actionLabel(s.action)}
                </span>
              </div>
              <p className="leading-relaxed text-ink/65">{s.reason}</p>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

export default function HomePage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncNote, setSyncNote] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const dash = await api.dashboard();
      setData(dash);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleSync(force = false) {
    setSyncing(true);
    setSyncNote(null);
    try {
      const result = await api.sync(force);
      const skipped = Number(result.skipped ?? 0);
      const fetched = Number(result.fetched ?? 0);
      const incremental = Number(result.incremental ?? 0);
      const rowsAdded = Number(result.rows_added ?? 0);
      if (typeof result.warning === "string" && result.warning) {
        setSyncNote(result.warning);
      } else if (skipped && !fetched) {
        setSyncNote(`行情已是最新，跳过 ${skipped} 个标的网络拉取`);
      } else if (incremental && !force) {
        setSyncNote(
          `增量同步：补齐 ${rowsAdded} 根 K 线（${incremental} 个标的，跳过 ${skipped}）`
        );
      } else {
        setSyncNote(
          `同步完成：更新 ${fetched} 个，跳过 ${skipped} 个` +
            (rowsAdded ? `，新增 ${rowsAdded} 根` : "")
        );
      }
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "同步失败");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <main>
      <section className="mb-6 flex flex-col gap-4 sm:mb-8 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between">
        <div className="min-w-0">
          <h1 className="font-display text-2xl text-ink sm:text-3xl">今日操作清单</h1>
          <p className="mt-1 text-sm leading-relaxed text-ink/60">
            {data
              ? `信号日期（T-1） ${data.date}`
              : "加载中…"}
            {data?.normalized ? " · 已按预算上限缩放" : ""}
            {data
              ? data.execution_today
                ? " · 今日执行定投"
                : ` · 非执行日${
                    data.next_execution_date
                      ? `（下一：${data.next_execution_date}）`
                      : ""
                  }`
              : ""}
          </p>
        </div>
        <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:items-center sm:gap-3">
          {data && (
            <div className="rounded-lg bg-ink px-4 py-3 text-paper sm:py-2">
              <p className="text-[10px] uppercase tracking-wider opacity-70">
                今日合计买入
              </p>
              <p className="font-display text-xl">
                ¥
                {data.total_buy_amount.toLocaleString("zh-CN", {
                  maximumFractionDigits: 0,
                })}
              </p>
              <p className="text-xs opacity-70">
                月预算 ¥{data.base_amount.toLocaleString("zh-CN")} · 本期 ¥
                {(data.period_amount ?? data.base_amount).toLocaleString(
                  "zh-CN"
                )}
              </p>
            </div>
          )}
          <div className="grid grid-cols-2 gap-2 sm:flex sm:items-center sm:gap-3">
            <button
              type="button"
              onClick={() => handleSync(false)}
              disabled={syncing}
              className="min-h-11 rounded-lg border border-ink/20 bg-white/80 px-4 py-2 text-sm font-medium hover:bg-white disabled:opacity-50"
            >
              {syncing ? "同步中…" : "同步行情"}
            </button>
            <button
              type="button"
              onClick={() => handleSync(true)}
              disabled={syncing}
              title="忽略本地缓存，强制全量重拉"
              className="min-h-11 rounded-lg border border-ink/10 bg-transparent px-3 py-2 text-xs font-medium text-ink/60 hover:bg-ink/5 disabled:opacity-50 sm:text-sm"
            >
              强制全量
            </button>
          </div>
        </div>
      </section>

      {syncNote && (
        <div className="mb-4 rounded-lg border border-moss/25 bg-moss/10 px-4 py-3 text-sm text-moss">
          {syncNote}
        </div>
      )}
      {data?.warning && (
        <div className="mb-4 rounded-lg border border-clay/30 bg-clay/10 px-4 py-3 text-sm text-clay">
          {data.warning}
        </div>
      )}
      {error && (
        <div className="mb-4 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
          <p className="mt-1 text-xs">
            请确认已运行 <code>./scripts/dev.sh</code>，并打开
            http://127.0.0.1:3000（页面通过同源 /api 代理访问后端）
          </p>
        </div>
      )}
      {loading && !data && (
        <p className="text-ink/60">正在计算估值与趋势合成信号…</p>
      )}

      <div className="grid gap-4">
        {data?.items.map((item) => (
          <OperationCard key={item.symbol} item={item} />
        ))}
      </div>
    </main>
  );
}
