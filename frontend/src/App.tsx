import { useEffect, useMemo, type ReactNode } from 'react';
import { Database, Home as HomeIcon, BarChart3, Loader2 } from 'lucide-react';
import { useChatStore, type PageKey } from './store/chatStore';
import { useChat } from './hooks/useChat';
import { useUpload } from './hooks/useUpload';
import Sidebar from './components/Sidebar';
import FileUpload from './components/Upload';
import Home from './pages/Home';
import Analysis from './pages/Analysis';
import './App.css';

const NAV_ITEMS: { key: PageKey; label: string; icon: ReactNode }[] = [
  { key: 'home', label: '对话', icon: <HomeIcon size={14} /> },
  { key: 'analysis', label: '分析', icon: <BarChart3 size={14} /> },
];

export default function App() {
  const page = useChatStore((s) => s.page);
  const setPage = useChatStore((s) => s.setPage);
  const activeId = useChatStore((s) => s.activeDataSourceId);
  const setActive = useChatStore((s) => s.setActiveDataSource);
  const sessionId = useChatStore((s) => s.sessionId);
  const ensureSession = useChatStore((s) => s.ensureSession);
  const restoreSession = useChatStore((s) => s.restoreSession);
  const { sendMessage } = useChat();
  const { status, reset } = useUpload();

  // Bootstrap the session on mount: try to rehydrate the persisted id, or
  // create a new one. This way the very first chat doesn't have to wait for
  // a round-trip to mint a session.
  useEffect(() => {
    let alive = true;
    (async () => {
      if (sessionId) {
        await restoreSession(sessionId);
        if (!alive) return;
      }
      if (!useChatStore.getState().sessionId) {
        try {
          await ensureSession();
        } catch (e) {
          // Surface a connection error once; the rest of the app will keep
          // working and useChat will surface the same error on the next send.
          // eslint-disable-next-line no-console
          console.error('failed to create session', e);
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [sessionId, ensureSession, restoreSession]);

  // Listen for suggestion chips from the Home page.
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<string>).detail;
      if (typeof detail === 'string' && detail.trim()) {
        sendMessage(detail, activeId);
      }
    };
    window.addEventListener('chat:suggest', handler);
    return () => window.removeEventListener('chat:suggest', handler);
  }, [sendMessage, activeId]);

  const Page = useMemo(() => (page === 'home' ? Home : Analysis), [page]);

  return (
    <div className="app">
      <header className="app-header">
        <h1>
          <Database size={20} /> 数据分析 Agent
        </h1>
        <nav className="nav">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={page === item.key ? 'active' : ''}
              onClick={() => setPage(item.key)}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </nav>
      </header>

      <main className="app-main">
        <Sidebar />

        <section className="chat-section">
          {page === 'home' && !activeId && (status === 'idle' || status === 'error') ? (
            <div
              style={{
                padding: 20,
                borderBottom: '1px solid var(--color-border)',
                background: 'var(--color-surface)',
              }}
            >
              <FileUpload
                onUploadSuccess={(fileId, filename) => {
                  setActive({ id: fileId, name: filename });
                }}
              />
            </div>
          ) : null}

          <Page />

          {status === 'uploading' ? (
            <div
              style={{
                position: 'fixed',
                right: 24,
                bottom: 24,
                background: 'var(--color-surface)',
                border: '1px solid var(--color-border)',
                boxShadow: 'var(--shadow-md)',
                padding: '10px 14px',
                borderRadius: 8,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontSize: 13,
                zIndex: 50,
              }}
            >
              <Loader2 className="spin" size={14} /> 上传中…
              <button
                type="button"
                onClick={reset}
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--color-text-muted)',
                  fontSize: 12,
                  marginLeft: 4,
                }}
              >
                取消
              </button>
            </div>
          ) : null}
        </section>
      </main>
    </div>
  );
}
