import React, { useEffect, useRef } from 'react';
import { X } from 'lucide-react';

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export const cx = (...parts) => parts.filter(Boolean).join(' ');

export const PageContainer = ({ className = '', children }) => (
  <div className={cx('taali-page', className)}>{children}</div>
);

export const PageHeader = ({ title, subtitle, actions, className = '', children }) => (
  <header className={cx('taali-page-header', className)}>
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div>
        {title ? <h1 className="text-3xl font-bold tracking-tight">{title}</h1> : null}
        {subtitle ? <p className="mt-1 text-sm text-gray-600">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
    {children ? <div className="mt-4">{children}</div> : null}
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
};

export const Badge = ({ variant = 'muted', className = '', children }) => (
  <span className={cx('taali-badge', BADGE_VARIANT_CLASS[variant] || BADGE_VARIANT_CLASS.muted, className)}>
    {children}
  </span>
);

export const EmptyState = ({ title, description, action, className = '' }) => (
  <div className={cx('taali-empty-state px-5 py-12 text-center', className)}>
    <p className="text-lg font-semibold text-gray-900">{title}</p>
    {description ? <p className="mt-1 text-sm text-gray-600">{description}</p> : null}
    {action ? <div className="mt-4 flex justify-center">{action}</div> : null}
  </div>
);

export const TableShell = ({ className = '', children }) => (
  <div className={cx('taali-table-shell', className)}>
    {children}
  </div>
);

export const Sheet = ({ open, onClose, title, description, children, footer }) => {
  const panelRef = useRef(null);
  const previousFocusRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;

    const previousOverflow = document.body.style.overflow;
    previousFocusRef.current = document.activeElement;
    document.body.style.overflow = 'hidden';

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
      document.body.style.overflow = previousOverflow;
      if (previousFocusRef.current && typeof previousFocusRef.current.focus === 'function') {
        previousFocusRef.current.focus();
      }
    };
  }, [onClose, open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/55"
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
        className="absolute inset-x-0 bottom-0 max-h-[92vh] border-t-2 border-[var(--taali-border)] bg-[var(--taali-surface)] focus:outline-none md:inset-y-0 md:right-0 md:left-auto md:h-full md:max-h-none md:w-[640px] md:border-t-0 md:border-l-2"
      >
        <div className="sticky top-0 z-10 border-b-2 border-[var(--taali-border)] bg-[var(--taali-surface)] px-5 py-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-xl font-bold tracking-tight">{title}</h2>
              {description ? <p className="mt-1 text-sm text-gray-600">{description}</p> : null}
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
        <div className="overflow-y-auto px-5 py-5" style={{ maxHeight: 'calc(92vh - 150px)' }}>
          {children}
        </div>
        <div className="sticky bottom-0 border-t-2 border-[var(--taali-border)] bg-[var(--taali-surface)] px-5 py-4">
          {footer}
        </div>
      </div>
    </div>
  );
};
