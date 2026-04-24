const THEME_STORAGE_KEY = 'taali-theme';
const LEGACY_THEME_STORAGE_KEY = 'taali_dark_mode';
const THEME_EVENT_NAME = 'taali-theme-changed';

export const readDarkModePreference = () => {
  if (typeof window === 'undefined') return false;
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (stored === 'dark') return true;
  if (stored === 'light') return false;
  const legacyStored = window.localStorage.getItem(LEGACY_THEME_STORAGE_KEY);
  if (legacyStored != null) return legacyStored === '1';
  return window.matchMedia?.('(prefers-color-scheme: dark)')?.matches ?? false;
};

export const applyDarkModeClass = (darkMode) => {
  if (typeof document === 'undefined') return;
  const nextTheme = darkMode ? 'dark' : 'light';
  document.documentElement.classList.toggle('dark', Boolean(darkMode));
  document.documentElement.setAttribute('data-theme', nextTheme);
};

export const setDarkModePreference = (darkMode) => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(THEME_STORAGE_KEY, darkMode ? 'dark' : 'light');
  window.localStorage.setItem(LEGACY_THEME_STORAGE_KEY, darkMode ? '1' : '0');
  applyDarkModeClass(darkMode);
  window.dispatchEvent(new CustomEvent(THEME_EVENT_NAME, { detail: { darkMode: Boolean(darkMode) } }));
};

export const subscribeThemePreference = (listener) => {
  if (typeof window === 'undefined') return () => {};

  const onStorage = (event) => {
    if (!event || event.key === THEME_STORAGE_KEY || event.key === LEGACY_THEME_STORAGE_KEY) {
      listener(readDarkModePreference());
    }
  };
  const onThemeEvent = () => {
    listener(readDarkModePreference());
  };

  window.addEventListener('storage', onStorage);
  window.addEventListener(THEME_EVENT_NAME, onThemeEvent);
  return () => {
    window.removeEventListener('storage', onStorage);
    window.removeEventListener(THEME_EVENT_NAME, onThemeEvent);
  };
};
