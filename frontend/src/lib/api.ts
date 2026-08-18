export type Action = "buy" | "pause" | "reduce" | "hold";

export interface StrategySignal {
  strategy: string;
  symbol: string;
  action: Action;
  multiplier: number;
  confidence: number;
  reason: string;
  reduce_ratio?: number | null;
  meta?: Record<string, unknown>;
}

export interface EnsembleItem {
  symbol: string;
  name: string;
  etf_code: string;
  target_weight: number;
  action: Action;
  multiplier: number;
  amount: number;
  reduce_ratio?: number | null;
  reason: string;
  strategies: StrategySignal[];
  hard_veto: boolean;
  // R8 rebalance fields (optional, populated when drift >= threshold)
  actual_weight?: number | null;
  weight_drift?: number | null;
  rebalance_reason?: string | null;
}

export type BuyFrequency = "daily" | "weekly" | "monthly";

/** 成长仓空头排列策略：hard_veto=防守（硬停）/ soft=追收益（软降频） */
export type GrowthBearPolicy = "hard_veto" | "soft";

export interface Dashboard {
  date: string;
  base_amount: number;
  period_amount: number;
  buy_frequency: BuyFrequency;
  execution_today: boolean;
  next_execution_date?: string | null;
  total_buy_amount: number;
  normalized: boolean;
  items: EnsembleItem[];
  warning?: string | null;
  pool_factor?: number | null;
  disclaimer: string;
}

export interface SymbolInfo {
  id: string;
  name: string;
  etf_code: string;
  index_code: string;
  target_weight: number;
  role: string;
  valuation_enabled: boolean;
  valuation_proxy: boolean;
  valuation_proxy_label?: string | null;
  strategy_profile?: {
    valuation_mode?: "pe" | "pe_pb_composite";
    pe_weight?: number;
    pb_weight?: number;
    percentile_window?: "5y" | "full";
  };
}

export interface Holding {
  symbol: string;
  shares: number;
  cost_price: number;
  market_value?: number | null;
  take_profit_stage: number;
  trend_state?: "bull" | "mild_bull" | "sandwich" | "bear" | null;
  trailing_armed?: boolean;
  trail_peak_price?: number | null;
  /** 累计已收现金分红（含分红后的总回报口径） */
  dividends_received?: number;
}

export type TradeKind = "deposit" | "buy" | "sell" | "dividend";

export interface TradeInput {
  symbol: string;
  kind: TradeKind;
  date?: string | null;
  amount?: number | null;
  price?: number | null;
  shares?: number | null;
  ratio?: number | null;
  reinvest?: boolean;
}

export interface TradeRecord extends TradeInput {
  applied_at: string;
}

export interface Portfolio {
  holdings: Holding[];
  cash: number;
  trades?: TradeRecord[];
}

export interface UserSettings {
  base_amount: number;
  hard_veto_enabled: boolean;
  normalize_buy_cap: number;
  target_weights?: Record<string, number> | null;
  ma_short: number;
  ma_long: number;
  buy_frequency: BuyFrequency;
  weekly_weekday: number;
  monthly_day: number;
  profit_take_enabled: boolean;
  cash_pool_enabled: boolean;
  /** 全局止盈：估值武装分位（0–1），覆盖各标的 profile 的 trail_arm */
  valuation_reduce_percentile: number;
  /** 全局止盈：估值清仓分位（0–1），覆盖各标的 profile 的 trail_exit */
  valuation_exit_percentile: number;
  /** 成长仓空头策略：hard_veto=防守（硬停） / soft=追收益（软降频） */
  growth_bear_policy: GrowthBearPolicy;
  /** soft 模式下的空头买入倍数（0–1） */
  growth_bear_mult: number;
  /** 提醒推送（R7） */
  notify_enabled: boolean;
  notify_url: string;
  notify_on_execution: boolean;
  notify_on_signal_change: boolean;
}

export interface SignalHistoryEntry {
  date: string;
  execution_today: boolean;
  total_buy_amount: number;
  action_counts: { buy: number; pause: number; reduce: number; hold: number };
  warning?: string | null;
  forward: Record<string, number | null>;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly fieldErrors: Record<string, string> = {}
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface MarketPoint {
  date: string;
  close: number;
  ma_short?: number | null;
  ma_long?: number | null;
  drawdown?: number | null;
  pe?: number | null;
  pb?: number | null;
  pe_percentile?: number | null;
  pb_percentile?: number | null;
  valuation_asof?: string | null;
  valuation_source?: string | null;
}

export interface DatabaseSymbolStats {
  symbol: string;
  rows: number;
  earliest_date: string | null;
  latest_date: string | null;
  updated_at: string | null;
  sources: string | null;
  missing_close: number;
  missing_etf_close: number;
  missing_pe: number;
  missing_pb: number;
  valuation_asof?: string | null;
  valuation_sources?: string | null;
  valuation_lag_sessions?: number | null;
  valuation_fresh?: boolean;
}

export interface DatabaseOverview {
  db_path: string;
  db_size_bytes: number;
  signal_date: string | null;
  calendar_t1?: string | null;
  warehouse_fresh?: boolean;
  overall: {
    total_rows: number;
    symbol_count: number;
    earliest_date: string | null;
    latest_date: string | null;
    missing_close: number;
    missing_etf_close: number;
    missing_pe: number;
    missing_pb: number;
    missing_ma_long: number;
    price_completeness_pct: number;
    valuation_completeness_pct: number;
    valuation_freshness_pct: number;
    etf_completeness_pct: number;
    /** Legacy blend retained for API compatibility. */
    quality_score: number;
  };
  symbols: DatabaseSymbolStats[];
  monthly: Array<{ month: string; rows: number }>;
  sync_meta: Array<{
    symbol: string;
    last_sync_at: string | null;
    latest_date: string | null;
    source: string | null;
    row_count: number;
  }>;
  valuations: Array<{
    symbol: string;
    rows: number;
    earliest_date: string | null;
    latest_date: string | null;
    sources: string | null;
    fetched_at: string | null;
  }>;
}

export interface DatabaseRow {
  symbol: string;
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  etf_close: number | null;
  ma_short: number | null;
  ma_long: number | null;
  drawdown: number | null;
  pe: number | null;
  pb: number | null;
  pe_percentile: number | null;
  pb_percentile: number | null;
  valuation_asof: string | null;
  valuation_source: string | null;
  source: string | null;
  updated_at: string | null;
}

export interface DatabaseRowsPage {
  items: DatabaseRow[];
  next_cursor: string | null;
  has_more: boolean;
  limit: number;
}

export interface SyncSymbolResult {
  symbol: string;
  source: string;
  mode: "skipped" | "incremental" | "full" | "error";
  rows_added?: number;
  rows?: number;
  latest_date?: string | null;
  latest_close?: number | null;
  valuation_source?: string | null;
  error?: string;
  stored_in?: string;
}

export interface SyncResult {
  synced_at: string;
  results: SyncSymbolResult[];
  skipped: number;
  incremental: number;
  fetched: number;
  rows_added: number;
  warning?: string | null;
  live: boolean;
  force: boolean;
  purged?: unknown[];
  data_status?: Record<string, unknown>;
}

const API_BASE =
  typeof window !== "undefined"
    ? "" // browser: same-origin via Next rewrite → no CORS
    : process.env.API_URL ||
      process.env.NEXT_PUBLIC_API_URL ||
      "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers || {}),
      },
      cache: "no-store",
    });
  } catch {
    throw new Error(
      "无法连接后端。请确认已运行 ./scripts/dev.sh，并打开 http://127.0.0.1:3000"
    );
  }
  if (!res.ok) {
    const text = await res.text();
    const looksHtml =
      text.trimStart().startsWith("<!") ||
      /internal server error/i.test(text);
    try {
      const body = JSON.parse(text) as {
        detail?:
          | string
          | Array<{ loc?: Array<string | number>; msg?: string }>;
      };
      if (typeof body.detail === "string") {
        throw new Error(body.detail);
      }
      if (Array.isArray(body.detail)) {
        const fieldErrors: Record<string, string> = {};
        for (const issue of body.detail) {
          const path = (issue.loc || [])
            .filter((part) => part !== "body")
            .map(String)
            .join(".");
          const message = (issue.msg || "输入无效").replace(/^Value error,\s*/, "");
          fieldErrors[path || "_form"] = message;
        }
        throw new ApiError(
          Object.values(fieldErrors).join("；") || "输入参数无效",
          fieldErrors
        );
      }
      if (body.detail != null) {
        throw new Error(JSON.stringify(body.detail));
      }
    } catch (e) {
      if (e instanceof SyntaxError || looksHtml) {
        throw new Error(
          looksHtml || res.status >= 500
            ? `后端不可用（${res.status}）。请重新运行 ./scripts/dev.sh 后刷新。`
            : text || `Request failed: ${res.status}`
        );
      }
      throw e;
    }
    throw new Error(text || `Request failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  dashboard: () => request<Dashboard>("/api/dashboard/today"),
  symbols: () => request<{ symbols: SymbolInfo[] }>("/api/symbols"),
  market: (symbol: string, limit = 365) =>
    request<{ symbol: string; series: MarketPoint[] }>(
      `/api/market/${symbol}?limit=${limit}`
    ),
  portfolio: () => request<Portfolio>("/api/portfolio"),
  savePortfolio: (body: Portfolio) =>
    request<Portfolio>("/api/portfolio", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  saveTrade: (body: TradeInput) =>
    request<{ portfolio: Portfolio; trade: TradeRecord }>("/api/portfolio/trades", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  signalHistory: (limit = 60) =>
    request<{ history: SignalHistoryEntry[] }>(
      `/api/signals/history?limit=${limit}`
    ),
  settings: () => request<UserSettings>("/api/settings"),
  saveSettings: (body: UserSettings) =>
    request<UserSettings>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  sync: (force = false) =>
    request<SyncResult>(
      `/api/jobs/sync?use_mock=false&force=${force ? "true" : "false"}`,
      { method: "POST" }
    ),
  databaseOverview: () => request<DatabaseOverview>("/api/data/overview"),
  databaseRows: (params: {
    symbol?: string;
    dateFrom?: string;
    dateTo?: string;
    cursor?: string;
    limit?: number;
  }) => {
    const query = new URLSearchParams();
    if (params.symbol) query.set("symbol", params.symbol);
    if (params.dateFrom) query.set("date_from", params.dateFrom);
    if (params.dateTo) query.set("date_to", params.dateTo);
    if (params.cursor) query.set("cursor", params.cursor);
    query.set("limit", String(params.limit ?? 100));
    return request<DatabaseRowsPage>(`/api/data/rows?${query.toString()}`);
  },
};

export const STRATEGY_LABELS: Record<string, string> = {
  valuation: "估值定投",
  trend: "均线过滤",
  profit_taking: "分批止盈",
};

export function actionLabel(action: Action): string {
  return { buy: "买入", pause: "暂停", reduce: "减仓", hold: "观望" }[action];
}

/** Display as Chinese full name + security code, e.g. 华泰柏瑞沪深300ETF（510300） */
export function symbolLabel(
  name: string,
  etfCode?: string | null,
  fallbackId?: string
): string {
  const code = (etfCode || "").trim();
  const title = (name || fallbackId || "").trim();
  if (title && code) {
    if (title.includes(code) || title.includes(`（${code}）`)) return title;
    return `${title}（${code}）`;
  }
  return title || code || fallbackId || "";
}

export function actionClass(action: Action): string {
  return {
    buy: "bg-moss text-white",
    pause: "bg-ink/70 text-white",
    reduce: "bg-clay text-white",
    hold: "bg-steel text-white",
  }[action];
}
