import type { ChatMessage } from '../types';

/** Generate a stable session id used to thread messages through the backend. */
export function generateSessionId(): string {
  return `session_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

/** Friendly timestamp like "14:32" or "Yesterday 14:32". */
export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** Format a byte count for upload size labels. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Extract the user-facing reply from a chain of messages. */
export function lastAssistantText(messages: ChatMessage[]): string {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'assistant') return messages[i].content;
  }
  return '';
}

/** Validate a file before upload: type and (optional) size cap. */
export function validateFile(file: File, maxBytes = 50 * 1024 * 1024): string | null {
  const allowed = ['.csv', '.xlsx', '.xls', '.json'];
  const lower = file.name.toLowerCase();
  if (!allowed.some((ext) => lower.endsWith(ext))) {
    return `Unsupported file type. Allowed: ${allowed.join(', ')}`;
  }
  if (file.size > maxBytes) {
    return `File too large. Max ${formatBytes(maxBytes)}.`;
  }
  return null;
}

/** Convert an ECharts option into a string the backend can echo back for debugging. */
export function safeStringify(value: unknown, maxLen = 4000): string {
  try {
    const s = JSON.stringify(value, null, 2);
    if (s.length <= maxLen) return s;
    return `${s.slice(0, maxLen)}\n... (truncated)`;
  } catch {
    return String(value);
  }
}

/** Convert tabular rows to a CSV string (RFC 4180-ish: quote on `, " \n`). */
export function rowsToCSV(rows: Record<string, unknown>[]): string {
  if (rows.length === 0) return '';
  const cols = Object.keys(rows[0]);
  const escape = (v: unknown): string => {
    if (v === null || v === undefined) return '';
    const s = String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const header = cols.join(',');
  const body = rows.map((r) => cols.map((c) => escape(r[c])).join(',')).join('\n');
  return `${header}\n${body}`;
}

/** Trigger a browser download for arbitrary text content via a Blob URL. */
export function downloadText(filename: string, content: string, mime = 'text/plain'): void {
  const blob = new Blob([content], { type: `${mime};charset=utf-8;` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Defer revoke so Safari's XHR pickup doesn't race the download.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Trigger a browser download for an existing data URL (e.g. echarts.getDataURL). */
export function downloadDataURL(filename: string, dataUrl: string): void {
  const a = document.createElement('a');
  a.href = dataUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
