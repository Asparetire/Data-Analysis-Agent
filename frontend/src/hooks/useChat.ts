import { useCallback, useRef } from 'react';
import { streamChat, type StreamEvent } from '../services/api';
import { useChatStore } from '../store/chatStore';
import type { ChatMessage } from '../types';

function describeStreamError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

/**
 * Replace the last assistant message in the store with a patched version.
 * The store doesn't expose this directly because only this hook uses it.
 */
function patchLastAssistant(patch: Partial<ChatMessage>) {
  useChatStore.setState((s) => {
    for (let i = s.messages.length - 1; i >= 0; i -= 1) {
      if (s.messages[i].role === 'assistant') {
        const messages = [...s.messages];
        messages[i] = { ...messages[i], ...patch };
        return { messages };
      }
    }
    return s;
  });
}

export function useChat() {
  const messages = useChatStore((s) => s.messages);
  const isLoading = useChatStore((s) => s.isLoading);
  const sessionId = useChatStore((s) => s.sessionId);
  const ensureSession = useChatStore((s) => s.ensureSession);
  const setSessionId = useChatStore((s) => s.setSessionId);
  const appendMessage = useChatStore((s) => s.appendMessage);
  const setLoading = useChatStore((s) => s.setLoading);
  const clearChat = useChatStore((s) => s.clearChat);

  // Cancellation token so an in-flight stream can be aborted on reset.
  const cancelRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(
    async (content: string, dataSourceId?: string) => {
      const trimmed = content.trim();
      if (!trimmed) return;

      let activeSessionId = sessionId;
      if (!activeSessionId) {
        try {
          activeSessionId = await ensureSession();
        } catch (e) {
          appendMessage({
            role: 'assistant',
            content: `无法创建会话：${describeStreamError(e)}`,
            timestamp: new Date().toISOString(),
          });
          return;
        }
      }

      const userTs = new Date().toISOString();
      appendMessage({ role: 'user', content: trimmed, timestamp: userTs });
      // Placeholder assistant message that we'll patch in place as events arrive.
      const assistantTs = new Date().toISOString();
      appendMessage({
        role: 'assistant',
        content: '思考中…',
        timestamp: assistantTs,
      });
      setLoading(true);

      const controller = new AbortController();
      cancelRef.current = controller;

      try {
        for await (const evt of streamChat(activeSessionId, trimmed, dataSourceId)) {
          if (controller.signal.aborted) break;
          handleStreamEvent(evt, activeSessionId, setSessionId);
        }
        if (controller.signal.aborted) {
          patchLastAssistant({ content: '(已停止生成)' });
        }
      } catch (err) {
        patchLastAssistant({ content: `Request failed: ${describeStreamError(err)}` });
      } finally {
        if (cancelRef.current === controller) cancelRef.current = null;
        setLoading(false);
      }
    },
    [sessionId, ensureSession, setSessionId, appendMessage, setLoading],
  );

  const abort = useCallback(() => {
    cancelRef.current?.abort();
    cancelRef.current = null;
    setLoading(false);
  }, [setLoading]);

  return { messages, isLoading, sessionId, sendMessage, clearChat, abort };
}

function handleStreamEvent(
  evt: StreamEvent,
  currentSessionId: string,
  setSessionId: (id: string) => void,
) {
  if (evt.event === 'chunk') {
    const payload = evt.data as { type: string; message?: string; content?: unknown[] };
    if (payload.type === 'progress') {
      patchLastAssistant({ content: payload.message || '处理中…' });
    } else if (payload.type === 'data') {
      // Row data chunks are surfaced as a small summary in the assistant text;
      // the data itself rides in `data_chunks` for the table renderer.
      const content = (payload.content as unknown[]) || [];
      const data_chunks = content as Record<string, unknown>[];
      patchLastAssistant({
        content: `已接收 ${data_chunks.length} 行数据`,
        data_chunks,
      });
    }
    return;
  }

  if (evt.event === 'end') {
    const payload = evt.data as {
      message?: string;
      chart_config?: unknown;
      sql_query?: string | null;
      session_id?: string;
    };
    if (payload.session_id && payload.session_id !== currentSessionId) {
      setSessionId(payload.session_id);
    }
    patchLastAssistant({
      content: payload.message || '(empty response)',
      chartData: payload.chart_config ?? undefined,
      sqlQuery: payload.sql_query ?? undefined,
    });
    return;
  }

  if (evt.event === 'error') {
    const payload = evt.data as { message?: string };
    patchLastAssistant({ content: `Error: ${payload.message || 'unknown error'}` });
  }
}
