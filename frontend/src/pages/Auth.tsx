import { useState, type FormEvent } from 'react';
import { Database, Loader2, AlertCircle } from 'lucide-react';
import axios from 'axios';
import { useAuthStore } from '../store/authStore';
import { useT } from '../hooks/useUi';

type Mode = 'login' | 'register';

/**
 * Map backend auth errors to readable Chinese. The raw axios error message
 * is usually "Request failed with status code 401" — useless to a non-dev.
 * FastAPI's 4xx bodies are `{"detail": "..."}`; we extract and humanize.
 */
function describeAuthError(err: unknown, mode: Mode): string {
  if (axios.isAxiosError(err)) {
    const status = err.response?.status;
    const detail = err.response?.data?.detail;
    if (status === 401) {
      return mode === 'login' ? '邮箱或密码不正确' : '注册失败，请检查输入';
    }
    if (status === 409) {
      return '该邮箱已注册，请直接登录';
    }
    if (status === 422) {
      // FastAPI validation error — detail is an array of field errors.
      if (Array.isArray(detail) && detail.length > 0) {
        const first = detail[0];
        if (first?.msg?.includes('email')) return '邮箱格式不正确';
        if (first?.msg?.includes('password')) return '密码不符合要求（须含字母+数字，≥8 位）';
      }
      return '输入格式有误，请检查邮箱和密码';
    }
    if (status === 429) {
      return '尝试过于频繁，请稍后再试';
    }
    if (status === 500) return '服务器异常，请稍后再试';
    if (status === 0 || err.code === 'ERR_NETWORK') {
      return '无法连接服务器，请检查网络';
    }
    if (typeof detail === 'string') return detail;
  }
  return err instanceof Error ? err.message : String(err);
}

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
      setLocalError(describeAuthError(err, mode));
    }
  };

  const shownError = localError ?? (error ? describeAuthError(error, mode) : null);

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
