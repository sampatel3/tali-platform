import React, {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown, Loader2, X } from 'lucide-react';

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
  xs: 'px-3 py-1.5 text-xs',
  sm: 'px-4 py-2 text-sm',
  md: 'px-5 py-2.5 text-sm',
  lg: 'px-6 py-3 text-base',
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

const FLOATING_MENU_GAP = 6;
const FLOATING_MENU_VIEWPORT_PADDING = 8;
const FLOATING_MENU_MIN_HEIGHT = 140;
const FLOATING_MENU_MAX_HEIGHT = 272;

const _clamp = (value, min, max) => Math.min(max, Math.max(min, value));

const _buildFloatingMenuStyle = (anchorElement) => {
  if (!anchorElement || typeof window === 'undefined') return null;
  const rect = anchorElement.getBoundingClientRect();
  if (!Number.isFinite(rect.width) || rect.width <= 0) return null;

  const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
  const spaceBelow = viewportHeight - rect.bottom - FLOATING_MENU_VIEWPORT_PADDING;
  const spaceAbove = rect.top - FLOATING_MENU_VIEWPORT_PADDING;
  const openUpward = spaceBelow < FLOATING_MENU_MIN_HEIGHT && spaceAbove > spaceBelow;
  const availableHeight = openUpward
    ? spaceAbove - FLOATING_MENU_GAP
    : spaceBelow - FLOATING_MENU_GAP;
  const maxHeight = _clamp(
    Math.floor(availableHeight),
    FLOATING_MENU_MIN_HEIGHT,
    FLOATING_MENU_MAX_HEIGHT
  );
  const width = Math.max(120, Math.floor(rect.width));
  const maxLeft = Math.max(
    FLOATING_MENU_VIEWPORT_PADDING,
    viewportWidth - width - FLOATING_MENU_VIEWPORT_PADDING
  );
  const left = _clamp(Math.floor(rect.left), FLOATING_MENU_VIEWPORT_PADDING, maxLeft);
  const top = openUpward
    ? Math.max(
        FLOATING_MENU_VIEWPORT_PADDING,
        Math.floor(rect.top - FLOATING_MENU_GAP - maxHeight)
      )
    : Math.min(
        Math.max(
          FLOATING_MENU_VIEWPORT_PADDING,
          viewportHeight - FLOATING_MENU_VIEWPORT_PADDING - maxHeight
        ),
        Math.floor(rect.bottom + FLOATING_MENU_GAP)
      );

  return {
    left: `${left}px`,
    top: `${top}px`,
    width: `${width}px`,
    maxHeight: `${maxHeight}px`,
    zIndex: 1200,
  };
};

const useFloatingMenuStyle = (open, triggerRef) => {
  const [style, setStyle] = useState(null);
  const updateStyle = useCallback(() => {
    setStyle(_buildFloatingMenuStyle(triggerRef.current));
  }, [triggerRef]);

  useLayoutEffect(() => {
    if (!open) {
      setStyle(null);
      return undefined;
    }

    updateStyle();
    const onViewportChange = () => {
      window.requestAnimationFrame(updateStyle);
    };
    window.addEventListener('resize', onViewportChange);
    window.addEventListener('scroll', onViewportChange, true);
    return () => {
      window.removeEventListener('resize', onViewportChange);
      window.removeEventListener('scroll', onViewportChange, true);
    };
  }, [open, updateStyle]);

  return style;
};

export const Select = ({
  className = '',
  children,
  disabled = false,
  ...props
}) => {
  return (
    <div className="taali-select-shell">
      <select
        className={cx('taali-select appearance-none pr-9', className)}
        disabled={disabled}
        {...props}
      >
        {children}
      </select>
      <ChevronDown size={16} className="taali-select-icon" aria-hidden />
    </div>
  );
};

const _orderMultiValues = (values, options) => {
  const selected = new Set(values.map((item) => String(item)));
  return options
    .map((option) => String(option.value))
    .filter((optionValue) => selected.has(optionValue));
};

const _multiSelectSummary = (selectedValues, options, emptyLabel) => {
  if (!selectedValues.length) return emptyLabel;
  const labels = options
    .filter((option) => selectedValues.includes(String(option.value)))
    .map((option) => option.label);
  if (!labels.length) return emptyLabel;
  if (labels.length <= 2) return labels.join(', ');
  return `${labels[0]}, ${labels[1]} +${labels.length - 2}`;
};

export const MultiSelect = ({
  className = '',
  options = [],
  value = [],
  onChange,
  disabled = false,
  emptyLabel = 'All',
}) => {
  const controlId = useId();
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  const normalizedOptions = useMemo(
    () => (Array.isArray(options) ? options.map((option) => ({
      value: String(option?.value ?? ''),
      label: String(option?.label ?? option?.value ?? ''),
      disabled: Boolean(option?.disabled),
    })) : []),
    [options]
  );
  const selectedValues = useMemo(
    () => _orderMultiValues(Array.isArray(value) ? value : [], normalizedOptions),
    [normalizedOptions, value]
  );
  const [open, setOpen] = useState(false);
  const floatingMenuStyle = useFloatingMenuStyle(open, triggerRef);
  const resolvedMenuStyle = floatingMenuStyle || _buildFloatingMenuStyle(triggerRef.current);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event) => {
      if (
        !rootRef.current?.contains(event.target)
        && !menuRef.current?.contains(event.target)
      ) {
        setOpen(false);
      }
    };
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('touchstart', onPointerDown, { passive: true });
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('touchstart', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const allEnabledOptionValues = normalizedOptions
    .filter((option) => !option.disabled)
    .map((option) => option.value);
  const allEnabledSelected = allEnabledOptionValues.length > 0
    && allEnabledOptionValues.every((optionValue) => selectedValues.includes(optionValue));

  const applyValues = (nextValues) => {
    if (typeof onChange !== 'function') return;
    onChange(_orderMultiValues(nextValues, normalizedOptions));
  };

  const toggleValue = (nextValue) => {
    const key = String(nextValue);
    if (selectedValues.includes(key)) {
      applyValues(selectedValues.filter((item) => item !== key));
      return;
    }
    applyValues([...selectedValues, key]);
  };

  return (
    <div ref={rootRef} className="taali-select-shell">
      <button
        ref={triggerRef}
        type="button"
        className={cx('taali-select-trigger', className)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`taali-multiselect-listbox-${controlId}`}
        onClick={() => {
          if (!disabled) setOpen((current) => !current);
        }}
        onKeyDown={(event) => {
          if (disabled) return;
          if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            setOpen(true);
          }
          if (event.key === 'Escape') setOpen(false);
        }}
      >
        <span className={cx('taali-select-value', !selectedValues.length ? 'taali-select-value-placeholder' : '')}>
          {_multiSelectSummary(selectedValues, normalizedOptions, emptyLabel)}
        </span>
        <ChevronDown size={16} className={cx('taali-select-chevron', open ? 'rotate-180' : '')} aria-hidden />
      </button>
      {open && resolvedMenuStyle && typeof document !== 'undefined'
        ? createPortal(
            <div
              ref={menuRef}
              className="taali-select-menu"
              style={resolvedMenuStyle}
              role="listbox"
              id={`taali-multiselect-listbox-${controlId}`}
              aria-multiselectable
            >
              <div className="taali-multiselect-actions">
                <button
                  type="button"
                  className="taali-multiselect-action"
                  onClick={() => {
                    if (allEnabledSelected) {
                      applyValues([]);
                    } else {
                      applyValues(allEnabledOptionValues);
                    }
                  }}
                >
                  {allEnabledSelected ? 'Clear all' : 'Select all'}
                </button>
              </div>
              {normalizedOptions.map((option) => {
                const isSelected = selectedValues.includes(option.value);
                return (
                  <button
                    key={`${controlId}-${option.value}`}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    className={cx(
                      'taali-select-option',
                      isSelected ? 'taali-select-option-selected' : ''
                    )}
                    disabled={option.disabled}
                    onClick={() => {
                      if (!option.disabled) toggleValue(option.value);
                    }}
                  >
                    <span className="truncate">{option.label}</span>
                    {isSelected ? <Check size={14} aria-hidden /> : null}
                  </button>
                );
              })}
            </div>,
            document.body
          )
        : null}
    </div>
  );
};

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
      const isDisabled = Boolean(tab.disabled);
      return (
        <button
          key={tab.id}
          role="tab"
          aria-selected={isActive}
          aria-disabled={isDisabled}
          aria-controls={tab.panelId}
          id={tab.id}
          type="button"
          disabled={isDisabled}
          tabIndex={isDisabled ? -1 : undefined}
          onClick={() => {
            if (!isDisabled) onChange(tab.id);
          }}
          className={cx(
            'taali-btn inline-flex items-center justify-center',
            density === 'compact'
              ? 'px-3 py-1.5 text-xs'
              : 'px-4 py-2 text-sm',
            isDisabled
              ? 'taali-btn-ghost border-transparent text-[var(--taali-muted)] !opacity-70'
              : isActive
              ? 'taali-btn-secondary border-[var(--taali-border-soft)] text-[var(--taali-text)] shadow-[var(--taali-shadow-soft)]'
              : 'taali-btn-ghost border-transparent text-[var(--taali-muted)] hover:border-[var(--taali-border-soft)] hover:text-[var(--taali-text)]'
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
