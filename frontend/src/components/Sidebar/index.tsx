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
  History,
  Plus,
  MessagesSquare,
} from 'lucide-react';
import { useChatStore } from '../../store/chatStore';
import { getDataSources, renameDataSource } from '../../services/api';
import { useT } from '../../hooks/useUi';
import LineagePanel from '../LineagePanel';

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

type Tab = 'sources' | 'sessions';

function sessionTitle(text: string | undefined | null): string {
  if (!text) return '';
  const firstLine = text.split('\n')[0].trim();
  return firstLine.length > 30 ? `${firstLine.slice(0, 30)}…` : firstLine;
}

export default function Sidebar({ drawerOpen = false, onClose }: SidebarProps) {
  const t = useT();
  const dataSources = useChatStore((s) => s.dataSources);
  const setDataSources = useChatStore((s) => s.setDataSources);
  const activeId = useChatStore((s) => s.activeDataSourceId);
  const activeName = useChatStore((s) => s.activeDataSourceName);
  const setActive = useChatStore((s) => s.setActiveDataSource);
  const boundIds = useChatStore((s) => s.boundDataSourceIds);
  const setBoundIds = useChatStore((s) => s.setBoundDataSourceIds);
  const uploadedFileName = useChatStore((s) => s.uploadedFileName);

  const sessions = useChatStore((s) => s.sessions);
  const loadSessions = useChatStore((s) => s.loadSessions);
  const newChat = useChatStore((s) => s.newChat);
  const switchSession = useChatStore((s) => s.switchSession);
  const removeSession = useChatStore((s) => s.removeSession);
  const currentSessionId = useChatStore((s) => s.sessionId);

  const [tab, setTab] = useState<Tab>('sources');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingValue, setEditingValue] = useState('');
  const [pendingSwitch, setPendingSwitch] = useState<SwitchPrompt | null>(null);
  const [lineageFor, setLineageFor] = useState<{ id: string; name: string } | null>(null);
  const [busyNewChat, setBusyNewChat] = useState(false);

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

  // Load the session list whenever the user opens the sessions tab so TTL'd
  // sessions get pruned server-side and new ones show up without a manual
  // refresh.
  useEffect(() => {
    if (tab === 'sessions') {
      void loadSessions();
    }
  }, [tab, loadSessions]);

  const onNewChat = async () => {
    if (busyNewChat) return;
    setBusyNewChat(true);
    try {
      await newChat();
      onClose?.();
    } finally {
      setBusyNewChat(false);
    }
  };

  const onSwitchSession = async (id: string) => {
    if (id === currentSessionId) {
      onClose?.();
      return;
    }
    await switchSession(id);
    onClose?.();
  };

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

  /**
   * Phase 3C: toggle an auxiliary binding. Primary stays put; clicking
   * adds/removes the id from `boundDataSourceIds` (which is what the chat
   * request sends). The single-source UX (no checkbox) is unchanged.
   */
  const toggleAttach = (id: string) => {
    if (id === activeId) return;
    if (boundIds.includes(id)) {
      setBoundIds(boundIds.filter((x) => x !== id));
    } else {
      setBoundIds([...boundIds, id]);
    }
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
      <button type="button" className="new-chat-btn" onClick={onNewChat} disabled={busyNewChat}>
        <Plus size={14} /> {t('sidebar.newChat')}
      </button>

      <div className="sidebar-tabs">
        <button
          type="button"
          className={tab === 'sources' ? 'active' : ''}
          onClick={() => setTab('sources')}
        >
          <Database size={12} /> {t('sidebar.sources')}
        </button>
        <button
          type="button"
          className={tab === 'sessions' ? 'active' : ''}
          onClick={() => setTab('sessions')}
        >
          <MessagesSquare size={12} /> {t('sidebar.sessions')}
        </button>
      </div>

      {tab === 'sources' ? (
        <>
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
                  {boundIds.length > 1 ? (
                    <span
                      className="bound-badge"
                      title={boundIds
                        .map((id) => dataSources.find((d) => d.id === id)?.name || id)
                        .join(' / ')}
                    >
                      {t('sidebar.attachedCount', { n: boundIds.length })}
                    </span>
                  ) : null}
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

          <div className="sidebar-section">
            <h3>{t('sidebar.sources')}</h3>
            {dataSources.length === 0 ? (
              <p className="sidebar-empty">{t('sidebar.historyEmpty')}</p>
            ) : (
              <div className="datasource-list">
                {dataSources.map((ds) => {
                  const isEditing = editingId === ds.id;
                  const isActive = activeId === ds.id;
                  const isAttached = !isActive && boundIds.includes(ds.id);
                  return (
                    <div
                      key={ds.id}
                      className={`datasource-item ${isActive ? 'active' : ''} ${isAttached ? 'attached' : ''}`}
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
                          {!isActive ? (
                            <button
                              type="button"
                              className={`ds-row-action ${isAttached ? 'attached-mark' : ''}`}
                              onClick={() => toggleAttach(ds.id)}
                              title={isAttached ? t('sidebar.detach') : t('sidebar.attach')}
                              aria-pressed={isAttached}
                            >
                              <CheckCircle2 size={11} />
                            </button>
                          ) : (
                            <span className="ds-row-action" title={t('sidebar.primary')}>
                              <CheckCircle2 size={11} />
                            </span>
                          )}
                          <button
                            type="button"
                            className="ds-row-action"
                            onClick={() => startEdit(ds)}
                            title={t('sidebar.rename')}
                          >
                            <Pencil size={11} />
                          </button>
                          <button
                            type="button"
                            className="ds-row-action"
                            onClick={() => setLineageFor({ id: ds.id, name: ds.name })}
                            title={t('lineage.open')}
                          >
                            <History size={11} />
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
        </>
      ) : (
        <SessionList
          sessions={sessions}
          currentId={currentSessionId}
          dataSources={dataSources}
          onSwitch={onSwitchSession}
          onDelete={removeSession}
          onRefresh={loadSessions}
        />
      )}

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

      {lineageFor ? (
        <LineagePanel
          dataSourceId={lineageFor.id}
          dataSourceName={lineageFor.name}
          onClose={() => setLineageFor(null)}
        />
      ) : null}
    </aside>
  );
}

function SessionList({
  sessions,
  currentId,
  dataSources,
  onSwitch,
  onDelete,
  onRefresh,
}: {
  sessions: ReturnType<typeof useChatStore.getState>['sessions'];
  currentId: string | null;
  dataSources: ReturnType<typeof useChatStore.getState>['dataSources'];
  onSwitch: (id: string) => void;
  onDelete: (id: string) => Promise<void>;
  onRefresh: () => Promise<void>;
}) {
  const t = useT();
  const [busyId, setBusyId] = useState<string | null>(null);

  const onDeleteClick = async (id: string, title: string) => {
    if (!window.confirm(t('sidebar.deleteSessionConfirm', { name: title || id.slice(0, 8) }))) {
      return;
    }
    setBusyId(id);
    try {
      await onDelete(id);
    } finally {
      setBusyId(null);
    }
  };

  if (sessions.length === 0) {
    return (
      <div className="sidebar-section">
        <div className="sidebar-empty-block">
          <MessagesSquare size={24} />
          <p>{t('sidebar.sessionsEmpty')}</p>
          <button type="button" className="btn-secondary" onClick={() => void onRefresh()}>
            {t('sidebar.refresh')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="sidebar-section">
      <div className="sidebar-section-head">
        <h3>{t('sidebar.sessions')}</h3>
        <button
          type="button"
          className="ds-row-action"
          onClick={() => void onRefresh()}
          title={t('sidebar.refresh')}
        >
          <History size={11} />
        </button>
      </div>
      <div className="session-list">
        {sessions.map((s) => {
          const firstUser = (s.chat_history || []).find((m) => m.role === 'user');
          const title = sessionTitle(firstUser?.content) || t('sidebar.untitledSession');
          const isActive = s.session_id === currentId;
          const dsName = s.data_source_id
            ? dataSources.find((d) => d.id === s.data_source_id)?.name
            : undefined;
          return (
            <div key={s.session_id} className={`session-item ${isActive ? 'active' : ''}`}>
              <button
                type="button"
                className="session-item-main"
                onClick={() => onSwitch(s.session_id)}
                title={title}
              >
                <div className="session-title">{title}</div>
                <div className="session-sub">
                  {dsName ? <span className="session-ds">{dsName}</span> : null}
                  {s.updated_at ? (
                    <span className="session-time">{formatSessionTime(s.updated_at)}</span>
                  ) : null}
                </div>
              </button>
              <button
                type="button"
                className="ds-row-action ds-row-action-danger"
                onClick={() => onDeleteClick(s.session_id, title)}
                disabled={busyId === s.session_id}
                title={t('sidebar.delete')}
              >
                <Trash2 size={11} />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function formatSessionTime(iso: string): string {
  try {
    const d = new Date(iso);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    if (sameDay) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    const diff = (now.getTime() - d.getTime()) / 86400000;
    if (diff < 7) {
      return `${Math.floor(diff)}d`;
    }
    return d.toLocaleDateString([], { month: '2-digit', day: '2-digit' });
  } catch {
    return '';
  }
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
