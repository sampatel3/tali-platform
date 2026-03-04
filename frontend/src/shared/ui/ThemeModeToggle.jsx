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
        'inline-flex items-center gap-1 rounded-full border border-[var(--taali-border-soft)] bg-[var(--taali-nav-pill-bg)] p-1 shadow-[var(--taali-shadow-soft)] backdrop-blur-md',
        className
      )}
    >
      <button
        type="button"
        aria-pressed={!isDark}
        aria-label="Switch to light theme"
        onClick={() => handleSelect('light')}
        className={cx(
          'inline-flex h-9 w-9 items-center justify-center rounded-full transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--taali-purple)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--taali-surface)]',
          !isDark
            ? 'bg-[linear-gradient(135deg,var(--taali-purple),var(--taali-purple-hover))] text-[var(--taali-inverse-text)] shadow-[0_10px_24px_rgba(157,0,255,0.22)]'
            : 'text-[var(--taali-muted)] hover:bg-[var(--taali-surface-subtle)]'
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
          'inline-flex h-9 w-9 items-center justify-center rounded-full transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--taali-purple)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--taali-surface)]',
          isDark
            ? 'bg-[linear-gradient(135deg,var(--taali-purple),var(--taali-purple-hover))] text-[var(--taali-inverse-text)] shadow-[0_10px_24px_rgba(157,0,255,0.22)]'
            : 'text-[var(--taali-muted)] hover:bg-[var(--taali-surface-subtle)]'
        )}
      >
        <Moon size={16} />
      </button>
    </div>
  );
};
