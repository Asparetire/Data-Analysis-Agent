import { useCallback, useEffect, useState } from 'react';
import {
  DEFAULT_LOCALE,
  SUPPORTED_LOCALES,
  t as translate,
  type Locale,
  type StringKey,
} from '../i18n/translations';

const LOCALE_KEY = 'data-analysis-agent:locale';
const THEME_KEY = 'data-analysis-agent:theme';

export type Theme = 'light' | 'dark';

function readStoredLocale(): Locale {
  if (typeof window === 'undefined') return DEFAULT_LOCALE;
  const stored = window.localStorage.getItem(LOCALE_KEY);
  if (stored && (SUPPORTED_LOCALES as string[]).includes(stored)) {
    return stored as Locale;
  }
  return DEFAULT_LOCALE;
}

function readStoredTheme(): Theme {
  if (typeof window === 'undefined') return 'light';
  const stored = window.localStorage.getItem(THEME_KEY);
  if (stored === 'dark' || stored === 'light') return stored;
  // Honor the OS preference on first load.
  if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
    return 'dark';
  }
  return 'light';
}

function applyTheme(theme: Theme): void {
  if (typeof document === 'undefined') return;
  document.documentElement.setAttribute('data-theme', theme);
}

function storeLocale(value: Locale): void {
  try {
    window.localStorage.setItem(LOCALE_KEY, value);
  } catch {
    /* ignore */
  }
}

function storeTheme(value: Theme): void {
  try {
    window.localStorage.setItem(THEME_KEY, value);
  } catch {
    /* ignore */
  }
}

// Module-level singletons. The UI is a singleton already (one root render),
// and these values are referenced by hooks in many components. Storing at
// module level avoids re-reading localStorage on every component mount and
// lets us fan out changes via subscribers without dragging in a store lib.
let _locale: Locale = readStoredLocale();
let _theme: Theme = readStoredTheme();
const _subs = new Set<() => void>();

function notify(): void {
  for (const cb of _subs) cb();
}

export function getLocale(): Locale {
  return _locale;
}

export function getTheme(): Theme {
  return _theme;
}

export function setLocale(value: Locale): void {
  if (_locale === value) return;
  _locale = value;
  storeLocale(value);
  document.documentElement.setAttribute('lang', value);
  notify();
}

export function setTheme(value: Theme): void {
  if (_theme === value) return;
  _theme = value;
  applyTheme(value);
  storeTheme(value);
  notify();
}

export function toggleTheme(): void {
  setTheme(_theme === 'light' ? 'dark' : 'light');
}

export function toggleLocale(): void {
  setLocale(_locale === 'zh' ? 'en' : 'zh');
}

export function useLocale(): [Locale, (next: Locale) => void] {
  const [locale, setLocal] = useState<Locale>(_locale);
  useEffect(() => {
    const cb = () => setLocal(_locale);
    _subs.add(cb);
    return () => {
      _subs.delete(cb);
    };
  }, []);
  const setLocaleAndStore = useCallback((next: Locale) => {
    setLocale(next);
  }, []);
  return [locale, setLocaleAndStore];
}

export function useTheme(): [Theme, (next: Theme) => void] {
  const [theme, setLocal] = useState<Theme>(_theme);
  useEffect(() => {
    const cb = () => setLocal(_theme);
    _subs.add(cb);
    return () => {
      _subs.delete(cb);
    };
  }, []);
  const setThemeAndStore = useCallback((next: Theme) => {
    setTheme(next);
  }, []);
  return [theme, setThemeAndStore];
}

export function useT(): (key: StringKey, params?: Record<string, string | number>) => string {
  const [locale] = useLocale();
  return useCallback((key, params) => translate(locale, key, params), [locale]);
}

// Apply the persisted theme/locale on module load so the first paint
// already reflects the user's choice (no flash of light theme in dark mode).
if (typeof document !== 'undefined') {
  applyTheme(_theme);
  document.documentElement.setAttribute('lang', _locale);
}
