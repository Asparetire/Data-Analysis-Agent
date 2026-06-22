import axios from 'axios';
import type {
  ChatMessageItem,
  DataSource,
  LineageResponse,
  SessionView,
  TokenResponse,
  UploadResponse,
  UserView,
} from '../types';

// Relative base URL so the Vite dev server's /api proxy and any production
// reverse proxy both work without changes.
const API_BASE_URL = '/api/v1';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Phase 4A: attach the JWT to every request. The token lives in localStorage
// (see authStore) — reading it lazily here avoids a circular import with
// the Zustand store and stays in sync even when login/logout rotates it.
api.interceptors.request.use((config) => {
  try {
    const token = window.localStorage.getItem('data-analysis-agent:accessToken');
    if (token) {
      config.headers = config.headers ?? {};
      config.headers.Authorization = `Bearer ${token}`;
    }
  } catch {
    /* localStorage may be unavailable (private mode) — ignore */
  }
  return config;
});

// Phase 4A: on 401, try a single refresh-token rotation, then replay the
// request once. If refresh fails, drop the stored tokens and let the caller
// surface the error (the route guard will redirect to /login).
let refreshPromise: Promise<string | null> | null = null;

api.interceptors.response.use(
  (resp) => resp,
  async (error) => {
    const original = error.config ?? {};
    if (
      error.response?.status === 401 &&
      !original.__retried &&
      !original.url?.includes('/auth/')
    ) {
      original.__retried = true;
      if (!refreshPromise) {
        // Lazy import to avoid a cycle: authStore imports this module.
        const { useAuthStore } = await import('../store/authStore');
        refreshPromise = useAuthStore
          .getState()
          .tryRefresh()
          .finally(() => {
            refreshPromise = null;
          });
      }
      const newToken = await refreshPromise;
      if (newToken) {
        original.headers = original.headers ?? {};
        original.headers.Authorization = `Bearer ${newToken}`;
        return api(original);
      }
    }
    return Promise.reject(error);
  },
);

// ---------------------------------------------------------------------------
// Auth (Phase 4A)
// ---------------------------------------------------------------------------

export const register = async (email: string, password: string) => {
  const resp = await api.post<TokenResponse>('/auth/register', { email, password });
  return resp.data;
};

export const login = async (email: string, password: string) => {
  const resp = await api.post<TokenResponse>('/auth/login', { email, password });
  return resp.data;
};

export const refresh = async (refreshToken: string) => {
  const resp = await api.post<TokenResponse>('/auth/refresh', { refresh_token: refreshToken });
  return resp.data;
};

export const logout = async (refreshToken: string) => {
  await api.post('/auth/logout', { refresh_token: refreshToken });
};

export const getCurrentUser = async () => {
  const resp = await api.get<UserView>('/auth/me');
  return resp.data;
};

export const uploadFile = async (file: File) => {
  const formData = new FormData();
  formData.append('file', file);

  const response = await api.post<UploadResponse>('/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });

  return response.data;
};

export const getDataSources = async () => {
  const response = await api.get<DataSource[]>('/datasources');
  return response.data;
};

export const previewDataSource = async (id: string, limit = 5) => {
  const response = await api.get<{ rows: Record<string, unknown>[]; count: number }>(
    `/datasources/${id}/preview`,
    { params: { limit } },
  );
  return response.data;
};

export const schemaDataSource = async (id: string, table?: string) => {
  const response = await api.get<{ schema: { name: string; type: string }[] }>(
    `/datasources/${id}/schema`,
    { params: table ? { table } : undefined },
  );
  return response.data;
};

// Phase 4D: server-side pagination. The browser keeps offset/limit/sort state
// and asks the server for one page at a time, so we never load more than the
// page size into memory — important when a table has 100k+ rows.
export interface RowsPage {
  table: string;
  rows: Record<string, unknown>[];
  columns: string[];
  total: number;
  offset: number;
  limit: number;
}

export const fetchRows = async (
  id: string,
  params: {
    table?: string;
    offset?: number;
    limit?: number;
    sort?: string;
    dir?: 'asc' | 'desc';
  },
) => {
  const response = await api.get<RowsPage>(`/datasources/${id}/rows`, { params });
  return response.data;
};

export const listTables = async (id: string) => {
  const response = await api.get<{ tables: { name: string; row_count: number }[] }>(
    `/datasources/${id}/tables`,
  );
  return response.data;
};

export const createSession = async () => {
  const response = await api.post<SessionView>('/sessions');
  return response.data;
};

export const getSession = async (sessionId: string) => {
  const response = await api.get<SessionView>(`/sessions/${sessionId}`);
  return response.data;
};

export const updateSession = async (
  sessionId: string,
  updates: {
    data_source_id?: string | null;
    data_source_ids?: string[];
    chat_history?: ChatMessageItem[];
    intermediate_results?: unknown;
    last_query?: string | null;
  },
) => {
  const response = await api.patch<SessionView>(`/sessions/${sessionId}`, updates);
  return response.data;
};

export const deleteSession = async (sessionId: string) => {
  await api.delete(`/sessions/${sessionId}`);
};

export const deleteDataSource = async (dataSourceId: string) => {
  const response = await api.delete<{ status: string; sessions_removed: number }>(
    `/datasources/${dataSourceId}`,
  );
  return response.data;
};

export const renameDataSource = async (dataSourceId: string, displayName: string) => {
  const response = await api.patch<DataSource>(`/datasources/${dataSourceId}`, {
    display_name: displayName,
  });
  return response.data;
};

export const getDataSourceLineage = async (dataSourceId: string, limit = 50) => {
  const response = await api.get<LineageResponse>(`/datasources/${dataSourceId}/lineage`, {
    params: { limit },
  });
  return response.data;
};

export type StreamEvent =
  | {
      event: 'chunk';
      data:
        | { type: 'progress'; message: string }
        | { type: 'token'; text: string; delta: number }
        | { type: 'data'; content: Record<string, unknown>[] };
    }
  | {
      event: 'end';
      data: {
        type: 'complete';
        message: string;
        chart_config?: unknown;
        sql_query?: string | null;
        session_id?: string;
      };
    }
  | { event: 'error'; data: { type: 'error'; code: number; message: string } }
  | { event: string; data: unknown };

function parseEventBlock(block: string): StreamEvent | null {
  let eventName = 'message';
  const dataLines: string[] = [];
  for (const rawLine of block.split('\n')) {
    const line = rawLine.replace(/\r$/, '');
    if (!line) continue;
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) return null;
  const payload = dataLines.join('\n');
  let parsed: unknown;
  try {
    parsed = JSON.parse(payload);
  } catch {
    parsed = payload;
  }
  return { event: eventName, data: parsed } as StreamEvent;
}

export async function* streamChat(
  sessionId: string,
  message: string,
  dataSourceId?: string,
  dataSourceIds?: string[],
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent, void, void> {
  const body: Record<string, unknown> = { session_id: sessionId, message };
  if (dataSourceId) body.data_source_id = dataSourceId;
  // Send the full list when we have one -- the server will merge/dedupe.
  if (dataSourceIds && dataSourceIds.length > 0) {
    body.data_source_ids = dataSourceIds;
  }
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  };
  try {
    const token = window.localStorage.getItem('data-analysis-agent:accessToken');
    if (token) headers.Authorization = `Bearer ${token}`;
  } catch {
    /* ignore */
  }
  // Phase 6: pass the abort signal through to fetch so the TCP connection
  // actually closes when the user cancels. Without this the backend keeps
  // running the graph (and burning LLM tokens) until it finishes — the
  // previous "abort" only stopped the client from iterating events.
  const response = await fetch(`${API_BASE_URL}/chat/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok || !response.body) {
    let detail = `HTTP ${response.status}`;
    try {
      const text = await response.text();
      if (text) detail += `: ${text}`;
    } catch {
      /* ignore */
    }
    yield { event: 'error', data: { type: 'error', code: response.status, message: detail } };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Normalize CRLF / CR to LF so the event-separator search only has to
      // handle one variant. sse-starlette emits `\r\n` line endings, so the
      // event separator is `\r\n\r\n` on the wire -- a plain `indexOf('\n\n')`
      // misses it (the two LFs are split by a CR) and no events ever parse.
      const normalized = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
      buffer = '';

      let sep = normalized.indexOf('\n\n');
      let last = 0;
      while (sep !== -1) {
        const block = normalized.slice(last, sep);
        last = sep + 2;
        const event = parseEventBlock(block);
        if (event) yield event;
        sep = normalized.indexOf('\n\n', last);
      }
      // Keep the trailing partial (no double-LF yet) for the next iteration.
      if (last > 0) buffer = normalized.slice(last);
      else buffer = normalized;
    }
    // Flush any trailing data the server emitted without a final blank line.
    if (buffer.trim()) {
      const event = parseEventBlock(buffer);
      if (event) yield event;
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* ignore */
    }
  }
}

export default api;
