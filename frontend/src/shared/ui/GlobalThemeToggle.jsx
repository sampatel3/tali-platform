import React, { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';

import {
  readDarkModePreference,
  setDarkModePreference,
  subscribeThemePreference,
} from '../../lib/themePreference';

export const GlobalThemeToggle = () => {
  const [darkMode, setDarkMode] = useState(() => readDarkModePreference());

  useEffect(() => {
    return subscribeThemePreference((next) => {
      setDarkMode(Boolean(next));
    });
  }, []);

  return (
    <button
      type="button"
      aria-label={`Switch to ${darkMode ? 'light' : 'dark'} theme`}
      onClick={() => setDarkModePreference(!darkMode)}
      className={`fixed bottom-4 left-4 z-[60] inline-flex items-center gap-2 border px-3 py-2 font-mono text-xs font-bold shadow-lg transition-colors ${
        darkMode
          ? 'border-white/20 bg-[#111827] text-gray-100 hover:border-[var(--taali-purple)]'
          : 'border-gray-300 bg-white text-gray-800 hover:border-[var(--taali-purple)]'
      }`}
    >
      {darkMode ? <Sun size={14} /> : <Moon size={14} />}
      {darkMode ? 'Light Mode' : 'Dark Mode'}
    </button>
  );
};
