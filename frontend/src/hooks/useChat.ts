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
      // The content stays empty until the first LLM token arrives so the user
      // doesn't see a stale "思考中…" right above the streaming text.
      const assistantTs = new Date().toISOString();
      appendMessage({
        role: 'assistant',
        content: '',
        timestamp: assistantTs,
      });
      setLoading(true);

      const controller = new AbortController();
      cancelRef.current = controller;

      try {
        const boundIds = useChatStore.getState().boundDataSourceIds;
        for await (const evt of streamChat(
          activeSessionId,
          trimmed,
          dataSourceId,
          boundIds.length > 0 ? boundIds : undefined,
          controller.signal,
        )) {
          if (controller.signal.aborted) break;
          handleStreamEvent(evt, activeSessionId, setSessionId);
        }
        if (controller.signal.aborted) {
          // Preserve whatever text streamed in before stop; just append a marker.
          useChatStore.setState((s) => {
            for (let i = s.messages.length - 1; i >= 0; i -= 1) {
              if (s.messages[i].role === 'assistant') {
                const messages = [...s.messages];
                messages[i] = {
                  ...messages[i],
                  content: messages[i].content
                    ? `${messages[i].content}\n(已停止生成)`
                    : '(已停止生成)',
                };
                return { messages };
              }
            }
            return s;
          });
        }
      } catch (err) {
        // AbortError is expected when the user clicks stop — don't surface
        // it as a request failure, the abort handler already marked the bubble.
        if (err instanceof DOMException && err.name === 'AbortError') {
          // no-op
        } else {
          patchLastAssistant({ content: `Request failed: ${describeStreamError(err)}` });
        }
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

  const regenerate = useCallback(
    async (dataSourceId?: string) => {
      if (isLoading) return;
      // Find the last user turn and resend it. If the last assistant turn was
      // a placeholder/error we drop it first so the UI shows a single new
      // assistant bubble instead of stacking on top of the failed one.
      const list = useChatStore.getState().messages;
      let lastUserIdx = -1;
      for (let i = list.length - 1; i >= 0; i -= 1) {
        if (list[i].role === 'user') {
          lastUserIdx = i;
          break;
        }
      }
      if (lastUserIdx === -1) return;
      const lastUser = list[lastUserIdx];
      // Drop the trailing assistant reply (and the user message we're about
      // to re-add) so we don't duplicate them.
      useChatStore.setState({ messages: list.slice(0, lastUserIdx) });
      await sendMessage(lastUser.content, dataSourceId);
    },
    [isLoading, sendMessage],
  );

  return { messages, isLoading, sessionId, sendMessage, clearChat, abort, regenerate };
}

function handleStreamEvent(
  evt: StreamEvent,
  currentSessionId: string,
  setSessionId: (id: string) => void,
) {
  if (evt.event === 'chunk') {
    const payload = evt.data as {
      type: string;
      message?: string;
      text?: string;
      content?: unknown[];
    };
    if (payload.type === 'progress') {
      // Don't clobber already-streamed text with a transient progress blurb.
      // Only set the blurb when the bubble is still empty (LLM hasn't started).
      useChatStore.setState((s) => {
        for (let i = s.messages.length - 1; i >= 0; i -= 1) {
          if (s.messages[i].role === 'assistant') {
            if (s.messages[i].content) return s; // already streaming
            const messages = [...s.messages];
            messages[i] = { ...messages[i], content: payload.message || '处理中…' };
            return { messages };
          }
        }
        return s;
      });
    } else if (payload.type === 'token') {
      // Append to the live assistant bubble. This is the per-token path the
      // user sees word-by-word. We read-modify-write through the store to
      // avoid losing other fields (sql/chart) that the end event will set.
      const text = payload.text || '';
      if (!text) return;
      useChatStore.setState((s) => {
        for (let i = s.messages.length - 1; i >= 0; i -= 1) {
          if (s.messages[i].role === 'assistant') {
            const messages = [...s.messages];
            messages[i] = {
              ...messages[i],
              content: (messages[i].content || '') + text,
            };
            return { messages };
          }
        }
        return s;
      });
    } else if (payload.type === 'data') {
      // Row data chunks are surfaced as a small summary in the assistant text;
      // the data itself rides in `data_chunks` for the table renderer. We only
      // set the summary when streaming text is *not* in progress, so we don't
      // clobber an LLM that just produced a sentence.
      const content = (payload.content as unknown[]) || [];
      const data_chunks = content as Record<string, unknown>[];
      useChatStore.setState((s) => {
        for (let i = s.messages.length - 1; i >= 0; i -= 1) {
          if (s.messages[i].role === 'assistant') {
            // Append to streamed text rather than replacing it.
            const summary = `已接收 ${data_chunks.length} 行数据`;
            const messages = [...s.messages];
            messages[i] = {
              ...messages[i],
              content: messages[i].content ? `${messages[i].content}\n${summary}` : summary,
              data_chunks,
            };
            return { messages };
          }
        }
        return s;
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
    // The streamed tokens already populated `content`. Only overwrite when
    // the server explicitly sent a final message that differs (the safety
    // net for empty-content models). When they match, leave it alone so we
    // don't lose typing-state artifacts.
    useChatStore.setState((s) => {
      for (let i = s.messages.length - 1; i >= 0; i -= 1) {
        if (s.messages[i].role === 'assistant') {
          const incoming = payload.message || '(empty response)';
          const messages = [...s.messages];
          messages[i] = {
            ...messages[i],
            content: messages[i].content || incoming,
            chartData: payload.chart_config ?? messages[i].chartData,
            sqlQuery: payload.sql_query ?? messages[i].sqlQuery,
          };
          return { messages };
        }
      }
      return s;
    });
    return;
  }

  if (evt.event === 'error') {
    const payload = evt.data as { message?: string };
    patchLastAssistant({ content: `Error: ${payload.message || 'unknown error'}` });
  }
}
