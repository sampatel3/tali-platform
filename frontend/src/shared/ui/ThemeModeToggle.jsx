import React from 'react';
import { Moon, Sun } from 'lucide-react';

import { cx } from './TaaliPrimitives';

export const ThemeModeToggle = ({
  value = 'dark',
  onChange,
  className = '',
  ariaLabel = 'Theme toggle',
  title,
  appearance = 'dual',
}) => {
  const isDark = value === 'dark';
  const targetMode = isDark ? 'light' : 'dark';

  const handleSelect = (nextValue) => {
    if (nextValue === value) return;
    onChange?.(nextValue);
  };

  if (appearance === 'single') {
    return (
      <div role="group" aria-label={ariaLabel} className={cx('inline-flex', className)}>
        <button
          type="button"
          aria-label={ariaLabel}
          aria-pressed={isDark}
          title={title || `Switch to ${targetMode} mode`}
          onClick={() => handleSelect(targetMode)}
          className={cx(
            'icon-btn focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--taali-purple)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--taali-surface)]',
            isDark ? 'text-[var(--ink-2)]' : 'text-[var(--ink)]'
          )}
        >
          {isDark ? <Moon size={15} /> : <Sun size={15} />}
        </button>
      </div>
    );
  }

  return (
    <div
      role="group"
      aria-label={ariaLabel}
      title={title}
      className={cx(
        'inline-flex items-center gap-2',
        className
      )}
    >
      <button
        type="button"
        aria-pressed={!isDark}
        aria-label="Switch to light theme"
        onClick={() => handleSelect('light')}
        className={cx(
          'taali-btn inline-flex h-10 w-10 items-center justify-center rounded-full transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--taali-purple)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--taali-surface)]',
          !isDark
            ? 'taali-btn-primary text-[var(--taali-inverse-text)]'
            : 'taali-btn-secondary text-[var(--taali-muted)] hover:text-[var(--taali-text)]'
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
          'taali-btn inline-flex h-10 w-10 items-center justify-center rounded-full transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--taali-purple)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--taali-surface)]',
          isDark
            ? 'taali-btn-primary text-[var(--taali-inverse-text)]'
            : 'taali-btn-secondary text-[var(--taali-muted)] hover:text-[var(--taali-text)]'
        )}
      >
        <Moon size={16} />
      </button>
    </div>
  );
};
