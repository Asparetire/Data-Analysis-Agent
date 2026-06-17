import { useCallback, useEffect, useState } from 'react';
import { X, RefreshCw, History, AlertTriangle, CheckCircle2, Clock } from 'lucide-react';
import { getDataSourceLineage } from '../../services/api';
import type { LineageEntry } from '../../types';
import { useT } from '../../hooks/useUi';

interface LineagePanelProps {
  dataSourceId: string;
  dataSourceName: string;
  onClose: () => void;
}

export default function LineagePanel({ dataSourceId, dataSourceName, onClose }: LineagePanelProps) {
  const t = useT();
  const [entries, setEntries] = useState<LineageEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getDataSourceLineage(dataSourceId, showAll ? 200 : 20);
      setEntries(res.entries);
      setTotal(res.total);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('lineage fetch failed', e);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [dataSourceId, showAll]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Close on ESC.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal lineage-modal">
        <div className="modal-title">
          <History size={16} />
          <span style={{ flex: 1 }}>
            {t('lineage.title')}
            <span
              style={{
                marginLeft: 8,
                fontSize: 12,
                color: 'var(--color-text-muted)',
                fontWeight: 400,
              }}
            >
              {dataSourceName}
            </span>
          </span>
          <button
            type="button"
            className="ds-row-action"
            onClick={() => void refresh()}
            title={t('lineage.refresh')}
            disabled={loading}
          >
            <RefreshCw size={12} className={loading ? 'spin' : ''} />
          </button>
          <button
            type="button"
            className="ds-row-action"
            onClick={onClose}
            title={t('lineage.close')}
          >
            <X size={12} />
          </button>
        </div>
        <div className="modal-body lineage-subtitle">{t('lineage.subtitle')}</div>

        {error ? (
          <div className="lineage-error">
            <AlertTriangle size={12} /> {error}
          </div>
        ) : null}

        <div className="lineage-list">
          {loading && entries.length === 0 ? (
            <div className="lineage-empty">{t('analysis.loading')}</div>
          ) : entries.length === 0 ? (
            <div className="lineage-empty">{t('lineage.empty')}</div>
          ) : (
            entries.map((e, i) => <LineageRow key={`${e.ts}-${i}`} entry={e} />)
          )}
        </div>

        {total > entries.length ? (
          <div className="lineage-footer">
            <span className="lineage-total">{t('lineage.total', { n: total })}</span>
            {!showAll ? (
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setShowAll(true)}
                disabled={loading}
              >
                {t('lineage.showMore')}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function LineageRow({ entry }: { entry: LineageEntry }) {
  const t = useT();
  const ts = new Date(entry.ts * 1000);
  const time = `${ts.getHours().toString().padStart(2, '0')}:${ts
    .getMinutes()
    .toString()
    .padStart(2, '0')}:${ts.getSeconds().toString().padStart(2, '0')}`;
  return (
    <div className={`lineage-row ${entry.ok ? '' : 'failed'}`}>
      <div className="lineage-row-head">
        {entry.ok ? (
          <CheckCircle2 size={11} className="ok-icon" />
        ) : (
          <AlertTriangle size={11} className="fail-icon" />
        )}
        <span className="lineage-time">
          <Clock size={10} /> {time}
        </span>
        {entry.cache_hit ? (
          <span className="lineage-badge hit">{t('lineage.cacheHit')}</span>
        ) : null}
        {!entry.ok ? <span className="lineage-badge fail">{t('lineage.failed')}</span> : null}
        <span className="lineage-stats">
          {t('lineage.rowCount', { n: entry.row_count })} ·{' '}
          {t('lineage.duration', { ms: entry.duration_ms.toFixed(1) })}
        </span>
      </div>
      <pre className="lineage-sql">{entry.sql}</pre>
      {entry.tables.length > 0 ? (
        <div className="lineage-tables">
          {t('lineage.tables', { tables: entry.tables.join(', ') })}
        </div>
      ) : null}
      {entry.error ? (
        <div className="lineage-row-error">
          {t('lineage.errorLabel')}: {entry.error}
        </div>
      ) : null}
    </div>
  );
}
