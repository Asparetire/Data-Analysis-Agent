import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Send, Trash2, Terminal, Square, Table } from 'lucide-react';
import type { EChartsOption } from 'echarts';
import { useChat } from '../../hooks/useChat';
import { useChatStore } from '../../store/chatStore';
import Chart from '../Chart';
import { formatTime, safeStringify } from '../../utils';
import type { ChatMessage } from '../../types';
import './ChatWindow.css';

interface ChatWindowProps {
  dataSourceId?: string;
}

export default function ChatWindow({ dataSourceId }: ChatWindowProps) {
  const { messages, isLoading, sendMessage, clearChat, abort } = useChat();
  const activeName = useChatStore((s) => s.activeDataSourceName);
  const [value, setValue] = useState('');
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

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

  return (
    <div className="chat-window">
      <div className="chat-header">
        <div>
          <div className="title">智能分析对话</div>
          <div className="subtitle">
            {dataSourceId && activeName ? `当前数据源：${activeName}` : '尚未选择数据源'}
          </div>
        </div>
        <button
          type="button"
          className="clear-btn"
          onClick={clearChat}
          disabled={messages.length === 0 || isLoading}
        >
          <Trash2 size={12} /> 清空对话
        </button>
      </div>

      <div className="chat-messages">
        {messages.length === 0 ? (
          <div className="empty-state">
            <h2>开始你的分析</h2>
            <p>上传 CSV / Excel 文件后，可以直接用自然语言提问，例如：</p>
            <div className="hint">
              <span>· 销售额最高的 5 个产品是什么？</span>
              <span>· 统计每个地区的订单数量</span>
              <span>· 画一个按月份的趋势图</span>
            </div>
          </div>
        ) : (
          messages.map((msg, i) => (
            <MessageBubble
              key={`${msg.timestamp}-${i}`}
              message={msg}
              isLive={isLoading && i === messages.length - 1 && msg.role === 'assistant'}
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
              ? '输入你的数据分析问题，Enter 发送，Shift+Enter 换行'
              : '请先在左侧上传或选择数据源'
          }
          rows={1}
          disabled={isLoading}
        />
        {isLoading ? (
          <button type="button" onClick={abort} title="停止生成" className="stop-btn">
            <Square size={14} fill="currentColor" /> 停止
          </button>
        ) : (
          <button type="submit" disabled={!value.trim()}>
            <Send size={18} />
          </button>
        )}
      </form>
    </div>
  );
}

function MessageBubble({ message, isLive }: { message: ChatMessage; isLive: boolean }) {
  const isUser = message.role === 'user';
  const chartOption = isChartOption(message.chartData)
    ? (message.chartData as EChartsOption)
    : null;
  const hasChunks = Array.isArray(message.data_chunks) && message.data_chunks.length > 0;

  return (
    <div className={`message ${isUser ? 'user-message' : 'assistant-message'}`}>
      <div className="message-content">
        <p className={isLive ? 'live' : undefined}>
          {message.content || (isUser ? '' : '(empty reply)')}
          {isLive ? <span className="caret">▍</span> : null}
        </p>

        {message.sqlQuery ? (
          <>
            <div className="sql-label">
              <Terminal size={11} /> 执行的 SQL
            </div>
            <pre className="sql-block">
              <code>{message.sqlQuery}</code>
            </pre>
          </>
        ) : null}

        {hasChunks ? (
          <details className="data-chunks" open>
            <summary>
              <Table size={11} /> 行数据 ({message.data_chunks!.length} 行)
            </summary>
            <div className="data-chunks-table">
              <DataChunksTable rows={message.data_chunks!} />
            </div>
          </details>
        ) : null}

        {chartOption ? (
          <div className="chart-block">
            <Chart option={chartOption} height="100%" />
          </div>
        ) : message.chartData && !isChartOption(message.chartData) ? (
          <details style={{ marginTop: 8 }}>
            <summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--color-text-muted)' }}>
              查看 chart_data
            </summary>
            <pre className="sql-block">{safeStringify(message.chartData)}</pre>
          </details>
        ) : null}

        {!isUser ? (
          <div className="message-meta">
            <span>{formatTime(message.timestamp)}</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function DataChunksTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (rows.length === 0) return null;
  const columns = Object.keys(rows[0]);
  const preview = rows.slice(0, 50);
  return (
    <table className="chunks-table">
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {preview.map((row, i) => (
          <tr key={i}>
            {columns.map((c) => (
              <td key={c} title={String(row[c] ?? '')}>
                {row[c] === null || row[c] === undefined ? '—' : String(row[c])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
      {rows.length > preview.length ? (
        <tfoot>
          <tr>
            <td colSpan={columns.length}>…还有 {rows.length - preview.length} 行</td>
          </tr>
        </tfoot>
      ) : null}
    </table>
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
