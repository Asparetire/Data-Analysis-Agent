import { useEffect, useState, type KeyboardEvent } from 'react';
import {
  Database,
  FileText,
  FileSpreadsheet,
  CheckCircle2,
  Pencil,
  Trash2,
  X,
  Check,
  AlertTriangle,
} from 'lucide-react';
import { useChatStore } from '../../store/chatStore';
import { getDataSources, renameDataSource } from '../../services/api';
import { useT } from '../../hooks/useUi';

const TYPE_LABEL: Record<string, string> = {
  csv: 'CSV',
  excel: 'XLSX',
  json: 'JSON',
};

function TypeIcon({ type }: { type: string }) {
  if (type === 'csv' || type === 'json') return <FileText size={16} />;
  if (type === 'excel') return <FileSpreadsheet size={16} />;
  return <FileText size={16} />;
}

interface SwitchPrompt {
  target: { id: string; name: string };
  /** True if the current session is already bound to a different data source. */
  boundToCurrent: boolean;
}

interface SidebarProps {
  /** Mobile: when true, render in a slide-in drawer. */
  drawerOpen?: boolean;
  /** Mobile: called when the user dismisses the drawer. */
  onClose?: () => void;
}

export default function Sidebar({ drawerOpen = false, onClose }: SidebarProps) {
  const t = useT();
  const dataSources = useChatStore((s) => s.dataSources);
  const setDataSources = useChatStore((s) => s.setDataSources);
  const activeId = useChatStore((s) => s.activeDataSourceId);
  const activeName = useChatStore((s) => s.activeDataSourceName);
  const setActive = useChatStore((s) => s.setActiveDataSource);
  const uploadedFileName = useChatStore((s) => s.uploadedFileName);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingValue, setEditingValue] = useState('');
  const [pendingSwitch, setPendingSwitch] = useState<SwitchPrompt | null>(null);

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const list = await getDataSources();
        if (alive) setDataSources(list);
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn('failed to load data sources', e);
      }
    };
    refresh();
    return () => {
      alive = false;
    };
  }, [setDataSources, uploadedFileName]);

  const requestSwitch = (target: { id: string; name: string }) => {
    if (target.id === activeId) return;
    if (!activeId) {
      // No current data source: nothing to confirm.
      setActive(target);
      return;
    }
    setPendingSwitch({ target, boundToCurrent: true });
  };

  const confirmSwitch = () => {
    if (!pendingSwitch) return;
    setActive(pendingSwitch.target);
    setPendingSwitch(null);
    onClose?.();
  };

  const startEdit = (ds: { id: string; name: string }) => {
    setEditingId(ds.id);
    setEditingValue(ds.name);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditingValue('');
  };

  const saveEdit = async () => {
    if (!editingId) return;
    const next = editingValue.trim();
    if (!next) {
      cancelEdit();
      return;
    }
    const previous = dataSources.find((d) => d.id === editingId);
    // Optimistic local update so the UI feels instant.
    setDataSources(dataSources.map((d) => (d.id === editingId ? { ...d, name: next } : d)));
    if (activeId === editingId) {
      setActive({ id: editingId, name: next });
    }
    setEditingId(null);
    setEditingValue('');
    try {
      const saved = await renameDataSource(editingId, next);
      setDataSources(dataSources.map((d) => (d.id === editingId ? saved : d)));
      if (activeId === editingId) {
        setActive({ id: saved.id, name: saved.name });
      }
    } catch (e) {
      // Roll back on failure.
      if (previous) {
        setDataSources(dataSources.map((d) => (d.id === editingId ? previous : d)));
        if (activeId === editingId) {
          setActive({ id: previous.id, name: previous.name });
        }
      }
      // eslint-disable-next-line no-console
      console.warn('rename failed', e);
    }
  };

  const onEditKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      void saveEdit();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cancelEdit();
    }
  };

  return (
    <aside className={`sidebar ${drawerOpen ? 'open' : ''}`}>
      <h2>{t('sidebar.sources')}</h2>
      {activeId && activeName ? (
        <div className="current-datasource">
          <span className="ds-icon">
            <Database size={16} />
          </span>
          <div className="ds-meta">
            <div className="ds-name" title={activeName}>
              {activeName}
            </div>
            <div className="ds-sub">
              <CheckCircle2 size={11} style={{ marginRight: 4, verticalAlign: -1 }} />
              {t('sidebar.active')}
            </div>
          </div>
        </div>
      ) : (
        <div className="current-datasource" style={{ background: 'var(--color-bg)' }}>
          <span className="ds-icon" style={{ background: 'var(--color-text-muted)' }}>
            <Database size={16} />
          </span>
          <div className="ds-meta">
            <div className="ds-name" style={{ color: 'var(--color-text-muted)' }}>
              {t('sidebar.none')}
            </div>
            <div className="ds-sub">{t('sidebar.uploadFirst')}</div>
          </div>
        </div>
      )}

      <div>
        <h3>{t('sidebar.sources')}</h3>
        {dataSources.length === 0 ? (
          <p style={{ fontSize: 12, color: 'var(--color-text-muted)', margin: 0 }}>
            {t('sidebar.historyEmpty')}
          </p>
        ) : (
          <div className="datasource-list">
            {dataSources.map((ds) => {
              const isEditing = editingId === ds.id;
              return (
                <div
                  key={ds.id}
                  className={`datasource-item ${activeId === ds.id ? 'active' : ''}`}
                >
                  {isEditing ? (
                    <>
                      <TypeIcon type={ds.type} />
                      <input
                        className="ds-rename-input"
                        autoFocus
                        value={editingValue}
                        onChange={(e) => setEditingValue(e.target.value)}
                        onKeyDown={onEditKeyDown}
                        onBlur={() => void saveEdit()}
                        maxLength={200}
                      />
                      <button
                        type="button"
                        className="ds-row-action"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => void saveEdit()}
                        title={t('sidebar.save')}
                      >
                        <Check size={12} />
                      </button>
                      <button
                        type="button"
                        className="ds-row-action"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={cancelEdit}
                        title={t('sidebar.cancel')}
                      >
                        <X size={12} />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="datasource-item-main"
                        onClick={() => requestSwitch({ id: ds.id, name: ds.name })}
                        title={ds.name}
                      >
                        <TypeIcon type={ds.type} />
                        <span className="ds-name">{ds.name}</span>
                        <span className="ds-type">{TYPE_LABEL[ds.type] || ds.type}</span>
                      </button>
                      <button
                        type="button"
                        className="ds-row-action"
                        onClick={() => startEdit(ds)}
                        title={t('sidebar.rename')}
                      >
                        <Pencil size={11} />
                      </button>
                      <DeleteButton id={ds.id} name={ds.name} />
                    </>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {pendingSwitch ? (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal">
            <div className="modal-title">
              <AlertTriangle size={16} color="var(--color-danger)" />
              {t('sidebar.switchTitle')}
            </div>
            <div className="modal-body">
              {t('sidebar.switchBody', { current: activeName, target: pendingSwitch.target.name })}
            </div>
            <div className="modal-actions">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setPendingSwitch(null)}
              >
                {t('sidebar.cancel')}
              </button>
              <button type="button" className="btn-primary" onClick={confirmSwitch}>
                {t('sidebar.confirmSwitch')}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </aside>
  );
}

function DeleteButton({ id, name }: { id: string; name: string }) {
  const t = useT();
  const dataSources = useChatStore((s) => s.dataSources);
  const setDataSources = useChatStore((s) => s.setDataSources);
  const activeId = useChatStore((s) => s.activeDataSourceId);
  const setActive = useChatStore((s) => s.setActiveDataSource);
  const [busy, setBusy] = useState(false);
  const onClick = async () => {
    if (busy) return;
    if (!window.confirm(t('sidebar.deleteConfirm', { name }))) {
      return;
    }
    setBusy(true);
    try {
      const { deleteDataSource } = await import('../../services/api');
      await deleteDataSource(id);
      setDataSources(dataSources.filter((d) => d.id !== id));
      if (activeId === id) {
        setActive(undefined);
      }
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('delete failed', e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      type="button"
      className="ds-row-action ds-row-action-danger"
      onClick={onClick}
      disabled={busy}
      title={t('sidebar.delete')}
    >
      <Trash2 size={11} />
    </button>
  );
}
