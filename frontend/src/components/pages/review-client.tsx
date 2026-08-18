"use client";

import { useEffect, useState } from "react";
import { SignalHistoryEntry, api } from "@/lib/api";

function pct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

export default function ReviewClient() {
  const [data, setData] = useState<SignalHistoryEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .signalHistory(60)
      .then((r) => setData(r.history ?? []))
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main>
      <section className="mb-6">
        <h1 className="font-display text-2xl text-ink sm:text-3xl">信号复盘</h1>
        <p className="mt-1 text-sm leading-relaxed text-ink/60">
          每个过去信号日的决策（买入/暂停/减仓），以及之后 5 / 20 / 60 日与至今的等权平均前瞻收益（基于各标的指数 T-1 收盘）
        </p>
      </section>

      {loading && <p className="text-ink/60">加载信号历史…</p>}
      {error && <p className="text-red-600">{error}</p>}
      {!loading && data.length === 0 && (
        <p className="text-ink/50">
          暂无已缓存的信号记录（看板每次计算后会保存当日信号，生成后此处即可复盘）
        </p>
      )}

      <div className="space-y-3">
        {data.map((d) => {
          const fwd = d.forward || {};
          return (
            <article
              key={d.date}
              className="rounded-xl border border-ink/10 bg-white/70 p-4 shadow-sm backdrop-blur"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="font-display text-lg text-ink">{d.date}</h2>
                <span
                  className={`rounded px-2 py-0.5 text-xs font-semibold ${
                    d.execution_today
                      ? "bg-moss text-white"
                      : "bg-ink/10 text-ink/70"
                  }`}
                >
                  {d.execution_today ? "定投执行日" : "非执行日"}
                </span>
              </div>

              <p className="mt-2 text-sm text-ink/70">
                本期分配{" "}
                <span className="font-semibold text-moss">
                  ¥{d.total_buy_amount.toLocaleString("zh-CN")}
                </span>{" "}
                · 买入×{d.action_counts.buy} 暂停×{d.action_counts.pause}{" "}
                减仓×{d.action_counts.reduce} 观望×{d.action_counts.hold}
              </p>

              <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm tabular-nums text-ink/70">
                <span>+5日 {pct(fwd["5"])}</span>
                <span>+20日 {pct(fwd["20"])}</span>
                <span>+60日 {pct(fwd["60"])}</span>
                <span className="text-ink/90">至今 {pct(fwd.since)}</span>
              </div>

              {d.warning && (
                <p className="mt-2 text-xs text-ink/50">{d.warning}</p>
              )}
            </article>
          );
        })}
      </div>
    </main>
  );
}
