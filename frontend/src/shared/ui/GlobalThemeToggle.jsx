import React, { useEffect, useState } from 'react';

import {
  readDarkModePreference,
  setDarkModePreference,
  subscribeThemePreference,
} from '../../lib/themePreference';
import { ThemeModeToggle } from './ThemeModeToggle';

export const GlobalThemeToggle = ({ className = '', appearance = 'dual' }) => {
  const [darkMode, setDarkMode] = useState(() => readDarkModePreference());

  useEffect(() => {
    return subscribeThemePreference((next) => {
      setDarkMode(Boolean(next));
    });
  }, []);

  const targetModeLabel = darkMode ? 'light' : 'dark';
  const setMode = (nextDarkMode) => {
    if (Boolean(nextDarkMode) === darkMode) return;
    setDarkModePreference(Boolean(nextDarkMode));
  };

  return (
    <ThemeModeToggle
      value={darkMode ? 'dark' : 'light'}
      onChange={(nextValue) => setMode(nextValue === 'dark')}
      ariaLabel={`Theme toggle. Current mode is ${darkMode ? 'dark' : 'light'}.`}
      title={`Switch to ${targetModeLabel} mode`}
      className={className}
      appearance={appearance}
    />
  );
};
