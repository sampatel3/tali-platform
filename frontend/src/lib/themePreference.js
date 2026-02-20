const THEME_STORAGE_KEY = 'taali_dark_mode';
const THEME_EVENT_NAME = 'taali-theme-changed';

export const readDarkModePreference = () => {
  if (typeof window === 'undefined') return true;
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  return stored == null ? true : stored === '1';
};

export const applyDarkModeClass = (darkMode) => {
  if (typeof document === 'undefined') return;
  document.documentElement.classList.toggle('dark', Boolean(darkMode));
};

export const setDarkModePreference = (darkMode) => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(THEME_STORAGE_KEY, darkMode ? '1' : '0');
  applyDarkModeClass(darkMode);
  window.dispatchEvent(new CustomEvent(THEME_EVENT_NAME, { detail: { darkMode: Boolean(darkMode) } }));
};

export const subscribeThemePreference = (listener) => {
  if (typeof window === 'undefined') return () => {};

  const onStorage = (event) => {
    if (!event || event.key === THEME_STORAGE_KEY) {
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

