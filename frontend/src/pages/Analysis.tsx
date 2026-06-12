import { useEffect, useState } from 'react';
import axios from 'axios';
import { Database, Layers, ListTree, AlertTriangle, Sparkles } from 'lucide-react';
import ChatWindow from '../components/Chat/ChatWindow';
import { useChatStore } from '../store/chatStore';
import { previewDataSource, schemaDataSource } from '../services/api';

interface PreviewState {
  loading: boolean;
  rows: Record<string, unknown>[];
  schema: { name: string; type: string }[];
  error?: string;
}

export default function Analysis() {
  const activeId = useChatStore((s) => s.activeDataSourceId);
  const activeName = useChatStore((s) => s.activeDataSourceName);
  const [preview, setPreview] = useState<PreviewState | null>(null);

  useEffect(() => {
    if (!activeId) {
      setPreview(null);
      return;
    }
    let alive = true;
    setPreview({ loading: true, rows: [], schema: [] });
    Promise.all([previewDataSource(activeId, 50), schemaDataSource(activeId)])
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
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <div className="page-section" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Header
          name={activeName}
          rowCount={preview?.rows.length ?? 0}
          schemaCount={preview?.schema.length ?? 0}
        />

        {!activeId ? (
          <EmptyState />
        ) : preview?.loading ? (
          <div style={{ color: 'var(--color-text-muted)' }}>加载中…</div>
        ) : preview?.error ? (
          <div className="upload-error">加载数据失败：{preview.error}</div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 16 }}>
            <SchemaPanel schema={preview?.schema ?? []} rows={preview?.rows ?? []} />
            <DataTable rows={preview?.rows ?? []} schema={preview?.schema ?? []} />
          </div>
        )}
      </div>

      <ChatWindow dataSourceId={activeId} />
    </div>
  );
}

function Header({
  name,
  rowCount,
  schemaCount,
}: {
  name: string;
  rowCount: number;
  schemaCount: number;
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 16, flexWrap: 'wrap' }}>
      <h1 style={{ margin: 0, fontSize: 20, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Database size={18} /> {name || '请选择数据源'}
      </h1>
      {name ? (
        <div style={{ display: 'flex', gap: 12, color: 'var(--color-text-muted)', fontSize: 12 }}>
          <span>
            <Layers size={12} style={{ verticalAlign: -1, marginRight: 4 }} />
            {schemaCount} 列
          </span>
          <span>预览 {rowCount} 行</span>
        </div>
      ) : null}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="empty-state">
      <AlertTriangle size={28} />
      <h2>还没有数据源</h2>
      <p>请到 Home 页面先上传一个 CSV/Excel 文件，然后回到这里查看详细分析。</p>
    </div>
  );
}

function SchemaPanel({
  schema,
  rows,
}: {
  schema: { name: string; type: string }[];
  rows: Record<string, unknown>[];
}) {
  const nullCount = (col: string) =>
    rows.reduce((n, r) => (r[col] === null || r[col] === undefined ? n + 1 : n), 0);

  return (
    <div
      style={{
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-md)',
        padding: 12,
      }}
    >
      <div
        style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8, fontWeight: 600 }}
      >
        <ListTree size={14} /> 字段概览
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {schema.map((col) => {
          const nc = nullCount(col.name);
          const pct = rows.length ? Math.round((nc / rows.length) * 100) : 0;
          return (
            <div
              key={col.name}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '6px 8px',
                borderRadius: 6,
                background: 'var(--color-bg)',
                fontSize: 12,
              }}
            >
              <span style={{ flex: 1, fontWeight: 500 }}>{col.name}</span>
              <span style={{ color: 'var(--color-text-muted)' }}>{col.type}</span>
              {pct > 0 ? (
                <span style={{ color: 'var(--color-danger)' }}>缺失 {pct}%</span>
              ) : (
                <span style={{ color: 'var(--color-success)' }}>完整</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DataTable({
  rows,
  schema,
}: {
  rows: Record<string, unknown>[];
  schema: { name: string; type: string }[];
}) {
  return (
    <div
      style={{
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-md)',
        padding: 12,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
        <Sparkles size={14} /> 数据预览（前 {rows.length} 行）
      </div>
      <div style={{ overflow: 'auto', maxHeight: 360 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {schema.map((col) => (
                <th
                  key={col.name}
                  style={{
                    textAlign: 'left',
                    padding: '6px 8px',
                    borderBottom: '1px solid var(--color-border)',
                    background: 'var(--color-bg)',
                    color: 'var(--color-text-muted)',
                    fontWeight: 500,
                    position: 'sticky',
                    top: 0,
                  }}
                >
                  {col.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {schema.map((col) => (
                  <td
                    key={col.name}
                    style={{
                      padding: '6px 8px',
                      borderBottom: '1px solid var(--color-border)',
                      maxWidth: 240,
                      whiteSpace: 'nowrap',
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
    </div>
  );
}
