import { useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useT } from '../hooks/useUi';

interface PaginatedTableProps {
  rows: Record<string, unknown>[];
  pageSize?: number;
  maxHeight?: number | string;
  /** Fixed column order -- if omitted, columns are derived from the first row. */
  columns?: string[];
  emptyText?: string;
  className?: string;
}

/**
 * A scrollable, paginated table for tabular data. Used by both the in-chat
 * row dump (data_chunks) and the Analysis page's preview.
 *
 * Why a shared component: pagination rules + the "X of N rows" footer were
 * copy-pasted in two places. Centralizing the logic here means we can change
 * the page size or rendering once.
 */
export default function PaginatedTable({
  rows,
  pageSize = 20,
  maxHeight = 360,
  columns,
  emptyText,
  className,
}: PaginatedTableProps) {
  const t = useT();
  const fallbackEmpty = emptyText ?? t('common.emptyData');
  const [page, setPage] = useState(0);

  const cols = useMemo(() => {
    if (columns) return columns;
    if (rows.length === 0) return [] as string[];
    return Object.keys(rows[0]);
  }, [columns, rows]);

  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
  // Clamp the page index in case the row count shrinks underneath us.
  const safePage = Math.min(page, totalPages - 1);
  const start = safePage * pageSize;
  const slice = rows.slice(start, start + pageSize);

  if (rows.length === 0) {
    return (
      <div
        style={{
          color: 'var(--color-text-muted)',
          fontSize: 13,
          padding: '12px 0',
        }}
      >
        {fallbackEmpty}
      </div>
    );
  }

  return (
    <div
      className={className}
      style={{
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-md)',
        overflow: 'hidden',
      }}
    >
      <div style={{ overflow: 'auto', maxHeight }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {cols.map((c) => (
                <th
                  key={c}
                  style={{
                    textAlign: 'left',
                    padding: '6px 8px',
                    borderBottom: '1px solid var(--color-border)',
                    background: 'var(--color-bg)',
                    color: 'var(--color-text-muted)',
                    fontWeight: 500,
                    whiteSpace: 'nowrap',
                    position: 'sticky',
                    top: 0,
                  }}
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {slice.map((row, i) => (
              <tr key={start + i}>
                {cols.map((c) => (
                  <td
                    key={c}
                    title={String(row[c] ?? '')}
                    style={{
                      padding: '6px 8px',
                      borderBottom: '1px solid var(--color-border)',
                      whiteSpace: 'nowrap',
                      maxWidth: 240,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {row[c] === null || row[c] === undefined ? (
                      <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                    ) : (
                      String(row[c])
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '6px 10px',
          fontSize: 11,
          color: 'var(--color-text-muted)',
          background: 'var(--color-bg)',
          borderTop: '1px solid var(--color-border)',
        }}
      >
        <span>
          {t('pagination.range', {
            from: start + 1,
            to: Math.min(start + pageSize, rows.length),
            total: rows.length,
          })}
        </span>
        <span style={{ display: 'inline-flex', gap: 4 }}>
          <button
            type="button"
            className="page-btn"
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={safePage === 0}
            aria-label={t('pagination.prev')}
            title={t('pagination.prev')}
          >
            <ChevronLeft size={12} />
          </button>
          <span style={{ alignSelf: 'center' }}>
            {safePage + 1} / {totalPages}
          </span>
          <button
            type="button"
            className="page-btn"
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={safePage >= totalPages - 1}
            aria-label={t('pagination.next')}
            title={t('pagination.next')}
          >
            <ChevronRight size={12} />
          </button>
        </span>
      </div>
    </div>
  );
}
