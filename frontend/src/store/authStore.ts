import { create } from 'zustand';
import type { TokenResponse, UserView } from '../types';
import {
  getCurrentUser as apiGetMe,
  login as apiLogin,
  logout as apiLogout,
  refresh as apiRefresh,
  register as apiRegister,
} from '../services/api';

const ACCESS_KEY = 'data-analysis-agent:accessToken';
const REFRESH_KEY = 'data-analysis-agent:refreshToken';

function readAccess(): string | null {
  try {
    return window.localStorage.getItem(ACCESS_KEY);
  } catch {
    return null;
  }
}

function readRefresh(): string | null {
  try {
    return window.localStorage.getItem(REFRESH_KEY);
  } catch {
    return null;
  }
}

function writeTokens(access: string | null, refresh: string | null) {
  try {
    if (access) window.localStorage.setItem(ACCESS_KEY, access);
    else window.localStorage.removeItem(ACCESS_KEY);
    if (refresh) window.localStorage.setItem(REFRESH_KEY, refresh);
    else window.localStorage.removeItem(REFRESH_KEY);
  } catch {
    /* quota / private mode — silent */
  }
}

interface AuthState {
  user: UserView | null;
  accessToken: string | null;
  refreshToken: string | null;
  status: 'idle' | 'loading' | 'authed' | 'guest' | 'error';
  error: string | null;

  /** Bootstrap on app start: if a token is stored, fetch /auth/me to confirm. */
  bootstrap: () => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  /** Revoke the refresh token server-side, then clear local state.
   * Best-effort: local state is cleared even if the network call fails,
   * so a flaky connection can't strand the user in an authed UI. */
  logout: () => Promise<void>;
  /** Called by the axios interceptor when a 401 is observed on a request. */
  clearIfInvalid: () => void;
  /** Force a refresh-token rotation; returns the new access token or null. */
  tryRefresh: () => Promise<string | null>;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  accessToken: readAccess(),
  refreshToken: readRefresh(),
  status: 'idle',
  error: null,

  bootstrap: async () => {
    const access = get().accessToken;
    if (!access) {
      set({ status: 'guest' });
      return;
    }
    set({ status: 'loading', error: null });
    try {
      const user = await apiGetMe();
      set({ user, status: 'authed' });
    } catch {
      // Maybe the access token expired — try refresh before giving up.
      const newAccess = await get().tryRefresh();
      if (newAccess) {
        try {
          const user = await apiGetMe();
          set({ user, status: 'authed' });
          return;
        } catch {
          /* fall through */
        }
      }
      writeTokens(null, null);
      set({ user: null, accessToken: null, refreshToken: null, status: 'guest' });
    }
  },

  register: async (email, password) => {
    set({ status: 'loading', error: null });
    try {
      const tokens: TokenResponse = await apiRegister(email, password);
      writeTokens(tokens.access_token, tokens.refresh_token);
      set({
        accessToken: tokens.access_token,
        refreshToken: tokens.refresh_token,
        status: 'authed',
      });
      const user = await apiGetMe();
      set({ user });
    } catch (e) {
      set({ status: 'error', error: e instanceof Error ? e.message : String(e) });
      throw e;
    }
  },

  login: async (email, password) => {
    set({ status: 'loading', error: null });
    try {
      const tokens: TokenResponse = await apiLogin(email, password);
      writeTokens(tokens.access_token, tokens.refresh_token);
      set({
        accessToken: tokens.access_token,
        refreshToken: tokens.refresh_token,
        status: 'authed',
      });
      const user = await apiGetMe();
      set({ user });
    } catch (e) {
      set({ status: 'error', error: e instanceof Error ? e.message : String(e) });
      throw e;
    }
  },

  logout: async () => {
    // Phase 4A: revoke the refresh token server-side so its jti leaves Redis
    // immediately. Without this the jti lingers until natural expiry (default
    // 7 days), and a stolen token stays usable for that whole window.
    // Best-effort: clear local state regardless of network outcome so a flaky
    // connection can't strand the user in an authed UI.
    const refreshToken = get().refreshToken;
    if (refreshToken) {
      try {
        await apiLogout(refreshToken);
      } catch {
        /* network error / already revoked — proceed to clear local state */
      }
    }
    writeTokens(null, null);
    set({ user: null, accessToken: null, refreshToken: null, status: 'guest', error: null });
  },

  clearIfInvalid: () => {
    writeTokens(null, null);
    set({ user: null, accessToken: null, refreshToken: null, status: 'guest' });
  },

  tryRefresh: async () => {
    const refreshToken = get().refreshToken;
    if (!refreshToken) return null;
    try {
      const tokens: TokenResponse = await apiRefresh(refreshToken);
      writeTokens(tokens.access_token, tokens.refresh_token);
      set({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token });
      return tokens.access_token;
    } catch {
      writeTokens(null, null);
      set({
        user: null,
        accessToken: null,
        refreshToken: null,
        status: 'guest',
      });
      return null;
    }
  },
}));
