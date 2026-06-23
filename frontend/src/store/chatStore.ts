import { create } from 'zustand';
import type { ChatMessage, DataSource, SessionView } from '../types';
import {
  createSession as apiCreateSession,
  deleteSession as apiDeleteSession,
  getDataSources as apiGetDataSources,
  getSession as apiGetSession,
  listSessions as apiListSessions,
} from '../services/api';

export type PageKey = 'home' | 'analysis';

export type UploadStatus = 'idle' | 'uploading' | 'success' | 'error';

const SESSION_STORAGE_KEY = 'data-analysis-agent:sessionId';

function readStoredSessionId(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage.getItem(SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredSessionId(value: string | null) {
  if (typeof window === 'undefined') return;
  try {
    if (value) window.localStorage.setItem(SESSION_STORAGE_KEY, value);
    else window.localStorage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    /* ignore quota / private-mode errors */
  }
}

function toChatMessage(item: SessionView['chat_history'][number]): ChatMessage {
  return {
    role: item.role,
    content: item.content,
    timestamp: item.timestamp || new Date().toISOString(),
    chartData: item.chart_data ?? undefined,
    sqlQuery: item.sql_query ?? undefined,
  };
}

interface ChatState {
  // Session
  sessionId: string | null;
  sessionReady: boolean;
  sessionError: string | null;
  sessionCreatedAt: string | null;
  setSessionId: (id: string | null) => void;
  ensureSession: () => Promise<string>;
  restoreSession: (id: string) => Promise<SessionView | null>;
  resetSession: () => Promise<void>;
  /** All live sessions owned by the current user, for the sidebar list. */
  sessions: SessionView[];
  loadSessions: () => Promise<void>;
  /** Push the current session snapshot into the sessions list (used by newChat). */
  upsertSession: (view: SessionView) => void;
  /** Create a fresh session without dropping the old one from the list. */
  newChat: () => Promise<void>;
  /** Switch to an existing session by id (loads its messages + data source). */
  switchSession: (id: string) => Promise<void>;
  /** Delete a session server-side and remove it from the list. */
  removeSession: (id: string) => Promise<void>;

  // Active data source (the "primary")
  activeDataSourceId: string | undefined;
  activeDataSourceName: string;
  setActiveDataSource: (ds: { id: string; name: string } | undefined) => void;
  dataSources: DataSource[];
  setDataSources: (list: DataSource[]) => void;

  // Phase 3C: every data source the current session is bound to. The
  // first entry is the primary; the rest are attached for JOINs.
  boundDataSourceIds: string[];
  setBoundDataSourceIds: (ids: string[]) => void;

  // Page navigation
  page: PageKey;
  setPage: (p: PageKey) => void;

  // Chat
  messages: ChatMessage[];
  isLoading: boolean;
  appendMessage: (m: ChatMessage) => void;
  setMessages: (list: ChatMessage[]) => void;
  setLoading: (loading: boolean) => void;
  clearChat: () => void;

  // Upload
  uploadStatus: UploadStatus;
  uploadError: string | null;
  uploadedFileName: string | null;
  setUploadStatus: (status: UploadStatus) => void;
  setUploadedFileName: (name: string | null) => void;
  setUploadError: (msg: string | null) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessionId: readStoredSessionId(),
  sessionReady: false,
  sessionError: null,
  sessionCreatedAt: null,
  sessions: [],

  setSessionId: (id) => {
    writeStoredSessionId(id);
    set({ sessionId: id, sessionError: null });
  },

  ensureSession: async () => {
    const existing = get().sessionId;
    if (existing) return existing;
    const created = await apiCreateSession();
    writeStoredSessionId(created.session_id);
    set({
      sessionId: created.session_id,
      sessionReady: true,
      sessionError: null,
      sessionCreatedAt: created.created_at ?? new Date().toISOString(),
    });
    return created.session_id;
  },

  restoreSession: async (id) => {
    try {
      const view = await apiGetSession(id);
      let dataSourceName = '';
      if (view.data_source_id) {
        // Resolve the human-readable name from the data source list.
        // The Sidebar already populates this on mount, but on a cold
        // start we may have to fetch it ourselves.
        const cached = get().dataSources;
        const hit = cached.find((ds) => ds.id === view.data_source_id);
        if (hit) {
          dataSourceName = hit.name;
        } else {
          try {
            const list = await apiGetDataSources();
            set({ dataSources: list });
            dataSourceName = list.find((ds) => ds.id === view.data_source_id)?.name ?? '';
          } catch {
            // Network error: leave name empty; the Sidebar will fill it in
            // on its own fetch.
          }
        }
      }
      writeStoredSessionId(view.session_id);
      set({
        sessionId: view.session_id,
        sessionReady: true,
        sessionError: null,
        sessionCreatedAt: view.created_at ?? null,
        messages: (view.chat_history || []).map(toChatMessage),
        activeDataSourceId: view.data_source_id || undefined,
        activeDataSourceName: dataSourceName,
        boundDataSourceIds: view.data_source_ids ?? [],
      });
      return view;
    } catch (e) {
      // 404: server forgot the session, drop the local id and start fresh.
      writeStoredSessionId(null);
      set({ sessionId: null, sessionReady: false, messages: [] });
      return null;
    }
  },

  resetSession: async () => {
    set({ sessionId: null, sessionReady: false, messages: [] });
    writeStoredSessionId(null);
    await get().ensureSession();
  },

  loadSessions: async () => {
    try {
      const list = await apiListSessions();
      // Newest first — the backend returns insertion order which is
      // creation order, but we want recency by updated_at to surface
      // active conversations.
      list.sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''));
      set({ sessions: list });
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('failed to load sessions', e);
    }
  },

  upsertSession: (view) => {
    set((s) => {
      const others = s.sessions.filter((x) => x.session_id !== view.session_id);
      return { sessions: [view, ...others] };
    });
  },

  newChat: async () => {
    // Preserve the current session in the sidebar list before minting a new
    // one. We don't refetch the list here — loadSessions() is called by the
    // sidebar when its sessions tab mounts, and restoreSession keeps the
    // entry fresh on switch.
    const currentId = get().sessionId;
    if (currentId) {
      try {
        const view = await apiGetSession(currentId);
        get().upsertSession(view);
      } catch {
        /* session may already be gone — fine */
      }
    }
    const created = await apiCreateSession();
    writeStoredSessionId(created.session_id);
    set({
      sessionId: created.session_id,
      sessionReady: true,
      sessionError: null,
      sessionCreatedAt: created.created_at ?? new Date().toISOString(),
      messages: [],
      activeDataSourceId: undefined,
      activeDataSourceName: '',
      boundDataSourceIds: [],
    });
  },

  switchSession: async (id) => {
    await get().restoreSession(id);
  },

  removeSession: async (id) => {
    try {
      await apiDeleteSession(id);
    } catch {
      /* server may have already expired it — drop from list regardless */
    }
    set((s) => ({ sessions: s.sessions.filter((x) => x.session_id !== id) }));
    // If we just deleted the active session, mint a fresh one.
    if (get().sessionId === id) {
      await get().newChat();
    }
  },

  activeDataSourceId: undefined,
  activeDataSourceName: '',
  setActiveDataSource: (ds) =>
    set({
      activeDataSourceId: ds?.id,
      activeDataSourceName: ds?.name ?? '',
      // Toggling the active source also re-syncs the bound list. Single-
      // source is the common case; the Sidebar wires this through.
      boundDataSourceIds: ds ? [ds.id] : [],
    }),
  dataSources: [],
  setDataSources: (list) => set({ dataSources: list }),

  boundDataSourceIds: [],
  setBoundDataSourceIds: (ids) => set({ boundDataSourceIds: ids }),

  page: 'home',
  setPage: (p) => set({ page: p }),

  messages: [],
  isLoading: false,
  appendMessage: (m) => set((s) => ({ messages: [...s.messages, m] })),
  setMessages: (list) => set({ messages: list }),
  setLoading: (loading) => set({ isLoading: loading }),
  clearChat: () => set({ messages: [] }),

  uploadStatus: 'idle',
  uploadError: null,
  uploadedFileName: null,
  setUploadStatus: (status) => set({ uploadStatus: status }),
  setUploadedFileName: (name) => set({ uploadedFileName: name }),
  setUploadError: (msg) => set({ uploadError: msg }),
}));
