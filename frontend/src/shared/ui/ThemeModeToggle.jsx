import React from 'react';
import { Moon, Sun } from 'lucide-react';

import { cx } from './TaaliPrimitives';

export const ThemeModeToggle = ({
  value = 'dark',
  onChange,
  className = '',
  ariaLabel = 'Theme toggle',
  title,
}) => {
  const isDark = value === 'dark';

  const handleSelect = (nextValue) => {
    if (nextValue === value) return;
    onChange?.(nextValue);
  };

  return (
    <div
      role="group"
      aria-label={ariaLabel}
      title={title}
      className={cx(
        'relative inline-flex items-center rounded-full border border-[var(--taali-border-soft)] bg-[var(--taali-nav-pill-bg)] p-1 shadow-[var(--taali-shadow-soft)] backdrop-blur-md',
        className
      )}
    >
      <span
        aria-hidden="true"
        className={cx(
          'pointer-events-none absolute left-1 top-1 h-10 w-10 rounded-full border border-[rgba(255,255,255,0.24)] bg-[linear-gradient(135deg,var(--taali-purple),var(--taali-purple-hover))] shadow-[0_14px_30px_rgba(157,0,255,0.2)] transition-transform duration-200 ease-out',
          isDark ? 'translate-x-10' : 'translate-x-0'
        )}
      />
      <button
        type="button"
        aria-pressed={!isDark}
        aria-label="Switch to light theme"
        onClick={() => handleSelect('light')}
        className={cx(
          'relative z-10 inline-flex h-10 w-10 items-center justify-center rounded-full transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--taali-purple)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--taali-surface)]',
          !isDark
            ? 'text-[var(--taali-inverse-text)]'
            : 'text-[var(--taali-muted)] hover:text-[var(--taali-text)]'
        )}
      >
        <Sun size={16} />
      </button>
      <button
        type="button"
        aria-pressed={isDark}
        aria-label="Switch to dark theme"
        onClick={() => handleSelect('dark')}
        className={cx(
          'relative z-10 inline-flex h-10 w-10 items-center justify-center rounded-full transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--taali-purple)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--taali-surface)]',
          isDark
            ? 'text-[var(--taali-inverse-text)]'
            : 'text-[var(--taali-muted)] hover:text-[var(--taali-text)]'
        )}
      >
        <Moon size={16} />
      </button>
    </div>
  );
};
