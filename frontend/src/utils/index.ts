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
