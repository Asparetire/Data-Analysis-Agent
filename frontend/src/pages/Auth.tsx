import { useState, type FormEvent } from 'react';
import { Database, Loader2, AlertCircle } from 'lucide-react';
import { useAuthStore } from '../store/authStore';
import { useT } from '../hooks/useUi';

type Mode = 'login' | 'register';

export default function Auth() {
  const t = useT();
  const { login, register, status, error } = useAuthStore();
  const [mode, setMode] = useState<Mode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);
  const busy = status === 'loading';

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setLocalError(null);
    if (password.length < 8) {
      setLocalError(mode === 'register' ? '密码至少 8 位' : '密码不能为空');
      return;
    }
    try {
      if (mode === 'register') {
        await register(email, password);
      } else {
        await login(email, password);
      }
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    }
  };

  const shownError = localError ?? error;

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <Database size={28} />
          <h1>{t('app.title')}</h1>
        </div>

        <div className="auth-tabs">
          <button
            type="button"
            className={mode === 'login' ? 'active' : ''}
            onClick={() => {
              setMode('login');
              setLocalError(null);
            }}
          >
            登录
          </button>
          <button
            type="button"
            className={mode === 'register' ? 'active' : ''}
            onClick={() => {
              setMode('register');
              setLocalError(null);
            }}
          >
            注册
          </button>
        </div>

        <form onSubmit={onSubmit} className="auth-form">
          <label>
            <span>邮箱</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              required
              disabled={busy}
              placeholder="you@example.com"
            />
          </label>
          <label>
            <span>密码{mode === 'register' ? ' (至少 8 位)' : ''}</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
              required
              disabled={busy}
              minLength={mode === 'register' ? 8 : 1}
            />
          </label>

          {shownError ? (
            <div className="auth-error" role="alert">
              <AlertCircle size={14} style={{ verticalAlign: -2, marginRight: 6 }} />
              {shownError}
            </div>
          ) : null}

          <button type="submit" className="btn-primary auth-submit" disabled={busy}>
            {busy ? <Loader2 size={14} className="spin" /> : null}
            {mode === 'register' ? '注册并登录' : '登录'}
          </button>
        </form>

        <p className="auth-hint">
          {mode === 'login' ? '没有账号？点上方"注册"' : '已有账号？点上方"登录"'}
        </p>
      </div>
    </div>
  );
}
