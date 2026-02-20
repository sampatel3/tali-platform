import React, { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';

import {
  readDarkModePreference,
  setDarkModePreference,
  subscribeThemePreference,
} from '../../lib/themePreference';

export const GlobalThemeToggle = ({ className = '', compact = false }) => {
  const [darkMode, setDarkMode] = useState(() => readDarkModePreference());

  useEffect(() => {
    return subscribeThemePreference((next) => {
      setDarkMode(Boolean(next));
    });
  }, []);

  const targetModeLabel = darkMode ? 'light' : 'dark';

  return (
    <button
      type="button"
      title={`Switch to ${targetModeLabel} mode`}
      aria-label={`Switch to ${targetModeLabel} theme`}
      onClick={() => setDarkModePreference(!darkMode)}
      className={`inline-flex items-center gap-2 border px-3 py-2 font-mono text-xs font-bold transition-colors ${
        darkMode
          ? 'border-white/20 bg-[#111827] text-gray-100 hover:border-[var(--taali-purple)]'
          : 'border-gray-300 bg-white text-gray-800 hover:border-[var(--taali-purple)]'
      } ${className}`}
    >
      {darkMode ? <Sun size={14} /> : <Moon size={14} />}
      <span className={compact ? 'hidden sm:inline' : ''}>
        {darkMode ? 'Light Mode' : 'Dark Mode'}
      </span>
    </button>
  );
};
