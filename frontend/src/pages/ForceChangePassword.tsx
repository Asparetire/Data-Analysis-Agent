import { useState, type FormEvent } from 'react';
import { Database, Loader2, AlertCircle } from 'lucide-react';
import axios from 'axios';
import { useAuthStore } from '../store/authStore';
import { useT } from '../hooks/useUi';

/**
 * Shown when the logged-in user has must_change_password=true (migration
 * admin on first login). Blocks the main UI until the user sets a fresh
 * password. The only escape hatches are submitting the form or logging out.
 */
export default function ForceChangePassword() {
  const t = useT();
  const { changePassword, logout, user } = useAuthStore();
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (newPassword.length < 8) {
      setError('新密码至少 8 位');
      return;
    }
    if (!/[a-zA-Z]/.test(newPassword) || !/\d/.test(newPassword)) {
      setError('新密码须同时包含字母和数字');
      return;
    }
    if (newPassword !== confirm) {
      setError('两次输入的新密码不一致');
      return;
    }
    if (newPassword === oldPassword) {
      setError('新密码不能与旧密码相同');
      return;
    }
    setBusy(true);
    try {
      await changePassword(oldPassword, newPassword);
      // authStore.changePassword re-fetches /auth/me, which flips
      // must_change_password to false — App.tsx re-renders into the main UI.
    } catch (err) {
      if (axios.isAxiosError(err)) {
        const status = err.response?.status;
        const detail = err.response?.data?.detail;
        if (status === 401) setError('旧密码不正确');
        else if (status === 422) setError('密码不符合复杂度要求');
        else if (typeof detail === 'string') setError(detail);
        else setError('修改失败，请稍后再试');
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <Database size={28} />
          <h1>{t('app.title')}</h1>
        </div>
        <div className="auth-tabs">
          <button type="button" className="active">
            修改密码
          </button>
        </div>
        <p style={{ fontSize: 13, color: 'var(--color-text-muted)', margin: '4px 0 12px' }}>
          首次登录或管理员创建的账号需要修改密码后才能使用。当前账号：{user?.email}
        </p>
        <form onSubmit={onSubmit} className="auth-form">
          <label>
            <span>旧密码</span>
            <input
              type="password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              autoComplete="current-password"
              required
              disabled={busy}
              minLength={1}
            />
          </label>
          <label>
            <span>新密码（至少 8 位，含字母+数字）</span>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
              required
              disabled={busy}
              minLength={8}
            />
          </label>
          <label>
            <span>确认新密码</span>
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
              required
              disabled={busy}
              minLength={8}
            />
          </label>
          {error ? (
            <div className="auth-error" role="alert">
              <AlertCircle size={14} style={{ verticalAlign: -2, marginRight: 6 }} />
              {error}
            </div>
          ) : null}
          <button type="submit" className="btn-primary auth-submit" disabled={busy}>
            {busy ? <Loader2 size={14} className="spin" /> : null}
            修改密码并继续
          </button>
        </form>
        <p className="auth-hint">
          <button
            type="button"
            onClick={() => void logout()}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--color-text-muted)',
              cursor: 'pointer',
              padding: 0,
              textDecoration: 'underline',
              fontSize: 12,
            }}
          >
            退出登录
          </button>
        </p>
      </div>
    </div>
  );
}
