import { useEffect, useState, type ReactNode } from 'react';
import axios from 'axios';
import { BarChart3, LineChart, PieChart, Sparkles, Table } from 'lucide-react';
import ChatWindow from '../components/Chat/ChatWindow';
import FileUpload from '../components/Upload';
import { useChatStore } from '../store/chatStore';
import { previewDataSource, schemaDataSource } from '../services/api';

interface PreviewState {
  loading: boolean;
  rows: Record<string, unknown>[];
  schema: { name: string; type: string }[];
  error?: string;
}

const SUGGESTIONS = [
  { icon: <BarChart3 size={14} />, text: '统计每个分类的数量并画柱状图' },
  { icon: <LineChart size={14} />, text: '按时间字段做一个趋势折线图' },
  { icon: <PieChart size={14} />, text: '占比前 5 的项目画成饼图' },
  { icon: <Sparkles size={14} />, text: '给我一个简要的数据概览（行数、列、缺失值）' },
];

export default function Home() {
  const activeId = useChatStore((s) => s.activeDataSourceId);
  const activeName = useChatStore((s) => s.activeDataSourceName);
  const setPage = useChatStore((s) => s.setPage);
  const [preview, setPreview] = useState<PreviewState | null>(null);

  useEffect(() => {
    if (!activeId) {
      setPreview(null);
      return;
    }
    let alive = true;
    setPreview({ loading: true, rows: [], schema: [] });
    Promise.all([previewDataSource(activeId, 8), schemaDataSource(activeId)])
      .then(([p, s]) => {
        if (!alive) return;
        setPreview({
          loading: false,
          rows: p.rows as Record<string, unknown>[],
          schema: s.schema as { name: string; type: string }[],
        });
      })
      .catch((e) => {
        if (!alive) return;
        const detail = axios.isAxiosError(e)
          ? e.response?.data?.detail || e.message
          : (e as Error).message;
        setPreview({ loading: false, rows: [], schema: [], error: detail });
      });
    return () => {
      alive = false;
    };
  }, [activeId]);

  return (
    <div
      className="page-section"
      style={{ display: 'flex', flexDirection: 'column', gap: 16, padding: 0 }}
    >
      {activeId ? (
        <div style={{ padding: '16px 24px 0 24px' }}>
          <DataPreview
            name={activeName}
            preview={preview}
            onOpenAnalysis={() => setPage('analysis')}
          />
          <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {SUGGESTIONS.map((s) => (
              <SuggestionChip key={s.text} icon={s.icon} text={s.text} />
            ))}
          </div>
        </div>
      ) : (
        <div className="empty-state" style={{ flex: 1, padding: 32 }}>
          <h2>上传你的数据</h2>
          <p>
            支持 CSV / Excel / JSON 文件，文件会被解析到独立的 SQLite 数据库，
            你可以用自然语言提出问题。
          </p>
          <div style={{ width: '100%', maxWidth: 480 }}>
            <FileUpload />
          </div>
        </div>
      )}

      <ChatWindow dataSourceId={activeId} />
    </div>
  );
}

function SuggestionChip({ icon, text }: { icon: ReactNode; text: string }) {
  // We dispatch a custom event so the ChatWindow can pick it up and send the prompt.
  return (
    <button
      type="button"
      onClick={() => {
        window.dispatchEvent(new CustomEvent('chat:suggest', { detail: text }));
      }}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        color: 'var(--color-text)',
        padding: '6px 12px',
        borderRadius: 999,
        fontSize: 12,
      }}
    >
      {icon}
      {text}
    </button>
  );
}

function DataPreview({
  name,
  preview,
  onOpenAnalysis,
}: {
  name: string;
  preview: PreviewState | null;
  onOpenAnalysis: () => void;
}) {
  if (!preview) return null;
  if (preview.loading) {
    return <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>正在加载数据预览…</div>;
  }
  if (preview.error) {
    return (
      <div style={{ color: 'var(--color-danger)', fontSize: 13 }}>
        加载预览失败：{preview.error}
      </div>
    );
  }
  return (
    <div
      style={{
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-md)',
        padding: 12,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
          <Table size={14} /> {name} · 前 {preview.rows.length} 行
        </div>
        <button
          type="button"
          onClick={onOpenAnalysis}
          style={{
            background: 'transparent',
            border: '1px solid var(--color-border)',
            color: 'var(--color-text-muted)',
            padding: '4px 10px',
            borderRadius: 6,
            fontSize: 12,
          }}
        >
          打开分析页
        </button>
      </div>
      {preview.rows.length === 0 ? (
        <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>数据为空</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr>
                {preview.schema.map((col) => (
                  <th
                    key={col.name}
                    style={{
                      textAlign: 'left',
                      padding: '6px 8px',
                      borderBottom: '1px solid var(--color-border)',
                      background: 'var(--color-bg)',
                      color: 'var(--color-text-muted)',
                      fontWeight: 500,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {col.name}
                    <span style={{ marginLeft: 6, fontSize: 10, color: 'var(--color-text-muted)' }}>
                      {col.type}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {preview.rows.map((row, i) => (
                <tr key={i}>
                  {preview.schema.map((col) => (
                    <td
                      key={col.name}
                      style={{
                        padding: '6px 8px',
                        borderBottom: '1px solid var(--color-border)',
                        whiteSpace: 'nowrap',
                        maxWidth: 220,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                      title={String(row[col.name] ?? '')}
                    >
                      {row[col.name] === null || row[col.name] === undefined ? (
                        <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                      ) : (
                        String(row[col.name])
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
