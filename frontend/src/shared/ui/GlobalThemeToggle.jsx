import React, { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';

import {
  readDarkModePreference,
  setDarkModePreference,
  subscribeThemePreference,
} from '../../lib/themePreference';

export const GlobalThemeToggle = ({ className = '' }) => {
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
      role="switch"
      aria-checked={darkMode}
      onClick={() => setDarkModePreference(!darkMode)}
      className={`inline-flex items-center border-2 border-[var(--taali-border)] bg-[var(--taali-surface)] p-1.5 text-[var(--taali-text)] transition-colors hover:border-[var(--taali-purple)] ${className}`}
    >
      <span
        aria-hidden="true"
        className={`relative inline-flex h-6 w-11 items-center rounded-full border-2 transition-colors ${
          darkMode
            ? 'border-[var(--taali-purple-hover)] bg-[var(--taali-purple)]'
            : 'border-[var(--taali-border-muted)] bg-[#c7cbd5]'
        }`}
      >
        <span
          className={`inline-flex h-4 w-4 transform items-center justify-center rounded-full border transition-transform ${
            darkMode
              ? 'translate-x-5 border-white/40 bg-[var(--taali-surface)] text-[var(--taali-purple)]'
              : 'translate-x-0.5 border-[var(--taali-border-muted)] bg-white text-[#6b7280]'
          }`}
        >
          {darkMode ? <Moon size={10} /> : <Sun size={10} />}
        </span>
      </span>
    </button>
  );
};
