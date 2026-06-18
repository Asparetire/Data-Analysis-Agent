export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  /**
   * Chart payload from the backend. The agent emits an ECharts option dict
   * in the common path, but the raw `chart_data` from the LLM tool can also
   * be any structured object, so we type it as `unknown` and let consumers
   * narrow (see `isChartOption` in ChatWindow).
   */
  chartData?: unknown;
  sqlQuery?: string;
  /** Streamed row payloads, populated by /chat/stream chunks. */
  data_chunks?: Record<string, unknown>[];
}

export interface ChatMessageItem {
  role: 'user' | 'assistant';
  content: string;
  chart_data?: unknown;
  sql_query?: string;
  timestamp?: string;
}

export interface ChatResponse {
  session_id: string;
  message: string;
  chart_data?: unknown;
  sql_query?: string | null;
  error?: string;
}

export interface DataSource {
  id: string;
  name: string;
  filename: string;
  type: 'csv' | 'excel' | 'json' | 'database';
  created_at: string;
}

export interface UploadResponse {
  file_id: string;
  filename: string;
  status: string;
  message: string;
}

export interface SessionView {
  session_id: string;
  data_source_id?: string | null;
  /** Phase 3C: every data source the session is bound to. First entry is the primary. */
  data_source_ids?: string[];
  chat_history: ChatMessageItem[];
  intermediate_results?: unknown;
  last_query?: string | null;
  created_at?: string;
  updated_at?: string;
  ttl_seconds: number;
}

/** Phase 3E: one executed query against a data source, for the lineage panel. */
export interface LineageEntry {
  ts: number;
  sql: string;
  source_ids: string[];
  tables: string[];
  row_count: number;
  duration_ms: number;
  ok: boolean;
  cache_hit: boolean;
  error?: string;
}

export interface LineageResponse {
  data_source_id: string;
  entries: LineageEntry[];
  total: number;
}

/** Phase 4A: authenticated user. */
export interface UserView {
  id: string;
  email: string;
  is_active: boolean;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}
