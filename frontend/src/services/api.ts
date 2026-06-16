import axios from 'axios';
import type { ChatMessageItem, DataSource, SessionView, UploadResponse } from '../types';

// Relative base URL so the Vite dev server's /api proxy and any production
// reverse proxy both work without changes.
const API_BASE_URL = '/api/v1';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

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

export const schemaDataSource = async (id: string) => {
  const response = await api.get<{ schema: { name: string; type: string }[] }>(
    `/datasources/${id}/schema`,
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
): AsyncGenerator<StreamEvent, void, void> {
  const response = await fetch(`${API_BASE_URL}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      session_id: sessionId,
      message,
      data_source_id: dataSourceId,
    }),
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

      let sep = buffer.indexOf('\n\n');
      while (sep !== -1) {
        const block = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const event = parseEventBlock(block);
        if (event) yield event;
        sep = buffer.indexOf('\n\n');
      }
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
