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

function OperationCard({ item }: { item: EnsembleItem }) {
  const [open, setOpen] = useState(false);
  return (
    <article className="rounded-xl border border-ink/10 bg-white/70 p-5 shadow-sm backdrop-blur">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="font-display text-2xl text-ink">{item.name}</h2>
            <ActionBadge action={item.action} />
            {item.hard_veto && (
              <span className="rounded bg-ink/10 px-2 py-0.5 text-xs text-ink/70">
                硬否决
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-ink/60">
            ETF {item.etf_code} · 目标仓位 {(item.target_weight * 100).toFixed(0)}%
          </p>
        </div>
        <div className="text-right">
          {item.action === "reduce" && item.reduce_ratio != null ? (
            <>
              <p className="text-xs uppercase tracking-wider text-ink/45">建议卖出</p>
              <p className="font-display text-3xl text-clay">
                {(item.reduce_ratio * 100).toFixed(0)}%
              </p>
              <p className="text-sm text-ink/60">卖出当前 ETF 份额</p>
            </>
          ) : (
            <>
              <p className="text-xs uppercase tracking-wider text-ink/45">建议买入</p>
              <p className="font-display text-3xl text-moss">
                ¥{item.amount.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}
              </p>
              <p className="text-sm text-ink/60">倍数 ×{item.multiplier.toFixed(2)}</p>
            </>
          )}
        </div>
      </div>
      <p className="mt-3 text-sm leading-relaxed text-ink/75">{item.reason}</p>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="mt-4 text-sm font-medium text-steel hover:underline"
      >
        {open ? "收起策略明细" : "展开估值、趋势与止盈明细"}
      </button>
      {open && (
        <ul className="mt-3 grid gap-2 sm:grid-cols-2">
          {item.strategies.map((s) => (
            <li
              key={s.strategy}
              className="rounded-lg border border-ink/8 bg-paper/80 p-3 text-sm"
            >
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="font-semibold">
                  {STRATEGY_LABELS[s.strategy] || s.strategy}
                </span>
                <span className="tabular-nums text-ink/70">
                  ×{s.multiplier.toFixed(2)} · {actionLabel(s.action)}
                </span>
              </div>
              <p className="text-ink/65">{s.reason}</p>
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

  async function handleSync() {
    setSyncing(true);
    try {
      await api.sync();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "同步失败");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <main>
      <section className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl text-ink">今日操作清单</h1>
          <p className="mt-1 text-sm text-ink/60">
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
        <div className="flex items-center gap-3">
          {data && (
            <div className="rounded-lg bg-ink px-4 py-2 text-paper">
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
          <button
            type="button"
            onClick={handleSync}
            disabled={syncing}
            className="rounded-lg border border-ink/20 bg-white/80 px-4 py-2 text-sm font-medium hover:bg-white disabled:opacity-50"
          >
            {syncing ? "同步中…" : "同步行情"}
          </button>
        </div>
      </section>

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
