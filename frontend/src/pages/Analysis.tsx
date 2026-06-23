import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  Database,
  Layers,
  ListTree,
  Sparkles,
  Table as TableIcon,
  X,
} from 'lucide-react';
import ChatWindow from '../components/Chat/ChatWindow';
import { useChatStore } from '../store/chatStore';
import { fetchRows, listTables, schemaDataSource, type RowsPage } from '../services/api';
import { useT } from '../hooks/useUi';

const PAGE_SIZE = 20;

interface TableMeta {
  name: string;
  row_count: number;
}
type SortDir = 'asc' | 'desc';

interface PageState {
  loading: boolean;
  data: RowsPage | null;
  error?: string;
}

export default function Analysis() {
  const t = useT();
  const activeId = useChatStore((s) => s.activeDataSourceId);
  const activeName = useChatStore((s) => s.activeDataSourceName);

  const [tables, setTables] = useState<TableMeta[]>([]);
  const [tablesLoading, setTablesLoading] = useState(false);
  const [activeTable, setActiveTable] = useState<string | null>(null);

  const [schema, setSchema] = useState<{ name: string; type: string }[]>([]);
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [pageState, setPageState] = useState<PageState>({ loading: false, data: null });

  // Load the table list + pick the primary table when the data source changes.
  useEffect(() => {
    if (!activeId) {
      setTables([]);
      setActiveTable(null);
      setSchema([]);
      setPageState({ loading: false, data: null });
      return;
    }
    let alive = true;
    setTablesLoading(true);
    listTables(activeId)
      .then((resp) => {
        if (!alive) return;
        setTables(resp.tables);
        setActiveTable(resp.tables[0]?.name ?? null);
      })
      .catch((e) => {
        if (!alive) return;
        const detail = axios.isAxiosError(e)
          ? e.response?.data?.detail || e.message
          : (e as Error).message;
        setPageState({ loading: false, data: null, error: detail });
      })
      .finally(() => alive && setTablesLoading(false));
    return () => {
      alive = false;
    };
  }, [activeId]);

  // Schema follows the active table — needed for the column panel + sort UX.
  useEffect(() => {
    if (!activeId || !activeTable) {
      setSchema([]);
      return;
    }
    let alive = true;
    schemaDataSource(activeId, activeTable ?? undefined)
      .then((s) => alive && setSchema(s.schema as { name: string; type: string }[]))
      .catch(() => alive && setSchema([]));
    return () => {
      alive = false;
    };
  }, [activeId, activeTable]);

  // Reset pagination/sort when the table changes.
  useEffect(() => {
    setPage(0);
    setSort(null);
    setSortDir('asc');
  }, [activeTable]);

  // Fetch the current page whenever inputs change.
  const loadPage = useCallback(async () => {
    if (!activeId || !activeTable) {
      setPageState({ loading: false, data: null });
      return;
    }
    setPageState((s) => ({ ...s, loading: true, error: undefined }));
    try {
      const data = await fetchRows(activeId, {
        table: activeTable,
        offset: page * PAGE_SIZE,
        limit: PAGE_SIZE,
        sort: sort ?? undefined,
        dir: sort ? sortDir : undefined,
      });
      setPageState({ loading: false, data });
    } catch (e) {
      const detail = axios.isAxiosError(e)
        ? e.response?.data?.detail || e.message
        : (e as Error).message;
      setPageState({ loading: false, data: null, error: detail });
    }
  }, [activeId, activeTable, page, sort, sortDir]);

  useEffect(() => {
    let alive = true;
    setPageState((s) => ({ ...s, loading: true }));
    loadPage().finally(() => {
      if (!alive) return;
    });
    return () => {
      alive = false;
    };
  }, [loadPage]);

  const totalRows = pageState.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalRows / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const columns = useMemo(() => {
    if (schema.length) return schema.map((c) => c.name);
    return pageState.data?.columns ?? [];
  }, [schema, pageState.data]);

  const toggleSort = (col: string) => {
    if (sort === col) {
      if (sortDir === 'asc') setSortDir('desc');
      else {
        // Second click on desc clears the sort.
        setSort(null);
        setSortDir('asc');
      }
    } else {
      setSort(col);
      setSortDir('asc');
    }
    setPage(0);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <div className="page-section" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Header
          name={activeName}
          tableCount={tables.length}
          rowCount={totalRows}
          schemaCount={columns.length}
        />

        {!activeId ? (
          <EmptyState />
        ) : tablesLoading ? (
          <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 16 }}>
            <div
              style={{
                background: 'var(--color-surface)',
                border: '1px solid var(--color-border)',
                borderRadius: 'var(--radius-md)',
                padding: 12,
              }}
            >
              {Array.from({ length: 6 }).map((_, i) => (
                <div
                  key={i}
                  className="skeleton-bar"
                  style={{ width: '80%', margin: '6px 0', height: 14 }}
                />
              ))}
            </div>
            <div
              style={{
                background: 'var(--color-surface)',
                border: '1px solid var(--color-border)',
                borderRadius: 'var(--radius-md)',
                padding: 12,
              }}
            >
              {Array.from({ length: 8 }).map((_, i) => (
                <div
                  key={i}
                  className="skeleton-bar"
                  style={{ width: `${60 + (i % 4) * 10}%`, margin: '6px 0', height: 12 }}
                />
              ))}
            </div>
          </div>
        ) : pageState.error && !activeTable ? (
          <div className="upload-error">{t('analysis.error', { err: pageState.error })}</div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 16 }}>
            <SchemaPanel schema={schema} rows={pageState.data?.rows ?? []} />
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 12,
                minHeight: 0,
              }}
            >
              {tables.length > 1 ? (
                <TableTabs tables={tables} active={activeTable} onSelect={setActiveTable} />
              ) : null}
              <DataTable
                columns={columns}
                rows={pageState.data?.rows ?? []}
                loading={pageState.loading}
                sort={sort}
                sortDir={sortDir}
                onToggleSort={toggleSort}
                onClearSort={() => {
                  setSort(null);
                  setSortDir('asc');
                }}
              />
              <PageFooter
                page={safePage}
                totalPages={totalPages}
                total={totalRows}
                pageSize={PAGE_SIZE}
                onPrev={() => setPage((p) => Math.max(0, p - 1))}
                onNext={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              />
            </div>
          </div>
        )}
      </div>

      <ChatWindow dataSourceId={activeId} />
    </div>
  );
}

function Header({
  name,
  tableCount,
  rowCount,
  schemaCount,
}: {
  name?: string;
  tableCount: number;
  rowCount: number;
  schemaCount: number;
}) {
  const t = useT();
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 16, flexWrap: 'wrap' }}>
      <h1 style={{ margin: 0, fontSize: 20, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Database size={18} /> {name || t('analysis.title')}
      </h1>
      {name ? (
        <div style={{ display: 'flex', gap: 12, color: 'var(--color-text-muted)', fontSize: 12 }}>
          {tableCount > 1 ? (
            <span>
              <TableIcon size={12} style={{ verticalAlign: -1, marginRight: 4 }} />
              {t('analysis.tables')}: {tableCount}
            </span>
          ) : null}
          <span>
            <Layers size={12} style={{ verticalAlign: -1, marginRight: 4 }} />
            {t('analysis.cols', { n: schemaCount })}
          </span>
          <span>{t('analysis.tableRows', { n: rowCount })}</span>
        </div>
      ) : null}
    </div>
  );
}

function EmptyState() {
  const t = useT();
  return (
    <div className="empty-state">
      <AlertTriangle size={28} />
      <h2>{t('analysis.emptyTitle')}</h2>
      <p>{t('analysis.emptyBody')}</p>
    </div>
  );
}

function TableTabs({
  tables,
  active,
  onSelect,
}: {
  tables: TableMeta[];
  active: string | null;
  onSelect: (name: string) => void;
}) {
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      {tables.map((tbl) => {
        const isActive = tbl.name === active;
        return (
          <button
            key={tbl.name}
            type="button"
            onClick={() => onSelect(tbl.name)}
            style={{
              padding: '4px 10px',
              fontSize: 12,
              borderRadius: 6,
              border: '1px solid var(--color-border)',
              background: isActive ? 'var(--color-primary)' : 'var(--color-surface)',
              color: isActive ? '#fff' : 'var(--color-text)',
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <TableIcon size={12} />
            {tbl.name}
            <span style={{ opacity: 0.7, fontSize: 11 }}>{tbl.row_count}</span>
          </button>
        );
      })}
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
  const t = useT();
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
        <ListTree size={14} /> {t('analysis.schema')}
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
                <span style={{ color: 'var(--color-danger)' }}>
                  {t('analysis.missing', { pct })}
                </span>
              ) : (
                <span style={{ color: 'var(--color-success)' }}>{t('analysis.complete')}</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DataTable({
  columns,
  rows,
  loading,
  sort,
  sortDir,
  onToggleSort,
  onClearSort,
}: {
  columns: string[];
  rows: Record<string, unknown>[];
  loading: boolean;
  sort: string | null;
  sortDir: SortDir;
  onToggleSort: (col: string) => void;
  onClearSort: () => void;
}) {
  const t = useT();
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
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontWeight: 600,
          justifyContent: 'space-between',
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <Sparkles size={14} /> {t('analysis.sortHint')}
        </span>
        {sort ? (
          <button
            type="button"
            onClick={onClearSort}
            style={{
              fontSize: 11,
              padding: '2px 6px',
              border: '1px solid var(--color-border)',
              borderRadius: 4,
              background: 'var(--color-bg)',
              color: 'var(--color-text-muted)',
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
            }}
          >
            <X size={11} />
            {t('analysis.clearSort')}
          </button>
        ) : null}
      </div>
      <div style={{ overflow: 'auto', maxHeight: 420 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {columns.map((c) => {
                const isSorted = sort === c;
                return (
                  <th
                    key={c}
                    onClick={() => onToggleSort(c)}
                    style={{
                      textAlign: 'left',
                      padding: '6px 8px',
                      borderBottom: '1px solid var(--color-border)',
                      background: 'var(--color-bg)',
                      color: isSorted ? 'var(--color-primary)' : 'var(--color-text-muted)',
                      fontWeight: 500,
                      whiteSpace: 'nowrap',
                      position: 'sticky',
                      top: 0,
                      cursor: 'pointer',
                      userSelect: 'none',
                    }}
                  >
                    {c}{' '}
                    {isSorted ? (
                      sortDir === 'asc' ? (
                        <ArrowUp size={11} style={{ verticalAlign: -1 }} />
                      ) : (
                        <ArrowDown size={11} style={{ verticalAlign: -1 }} />
                      )
                    ) : null}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              // Skeleton rows: 8 placeholder rows with shimmer bars. Gives
              // the user a visual anchor that data is loading rather than a
              // single "loading…" cell which looks like an empty table.
              Array.from({ length: 8 }).map((_, i) => (
                <tr key={`skeleton-${i}`}>
                  {columns.map((c, j) => (
                    <td
                      key={c || j}
                      style={{
                        padding: '6px 8px',
                        borderBottom: '1px solid var(--color-border)',
                      }}
                    >
                      <div
                        className="skeleton-bar"
                        style={{ width: `${60 + ((i + j) % 4) * 10}%` }}
                      />
                    </td>
                  ))}
                </tr>
              ))
            ) : rows.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length}
                  style={{ padding: 16, color: 'var(--color-text-muted)', textAlign: 'center' }}
                >
                  {t('common.emptyData')}
                </td>
              </tr>
            ) : (
              rows.map((row, i) => (
                <tr key={i}>
                  {columns.map((c) => (
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
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PageFooter({
  page,
  totalPages,
  total,
  pageSize,
  onPrev,
  onNext,
}: {
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  const t = useT();
  const from = total === 0 ? 0 : page * pageSize + 1;
  const to = Math.min((page + 1) * pageSize, total);
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '6px 10px',
        fontSize: 11,
        color: 'var(--color-text-muted)',
        background: 'var(--color-bg)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-md)',
      }}
    >
      <span>{t('analysis.pageRows', { from, to, total })}</span>
      <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
        <button
          type="button"
          className="page-btn"
          onClick={onPrev}
          disabled={page === 0}
          aria-label={t('pagination.prev')}
          title={t('pagination.prev')}
        >
          <ChevronLeft size={12} />
        </button>
        <span style={{ alignSelf: 'center' }}>
          {page + 1} / {totalPages}
        </span>
        <button
          type="button"
          className="page-btn"
          onClick={onNext}
          disabled={page >= totalPages - 1}
          aria-label={t('pagination.next')}
          title={t('pagination.next')}
        >
          <ChevronRight size={12} />
        </button>
      </span>
    </div>
  );
}
