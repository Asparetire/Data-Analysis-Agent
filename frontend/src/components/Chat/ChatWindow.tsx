import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Send, Trash2, Terminal, Square, Table, RefreshCw, Download, Image } from 'lucide-react';
import type { EChartsOption } from 'echarts';
import { useChat } from '../../hooks/useChat';
import { useChatStore } from '../../store/chatStore';
import Chart, { type ChartHandle } from '../Chart';
import { downloadDataURL, downloadText, formatTime, rowsToCSV, safeStringify } from '../../utils';
import { useT } from '../../hooks/useUi';
import PaginatedTable from '../PaginatedTable';
import type { ChatMessage } from '../../types';
import './ChatWindow.css';

interface ChatWindowProps {
  dataSourceId?: string;
}

export default function ChatWindow({ dataSourceId }: ChatWindowProps) {
  const { messages, isLoading, sendMessage, clearChat, abort, regenerate } = useChat();
  const activeName = useChatStore((s) => s.activeDataSourceName);
  const dataSources = useChatStore((s) => s.dataSources);
  const boundIds = useChatStore((s) => s.boundDataSourceIds);
  const t = useT();
  const [value, setValue] = useState('');
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const boundNames = boundIds.map((id) => dataSources.find((d) => d.id === id)?.name || id);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [value]);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || isLoading) return;
    sendMessage(trimmed, dataSourceId);
    setValue('');
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  // Global shortcuts:
  //  - Ctrl/Cmd + K  → focus the input
  //  - Ctrl/Cmd + Enter → send the current draft from anywhere on the page
  // We attach to window so users don't need to click the textarea first.
  useEffect(() => {
    const handler = (e: globalThis.KeyboardEvent) => {
      const meta = e.ctrlKey || e.metaKey;
      if (!meta) return;
      const key = e.key.toLowerCase();
      if (key === 'k') {
        e.preventDefault();
        textareaRef.current?.focus();
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        submit();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
    // submit closes over `value` and `isLoading`; rebind so it sees fresh values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, isLoading, dataSourceId]);

  const lastAssistantIdx = (() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === 'assistant') return i;
    }
    return -1;
  })();

  return (
    <div className="chat-window">
      <div className="chat-header">
        <div>
          <div className="title">{t('chat.title')}</div>
          <div className="subtitle">
            {dataSourceId && activeName
              ? t('chat.currentDataSource', { name: activeName })
              : t('chat.noDataSource')}
            {boundNames.length > 1 ? (
              <span
                className="bound-chips"
                title={boundNames.join(' / ')}
                style={{ marginLeft: 8 }}
              >
                {boundNames.slice(1).map((n) => (
                  <span key={n} className="bound-chip">
                    +{n}
                  </span>
                ))}
              </span>
            ) : null}
          </div>
        </div>
        <button
          type="button"
          className="clear-btn"
          onClick={clearChat}
          disabled={messages.length === 0 || isLoading}
        >
          <Trash2 size={12} /> {t('chat.clear')}
        </button>
      </div>

      <div className="chat-messages">
        {messages.length === 0 ? (
          <div className="empty-state">
            <h2>{t('chat.empty.title')}</h2>
            <p>{t('chat.empty.body')}</p>
            <div className="hint">
              <span>{t('chat.empty.hint1')}</span>
              <span>{t('chat.empty.hint2')}</span>
              <span>{t('chat.empty.hint3')}</span>
            </div>
            <div className="hint" style={{ marginTop: 8 }}>
              <span>{t('chat.empty.shortcuts')}</span>
            </div>
          </div>
        ) : (
          messages.map((msg, i) => (
            <MessageBubble
              key={`${msg.timestamp}-${i}`}
              message={msg}
              isLive={isLoading && i === messages.length - 1 && msg.role === 'assistant'}
              canRegenerate={!isLoading && msg.role === 'assistant' && i === lastAssistantIdx}
              onRegenerate={() => regenerate(dataSourceId)}
            />
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      <form
        className="chat-input-form"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={
            dataSourceId
              ? t('chat.input.placeholderWithSource')
              : t('chat.input.placeholderNoSource')
          }
          rows={1}
          disabled={isLoading}
        />
        {isLoading ? (
          <button type="button" onClick={abort} title={t('chat.stop')} className="stop-btn">
            <Square size={14} fill="currentColor" /> {t('chat.stop')}
          </button>
        ) : (
          <button type="submit" disabled={!value.trim()} title={t('chat.send')}>
            <Send size={18} />
          </button>
        )}
      </form>
    </div>
  );
}

interface MessageBubbleProps {
  message: ChatMessage;
  isLive: boolean;
  canRegenerate: boolean;
  onRegenerate: () => void;
}

function MessageBubble({ message, isLive, canRegenerate, onRegenerate }: MessageBubbleProps) {
  const t = useT();
  const isUser = message.role === 'user';
  const chartOption = isChartOption(message.chartData)
    ? (message.chartData as EChartsOption)
    : null;
  const hasChunks = Array.isArray(message.data_chunks) && message.data_chunks.length > 0;
  const chartRef = useRef<ChartHandle | null>(null);

  const downloadCSV = () => {
    if (!message.data_chunks || message.data_chunks.length === 0) return;
    const csv = rowsToCSV(message.data_chunks);
    const ts = new Date().toISOString().replace(/[:.]/g, '-');
    downloadText(`data-${ts}.csv`, csv, 'text/csv');
  };

  const downloadPNG = () => {
    const url = chartRef.current?.getPngDataURL();
    if (!url) return;
    const ts = new Date().toISOString().replace(/[:.]/g, '-');
    downloadDataURL(`chart-${ts}.png`, url);
  };

  return (
    <div className={`message ${isUser ? 'user-message' : 'assistant-message'}`}>
      <div className="message-content">
        <p className={isLive ? 'live' : undefined}>
          {message.content || (isUser ? '' : t('chat.emptyReply'))}
          {isLive ? <span className="caret">▍</span> : null}
        </p>

        {message.sqlQuery ? (
          <>
            <div className="sql-label">
              <Terminal size={11} /> {t('chat.sqlLabel')}
            </div>
            <pre className="sql-block">
              <code>{message.sqlQuery}</code>
            </pre>
          </>
        ) : null}

        {hasChunks ? (
          <details className="data-chunks" open>
            <summary>
              <Table size={11} /> {t('chat.dataRows', { n: message.data_chunks!.length })}
              <button
                type="button"
                className="inline-action"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  downloadCSV();
                }}
                title={t('chat.csvExport')}
              >
                <Download size={11} /> {t('chat.csvExport')}
              </button>
            </summary>
            <div className="data-chunks-table">
              <PaginatedTable
                rows={message.data_chunks!}
                pageSize={20}
                maxHeight={220}
                emptyText={t('common.emptyData')}
              />
            </div>
          </details>
        ) : null}

        {chartOption ? (
          <div className="chart-block">
            <div className="chart-toolbar">
              <button
                type="button"
                className="inline-action"
                onClick={downloadPNG}
                title={t('chat.pngExport')}
              >
                <Image size={11} /> {t('chat.pngExport')}
              </button>
            </div>
            <Chart ref={chartRef} option={chartOption} height="100%" />
          </div>
        ) : message.chartData && !isChartOption(message.chartData) ? (
          <details style={{ marginTop: 8 }}>
            <summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--color-text-muted)' }}>
              {t('chat.viewChartData')}
            </summary>
            <pre className="sql-block">{safeStringify(message.chartData)}</pre>
          </details>
        ) : null}

        {!isUser ? (
          <div className="message-meta">
            <span>{formatTime(message.timestamp)}</span>
            {canRegenerate ? (
              <button
                type="button"
                className="meta-action"
                onClick={onRegenerate}
                title={t('chat.regenerateTitle')}
              >
                <RefreshCw size={11} /> {t('chat.regenerate')}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function isChartOption(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  return (
    Array.isArray(v.series) &&
    (v.xAxis !== undefined || v.series.some((s) => (s as { type?: string }).type === 'pie'))
  );
}
