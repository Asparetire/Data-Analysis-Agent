import { useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  Database,
  Home as HomeIcon,
  BarChart3,
  Loader2,
  Moon,
  Sun,
  Menu,
  X,
  LogOut,
  Plus,
  Upload as UploadIcon,
} from 'lucide-react';
import { useChatStore, type PageKey } from './store/chatStore';
import { useAuthStore } from './store/authStore';
import { useChat } from './hooks/useChat';
import { useUpload } from './hooks/useUpload';
import { toggleLocale, toggleTheme, useT, useTheme } from './hooks/useUi';
import Sidebar from './components/Sidebar';
import FileUpload from './components/Upload';
import ErrorBoundary from './components/ErrorBoundary';
import Home from './pages/Home';
import Analysis from './pages/Analysis';
import Auth from './pages/Auth';
import './App.css';

export default function App() {
  const page = useChatStore((s) => s.page);
  const setPage = useChatStore((s) => s.setPage);
  const activeId = useChatStore((s) => s.activeDataSourceId);
  const sessionId = useChatStore((s) => s.sessionId);
  const ensureSession = useChatStore((s) => s.ensureSession);
  const restoreSession = useChatStore((s) => s.restoreSession);
  const { sendMessage } = useChat();
  const { status, reset } = useUpload();
  const resetSession = useChatStore((s) => s.resetSession);
  const t = useT();
  const [theme] = useTheme();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);

  const triggerNewChat = () => {
    if (status === 'uploading') return;
    void resetSession();
    setPage('home');
  };

  // Phase 4A: bootstrap auth on mount. While status is loading we show a
  // splash; once it resolves to 'guest' we render the Auth page; only when
  // it reaches 'authed' do we render the rest of the app.
  const authStatus = useAuthStore((s) => s.status);
  const bootstrap = useAuthStore((s) => s.bootstrap);
  const authUser = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  const navItems: { key: PageKey; label: string; icon: ReactNode }[] = useMemo(
    () => [
      { key: 'home', label: t('nav.home'), icon: <HomeIcon size={14} /> },
      { key: 'analysis', label: t('nav.analysis'), icon: <BarChart3 size={14} /> },
    ],
    [t],
  );

  // Bootstrap the session on mount: try to rehydrate the persisted id, or
  // create a new one. This way the very first chat doesn't have to wait for
  // a round-trip to mint a session.
  useEffect(() => {
    if (authStatus !== 'authed') return;
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
          // eslint-disable-next-line no-console
          console.error('failed to create session', e);
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [sessionId, ensureSession, restoreSession, authStatus]);

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

  if (authStatus === 'idle' || authStatus === 'loading') {
    return (
      <div className="app-splash">
        <Loader2 className="spin" size={24} />
      </div>
    );
  }

  if (authStatus !== 'authed') {
    return <Auth />;
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>
          <Database size={20} /> {t('app.title')}
        </h1>
        <div className="app-header-tools">
          <nav className="nav">
            {navItems.map((item) => (
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
          <button
            type="button"
            className="icon-btn"
            onClick={() => setUploadOpen(true)}
            title={t('nav.upload')}
            aria-label={t('nav.upload')}
            disabled={status === 'uploading'}
          >
            <UploadIcon size={16} />
          </button>
          <button
            type="button"
            className="icon-btn"
            onClick={triggerNewChat}
            title={t('nav.newChat')}
            aria-label={t('nav.newChat')}
          >
            <Plus size={16} />
          </button>
          {authUser ? (
            <span className="auth-user" title={authUser.email}>
              {authUser.email}
            </span>
          ) : null}
          <button
            type="button"
            className="icon-btn lang"
            onClick={toggleLocale}
            title={t('lang.toggle')}
            aria-label={t('lang.toggle')}
          >
            {t('lang.toggle')}
          </button>
          <button
            type="button"
            className="icon-btn"
            onClick={toggleTheme}
            title={t('theme.toggle')}
            aria-label={t('theme.toggle')}
          >
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          </button>
          <button
            type="button"
            className="icon-btn"
            onClick={() => {
              void logout();
            }}
            title="退出登录"
            aria-label="退出登录"
          >
            <LogOut size={16} />
          </button>
          <button
            type="button"
            className="icon-btn sidebar-toggle"
            onClick={() => setDrawerOpen((v) => !v)}
            title={drawerOpen ? t('common.cancel') : t('sidebar.sources')}
            aria-label={drawerOpen ? t('common.cancel') : t('sidebar.sources')}
          >
            {drawerOpen ? <X size={16} /> : <Menu size={16} />}
          </button>
        </div>
      </header>

      <main className="app-main">
        {drawerOpen ? (
          <div className="sidebar-backdrop" onClick={() => setDrawerOpen(false)} />
        ) : null}
        <Sidebar drawerOpen={drawerOpen} onClose={() => setDrawerOpen(false)} />

        <section className="chat-section">
          <ErrorBoundary>{<Page />}</ErrorBoundary>

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
              <Loader2 className="spin" size={14} /> {t('upload.uploading')}
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
                {t('common.cancel')}
              </button>
            </div>
          ) : null}
        </section>
      </main>

      {uploadOpen ? (
        <div
          className="modal-backdrop"
          role="dialog"
          aria-modal="true"
          onClick={() => status !== 'uploading' && setUploadOpen(false)}
        >
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 480 }}>
            <div className="modal-title" style={{ justifyContent: 'space-between' }}>
              <span>{t('nav.upload')}</span>
              <button
                type="button"
                className="icon-btn"
                onClick={() => status !== 'uploading' && setUploadOpen(false)}
                aria-label={t('common.cancel')}
                disabled={status === 'uploading'}
              >
                <X size={14} />
              </button>
            </div>
            <div className="modal-body">
              <FileUpload
                onUploadSuccess={() => {
                  setUploadOpen(false);
                  setPage('home');
                }}
              />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
