import { create } from 'zustand';
import type { ChatMessage, DataSource, SessionView } from '../types';
import {
  createSession as apiCreateSession,
  getDataSources as apiGetDataSources,
  getSession as apiGetSession,
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
  setSessionId: (id: string | null) => void;
  ensureSession: () => Promise<string>;
  restoreSession: (id: string) => Promise<SessionView | null>;
  resetSession: () => Promise<void>;

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

  setSessionId: (id) => {
    writeStoredSessionId(id);
    set({ sessionId: id, sessionError: null });
  },

  ensureSession: async () => {
    const existing = get().sessionId;
    if (existing) return existing;
    const created = await apiCreateSession();
    writeStoredSessionId(created.session_id);
    set({ sessionId: created.session_id, sessionReady: true, sessionError: null });
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
