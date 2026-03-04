import React, { useEffect, useRef } from 'react';
import { X, Loader2 } from 'lucide-react';

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

const SHEET_LOCK_COUNT_ATTR = 'data-taali-sheet-lock-count';
const SHEET_PREVIOUS_OVERFLOW_ATTR = 'data-taali-sheet-previous-overflow';

export const cx = (...parts) => parts.filter(Boolean).join(' ');

const lockBodyScrollForSheet = () => {
  const body = document.body;
  const currentCount = Number(body.getAttribute(SHEET_LOCK_COUNT_ATTR) || '0');

  if (currentCount === 0) {
    body.setAttribute(SHEET_PREVIOUS_OVERFLOW_ATTR, body.style.overflow || '');
    body.style.overflow = 'hidden';
  }

  body.setAttribute(SHEET_LOCK_COUNT_ATTR, String(currentCount + 1));
};

const unlockBodyScrollForSheet = () => {
  const body = document.body;
  const currentCount = Number(body.getAttribute(SHEET_LOCK_COUNT_ATTR) || '0');
  const nextCount = Math.max(0, currentCount - 1);

  if (nextCount === 0) {
    const previousOverflow = body.getAttribute(SHEET_PREVIOUS_OVERFLOW_ATTR) || '';
    body.style.overflow = previousOverflow;
    body.removeAttribute(SHEET_LOCK_COUNT_ATTR);
    body.removeAttribute(SHEET_PREVIOUS_OVERFLOW_ATTR);
    return;
  }

  body.setAttribute(SHEET_LOCK_COUNT_ATTR, String(nextCount));
};

export const PageContainer = ({
  className = '',
  density = 'default',
  width = 'default',
  children,
}) => (
  <div
    className={cx(
      'taali-page',
      density === 'compact' ? 'taali-page-compact' : '',
      width === 'wide' ? 'taali-page-wide' : '',
      className
    )}
  >
    {children}
  </div>
);

export const PageHeader = ({
  title,
  subtitle,
  actions,
  className = '',
  density = 'default',
  children,
}) => (
  <header className={cx('taali-page-header', density === 'compact' ? 'taali-page-header-compact' : '', className)}>
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div>
        {title ? <h1 className="taali-page-title taali-display text-3xl font-semibold tracking-tight">{title}</h1> : null}
        {subtitle ? <p className="taali-page-subtitle mt-1 text-sm text-[var(--taali-muted)]">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
    {children ? <div className={density === 'compact' ? 'mt-3' : 'mt-4'}>{children}</div> : null}
  </header>
);

export const Panel = ({ as: Component = 'section', className = '', children, ...props }) => (
  <Component className={cx('taali-panel', className)} {...props}>
    {children}
  </Component>
);

export const Card = ({ as: Component = 'div', className = '', children, ...props }) => (
  <Component className={cx('taali-card', className)} {...props}>
    {children}
  </Component>
);

const BUTTON_VARIANT_CLASS = {
  primary: 'taali-btn-primary',
  secondary: 'taali-btn-secondary',
  ghost: 'taali-btn-ghost',
  danger: 'taali-btn-danger',
};

const BUTTON_SIZE_CLASS = {
  xs: 'px-2 py-1 text-xs',
  sm: 'px-2.5 py-1.5 text-xs',
  md: 'px-3 py-2 text-sm',
  lg: 'px-4 py-2.5 text-base',
};

export const Button = ({
  className = '',
  variant = 'secondary',
  size = 'md',
  children,
  ...props
}) => (
  <button
    className={cx(
      'taali-btn inline-flex items-center justify-center gap-1.5',
      BUTTON_VARIANT_CLASS[variant] || BUTTON_VARIANT_CLASS.secondary,
      BUTTON_SIZE_CLASS[size] || BUTTON_SIZE_CLASS.md,
      className
    )}
    {...props}
  >
    {children}
  </button>
);

export const Input = ({ className = '', ...props }) => (
  <input className={cx('taali-input', className)} {...props} />
);

export const Select = ({ className = '', children, ...props }) => (
  <select className={cx('taali-select', className)} {...props}>
    {children}
  </select>
);

export const Textarea = ({ className = '', ...props }) => (
  <textarea className={cx('taali-textarea', className)} {...props} />
);

const BADGE_VARIANT_CLASS = {
  purple: 'taali-badge-purple',
  muted: 'taali-badge-muted',
  success: 'taali-badge-success',
  warning: 'taali-badge-warning',
  danger: 'taali-badge-danger',
  info: 'taali-badge-info',
};

export const Badge = ({ variant = 'muted', className = '', children }) => (
  <span className={cx('taali-badge', BADGE_VARIANT_CLASS[variant] || BADGE_VARIANT_CLASS.muted, className)}>
    {children}
  </span>
);

export const Spinner = ({ size = 24, className = '' }) => (
  <Loader2 size={size} className={cx('animate-spin text-[var(--taali-purple)]', className)} aria-hidden />
);

export const TabBar = ({ tabs, activeTab, onChange, className = '', density = 'default' }) => (
  <div
    role="tablist"
    className={cx('flex flex-wrap gap-2', className)}
    aria-label="Tabs"
  >
    {tabs.map((tab) => {
      const isActive = activeTab === tab.id;
      return (
        <button
          key={tab.id}
          role="tab"
          aria-selected={isActive}
          aria-controls={tab.panelId}
          id={tab.id}
          type="button"
          onClick={() => onChange(tab.id)}
          className={cx(
            density === 'compact'
              ? 'rounded-full px-3 py-2 text-xs font-semibold transition-colors border'
              : 'rounded-full px-4 py-2.5 text-sm font-semibold transition-colors border',
            isActive
              ? 'border-[var(--taali-border-soft)] bg-[var(--taali-surface)] text-[var(--taali-text)] shadow-[var(--taali-shadow-soft)]'
              : 'border-transparent text-[var(--taali-muted)] hover:border-[var(--taali-border-soft)] hover:bg-[var(--taali-surface)] hover:text-[var(--taali-text)]'
          )}
        >
          {tab.label}
        </button>
      );
    })}
  </div>
);

export const EmptyState = ({ title, description, action, className = '' }) => (
  <div className={cx('taali-empty-state px-5 py-12 text-center', className)}>
    <p className="text-lg font-semibold text-[var(--taali-text)]">{title}</p>
    {description ? <p className="mt-1 text-sm text-[var(--taali-muted)]">{description}</p> : null}
    {action ? <div className="mt-4 flex justify-center">{action}</div> : null}
  </div>
);

export const TableShell = ({ className = '', children }) => (
  <div className={cx('taali-table-shell', className)}>
    {children}
  </div>
);

export const Sheet = ({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  side = 'right',
  headerContent = null,
  overlayClassName = '',
  panelClassName = '',
  headerClassName = '',
  bodyClassName = '',
  footerClassName = '',
}) => {
  const panelRef = useRef(null);
  const previousFocusRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;

    previousFocusRef.current = document.activeElement;
    lockBodyScrollForSheet();

    const focusables = panelRef.current?.querySelectorAll(FOCUSABLE_SELECTOR);
    if (focusables && focusables.length > 0) {
      focusables[0].focus();
    } else {
      panelRef.current?.focus();
    }

    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const items = panelRef.current?.querySelectorAll(FOCUSABLE_SELECTOR);
      if (!items || items.length === 0) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      unlockBodyScrollForSheet();
      if (previousFocusRef.current && typeof previousFocusRef.current.focus === 'function') {
        previousFocusRef.current.focus();
      }
    };
  }, [onClose, open]);

  if (!open) return null;

  return (
    <div
      className={cx('fixed inset-0 z-50 bg-[rgba(12,18,32,0.38)] backdrop-blur-sm', overlayClassName)}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={cx(
          'absolute inset-x-0 bottom-0 flex max-h-[92vh] flex-col overflow-hidden border border-[var(--taali-border-soft)] bg-[var(--taali-surface-elevated)] shadow-[var(--taali-shadow-strong)] focus:outline-none motion-safe:animate-[taali-sheet-in_180ms_ease-out] md:inset-y-3 md:h-[calc(100%-1.5rem)] md:max-h-none md:w-[680px] md:rounded-[var(--taali-radius-panel)]',
          side === 'left'
            ? 'md:left-3 md:right-auto'
            : 'md:right-3 md:left-auto',
          panelClassName
        )}
      >
        <div className={cx('border-b border-[var(--taali-border-soft)] bg-[color:var(--taali-surface)] px-5 py-4 backdrop-blur-sm', headerClassName)}>
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0 flex-1">
              {headerContent || (
                <>
                  <h2 className="taali-display text-xl font-semibold tracking-tight">{title}</h2>
                  {description ? <p className="mt-1 text-sm text-[var(--taali-muted)]">{description}</p> : null}
                </>
              )}
            </div>
            <Button
              type="button"
              onClick={onClose}
              variant="ghost"
              size="sm"
              aria-label="Close"
              className="!px-2 !py-2"
            >
              <X size={14} />
            </Button>
          </div>
        </div>
        <div
          className={cx('min-h-0 flex-1 overflow-y-auto px-5 py-5', bodyClassName)}
        >
          {children}
        </div>
        {footer ? (
          <div className={cx('border-t border-[var(--taali-border-soft)] bg-[color:var(--taali-surface)] px-5 py-4 backdrop-blur-sm', footerClassName)}>
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  );
};

export const Dialog = ({
  open,
  onClose,
  title,
  description,
  children,
  footer = null,
  headerClassName = '',
  bodyClassName = '',
  footerClassName = '',
  panelClassName = '',
  overlayClassName = '',
}) => {
  const panelRef = useRef(null);
  const previousFocusRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;

    previousFocusRef.current = document.activeElement;
    lockBodyScrollForSheet();

    const focusables = panelRef.current?.querySelectorAll(FOCUSABLE_SELECTOR);
    if (focusables && focusables.length > 0) {
      focusables[0].focus();
    } else {
      panelRef.current?.focus();
    }

    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const items = panelRef.current?.querySelectorAll(FOCUSABLE_SELECTOR);
      if (!items || items.length === 0) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      unlockBodyScrollForSheet();
      if (previousFocusRef.current && typeof previousFocusRef.current.focus === 'function') {
        previousFocusRef.current.focus();
      }
    };
  }, [onClose, open]);

  if (!open) return null;

  return (
    <div
      className={cx('fixed inset-0 z-[60] bg-[rgba(12,18,32,0.42)] px-4 py-6 backdrop-blur-sm', overlayClassName)}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="flex min-h-full items-center justify-center">
        <div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-label={title}
          tabIndex={-1}
          className={cx(
            'flex w-full max-w-[34rem] flex-col overflow-hidden rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface-elevated)] shadow-[var(--taali-shadow-strong)] focus:outline-none motion-safe:animate-[taali-dialog-in_180ms_ease-out]',
            panelClassName
          )}
        >
          <div className={cx('border-b border-[var(--taali-border-soft)] px-5 py-4', headerClassName)}>
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <h2 className="taali-display text-xl font-semibold tracking-tight">{title}</h2>
                {description ? <p className="mt-1 text-sm text-[var(--taali-muted)]">{description}</p> : null}
              </div>
              <Button
                type="button"
                onClick={onClose}
                variant="ghost"
                size="sm"
                aria-label="Close"
                className="!px-2 !py-2"
              >
                <X size={14} />
              </Button>
            </div>
          </div>
          <div className={cx('px-5 py-5', bodyClassName)}>
            {children}
          </div>
          {footer ? (
            <div className={cx('border-t border-[var(--taali-border-soft)] px-5 py-4', footerClassName)}>
              {footer}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
};
