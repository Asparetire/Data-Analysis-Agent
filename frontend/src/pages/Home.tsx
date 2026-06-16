import { useEffect, useState, type ReactNode } from 'react';
import axios from 'axios';
import { BarChart3, LineChart, PieChart, Sparkles, Table, CheckCircle2 } from 'lucide-react';
import ChatWindow from '../components/Chat/ChatWindow';
import FileUpload from '../components/Upload';
import { useChatStore } from '../store/chatStore';
import { previewDataSource, schemaDataSource } from '../services/api';
import { useT } from '../hooks/useUi';

interface PreviewState {
  loading: boolean;
  rows: Record<string, unknown>[];
  schema: { name: string; type: string }[];
  error?: string;
}

export default function Home() {
  const t = useT();
  const activeId = useChatStore((s) => s.activeDataSourceId);
  const activeName = useChatStore((s) => s.activeDataSourceName);
  const setPage = useChatStore((s) => s.setPage);
  const uploadedFileName = useChatStore((s) => s.uploadedFileName);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [justUploaded, setJustUploaded] = useState<string | null>(null);

  const SUGGESTIONS: { icon: ReactNode; text: string }[] = [
    { icon: <BarChart3 size={14} />, text: t('home.suggestion.bar') },
    { icon: <LineChart size={14} />, text: t('home.suggestion.trend') },
    { icon: <PieChart size={14} />, text: t('home.suggestion.top5') },
    { icon: <Sparkles size={14} />, text: t('home.suggestion.overview') },
  ];

  // Fire a one-shot banner when the file name flips. The banner shows the
  // new filename + a "go to analysis" shortcut. The user dismissing the
  // banner (or any data-source switch) clears it.
  useEffect(() => {
    if (uploadedFileName) {
      setJustUploaded(uploadedFileName);
      const timer = setTimeout(() => setJustUploaded(null), 5000);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [uploadedFileName]);

  useEffect(() => {
    if (activeId) setJustUploaded(null);
  }, [activeId]);

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
          {justUploaded ? (
            <div
              role="status"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                background: 'var(--color-success)',
                color: 'white',
                padding: '8px 14px',
                borderRadius: 'var(--radius-md)',
                fontSize: 13,
                marginBottom: 12,
                boxShadow: 'var(--shadow-sm)',
              }}
            >
              <CheckCircle2 size={14} />
              <span style={{ flex: 1 }}>{t('upload.uploadedToast', { name: justUploaded })}</span>
              <button
                type="button"
                onClick={() => setPage('analysis')}
                style={{
                  background: 'rgba(255,255,255,0.18)',
                  border: 'none',
                  color: 'white',
                  padding: '4px 10px',
                  borderRadius: 6,
                  fontSize: 12,
                  cursor: 'pointer',
                }}
              >
                {t('upload.goToAnalysis')}
              </button>
            </div>
          ) : null}
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
          <h2>{t('common.uploadPrompt')}</h2>
          <p>{t('common.uploadPromptBody')}</p>
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
  const t = useT();
  if (!preview) return null;
  if (preview.loading) {
    return (
      <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>{t('common.preview')}</div>
    );
  }
  if (preview.error) {
    return (
      <div style={{ color: 'var(--color-danger)', fontSize: 13 }}>
        {t('common.previewFailed', { err: preview.error })}
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
          <Table size={14} /> {name} · {t('analysis.previewRows', { n: preview.rows.length })}
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
          {t('upload.goToAnalysis')}
        </button>
      </div>
      {preview.rows.length === 0 ? (
        <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
          {t('common.emptyData')}
        </div>
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
