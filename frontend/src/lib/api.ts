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
}

export interface Dashboard {
  date: string;
  base_amount: number;
  total_buy_amount: number;
  normalized: boolean;
  items: EnsembleItem[];
  warning?: string | null;
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
}

export interface Holding {
  symbol: string;
  shares: number;
  cost_price: number;
  market_value?: number | null;
  take_profit_stage: number;
}

export interface Portfolio {
  holdings: Holding[];
  cash: number;
}

export interface UserSettings {
  base_amount: number;
  hard_veto_enabled: boolean;
  normalize_buy_cap: number;
  target_weights?: Record<string, number> | null;
  ma_short: number;
  ma_long: number;
  buy_frequency: "weekly";
  profit_take_enabled: boolean;
  profit_take_return: number;
  valuation_reduce_percentile: number;
  valuation_exit_percentile: number;
}

export interface MarketPoint {
  date: string;
  close: number;
  ma_short?: number | null;
  ma_long?: number | null;
  drawdown?: number | null;
  pe_percentile?: number | null;
  pb_percentile?: number | null;
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
}

export interface DatabaseOverview {
  db_path: string;
  db_size_bytes: number;
  signal_date: string | null;
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
  source: string | null;
  updated_at: string | null;
}

export interface DatabaseRowsPage {
  items: DatabaseRow[];
  next_cursor: string | null;
  has_more: boolean;
  limit: number;
}

const API_BASE =
  typeof window !== "undefined"
    ? "" // browser: same-origin via Next rewrite → no CORS
    : process.env.API_URL ||
      process.env.NEXT_PUBLIC_API_URL ||
      "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text();
    try {
      const body = JSON.parse(text) as { detail?: unknown };
      if (typeof body.detail === "string") {
        throw new Error(body.detail);
      }
      if (body.detail != null) {
        throw new Error(JSON.stringify(body.detail));
      }
    } catch (e) {
      if (e instanceof SyntaxError) {
        throw new Error(text || `Request failed: ${res.status}`);
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
  settings: () => request<UserSettings>("/api/settings"),
  saveSettings: (body: UserSettings) =>
    request<UserSettings>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  sync: () =>
    request<Record<string, unknown>>("/api/jobs/sync?use_mock=false", {
      method: "POST",
    }),
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

export function actionClass(action: Action): string {
  return {
    buy: "bg-moss text-white",
    pause: "bg-ink/70 text-white",
    reduce: "bg-clay text-white",
    hold: "bg-steel text-white",
  }[action];
}
