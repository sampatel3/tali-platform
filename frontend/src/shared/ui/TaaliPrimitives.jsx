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
import { Check, ChevronDown, X } from 'lucide-react';
import {
  AnimatePresence,
  MotionSpinner,
  backdropVariants,
  createSheetVariants,
  dialogVariants,
  m,
} from '../motion';

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
  soft: 'taali-btn-soft',
  danger: 'taali-btn-danger',
  agent: 'taali-btn-agent',
  inverse: 'taali-btn-inverse',
};

const BUTTON_SIZE_CLASS = {
  xs: 'taali-btn-xs',
  sm: 'taali-btn-sm',
  md: 'taali-btn-md',
  lg: 'taali-btn-lg',
};

export const Button = React.forwardRef(function Button({
  className = '',
  variant = 'secondary',
  size = 'md',
  as: As = 'button',
  loading = false,
  loadingLabel,
  iconOnly = false,
  fullWidth = false,
  disabled,
  type,
  children,
  ...props
}, ref) {
  const componentProps = { ...props };
  const isNativeButton = As === 'button' || As?.rendersNativeButton === true;
  const isDisabled = Boolean(disabled || loading);

  if (isNativeButton) {
    componentProps.type = type ?? 'button';
  } else if (type !== undefined) {
    componentProps.type = type;
  }

  if (isNativeButton && (disabled !== undefined || loading)) {
    componentProps.disabled = isDisabled;
  } else if (!isNativeButton && isDisabled) {
    componentProps['aria-disabled'] = true;
    componentProps.tabIndex = -1;
    componentProps.onClick = (event) => {
      event.preventDefault();
      event.stopPropagation();
    };
  }

  if (loading) {
    componentProps['aria-busy'] = true;
  }

  return (
    <As
      {...componentProps}
      ref={ref}
      className={cx(
        'taali-btn inline-flex items-center justify-center gap-1.5',
        BUTTON_VARIANT_CLASS[variant] || BUTTON_VARIANT_CLASS.secondary,
        BUTTON_SIZE_CLASS[size] || BUTTON_SIZE_CLASS.md,
        iconOnly ? 'taali-btn-icon-only' : '',
        fullWidth ? 'taali-btn-full' : '',
        className
      )}
    >
      {loading ? (
        <MotionSpinner className="taali-btn-spinner" size={17} />
      ) : null}
      {loading ? loadingLabel ?? children : children}
    </As>
  );
});

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
  const minWidth = Math.max(160, Math.floor(rect.width));
  const maxLeft = Math.max(
    FLOATING_MENU_VIEWPORT_PADDING,
    viewportWidth - minWidth - FLOATING_MENU_VIEWPORT_PADDING
  );
  const left = _clamp(Math.floor(rect.left), FLOATING_MENU_VIEWPORT_PADDING, maxLeft);
  // Grow to fit the longest option (so full role names aren't truncated) but
  // never past the viewport edge. CSS `width: max-content` sizes the menu
  // between this min (at least the trigger width) and max.
  const maxWidth = Math.max(minWidth, viewportWidth - left - FLOATING_MENU_VIEWPORT_PADDING);
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
    minWidth: `${minWidth}px`,
    maxWidth: `${maxWidth}px`,
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

const _optionText = (node) => {
  const c = node?.props?.children;
  if (typeof c === 'string' || typeof c === 'number') return String(c);
  if (Array.isArray(c)) {
    return c.map((part) => (typeof part === 'string' || typeof part === 'number' ? part : '')).join('');
  }
  return String(node?.props?.value ?? '');
};

// `Select` keeps the familiar native-<select> API — a `value`, an
// `onChange` that receives an event with `target.value`, and `<option>`
// children — but renders the styled, cross-browser portal menu instead of
// the OS popup, so the *open* state matches the design system everywhere
// (not just the closed control). It is a thin adapter over <SingleSelect/>
// (defined below); existing call sites need no changes.
export const Select = ({
  className = '',
  children,
  value,
  onChange,
  disabled = false,
  placeholder,
  bare = false,
  inline = false,
  triggerClassName = '',
  title,
  'aria-label': ariaLabel,
}) => {
  const options = React.Children.toArray(children)
    .filter((child) => React.isValidElement(child) && child.type === 'option')
    .map((child) => ({
      value: child.props.value,
      label: _optionText(child),
      disabled: Boolean(child.props.disabled),
    }));
  return (
    <SingleSelect
      className={className}
      triggerClassName={cx(bare ? 'taali-select-trigger-bare' : '', triggerClassName)}
      // `bare` (inline filter-chips) and `inline` (compact toolbar selects)
      // both want a content-width shell so they don't stretch to fill a flex
      // toolbar; form-field selects keep the default full-width shell.
      shellClassName={(bare || inline) ? 'taali-select-shell-inline' : ''}
      options={options}
      value={value}
      disabled={disabled}
      placeholder={placeholder}
      ariaLabel={ariaLabel}
      title={title}
      onChange={(next) => {
        if (typeof onChange === 'function') onChange({ target: { value: next } });
      }}
    />
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
        <span className={cx('taali-select-chevron', open ? 'rotate-180' : '')} aria-hidden>
          <ChevronDown size={16} />
        </span>
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
                  className="taali-text-btn taali-multiselect-action"
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


// Single-select dropdown with the same floating-menu shell as MultiSelect.
// Use this when a `<select>` would render as a native browser popup —
// SingleSelect renders the option list as a portaled <div> menu so it
// styles consistently across browsers and inherits the rest of the
// `.taali-select-*` design tokens.
export const SingleSelect = ({
  className = '',
  options = [],
  value,
  onChange,
  disabled = false,
  placeholder = 'Select…',
  ariaLabel,
  renderOption,
  renderValue,
  triggerClassName = '',
  shellClassName = '',
  title,
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
      raw: option,
    })) : []),
    [options]
  );
  const currentKey = value === undefined || value === null ? '' : String(value);
  const currentOption = normalizedOptions.find((option) => option.value === currentKey) || null;
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const optionRefs = useRef([]);
  const floatingMenuStyle = useFloatingMenuStyle(open, triggerRef);
  const resolvedMenuStyle = floatingMenuStyle || _buildFloatingMenuStyle(triggerRef.current);

  const firstEnabledIndex = useCallback(
    (from, direction) => {
      const count = normalizedOptions.length;
      if (count === 0) return -1;
      for (let step = 0; step < count; step += 1) {
        const index = ((from + direction * step) % count + count) % count;
        if (!normalizedOptions[index].disabled) return index;
      }
      return -1;
    },
    [normalizedOptions],
  );

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
      if (event.key === 'Escape') setOpen(false);
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

  // When the menu opens, seed the active option (selected, else first
  // enabled) and move keyboard focus into it so arrow navigation works.
  useEffect(() => {
    if (!open) {
      setActiveIndex(-1);
      return;
    }
    const selectedIndex = normalizedOptions.findIndex((option) => option.value === currentKey);
    const next = selectedIndex >= 0 && !normalizedOptions[selectedIndex].disabled
      ? selectedIndex
      : firstEnabledIndex(0, 1);
    setActiveIndex(next);
  }, [open, currentKey, normalizedOptions, firstEnabledIndex]);

  useEffect(() => {
    if (!open || activeIndex < 0) return;
    optionRefs.current[activeIndex]?.focus();
  }, [open, activeIndex]);

  const choose = (next) => {
    if (typeof onChange === 'function') onChange(next);
    setOpen(false);
    triggerRef.current?.focus();
  };

  const onMenuKeyDown = (event) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveIndex((current) => firstEnabledIndex(current < 0 ? 0 : current + 1, 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex((current) => firstEnabledIndex(current < 0 ? normalizedOptions.length - 1 : current - 1, -1));
    } else if (event.key === 'Home') {
      event.preventDefault();
      setActiveIndex(firstEnabledIndex(0, 1));
    } else if (event.key === 'End') {
      event.preventDefault();
      setActiveIndex(firstEnabledIndex(normalizedOptions.length - 1, -1));
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      const option = normalizedOptions[activeIndex];
      if (option && !option.disabled) choose(option.value);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    } else if (event.key === 'Tab') {
      setOpen(false);
    }
  };

  return (
    <div ref={rootRef} className={cx('taali-select-shell', shellClassName)}>
      <button
        ref={triggerRef}
        type="button"
        className={cx('taali-select-trigger', triggerClassName, className)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        aria-controls={`taali-singleselect-listbox-${controlId}`}
        title={title}
        onClick={() => { if (!disabled) setOpen((current) => !current); }}
        onKeyDown={(event) => {
          if (disabled) return;
          if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            setOpen(true);
          }
          if (event.key === 'Escape') setOpen(false);
        }}
      >
        <span className={cx('taali-select-value', !currentOption ? 'taali-select-value-placeholder' : '')}>
          {currentOption
            ? (typeof renderValue === 'function' ? renderValue(currentOption.raw) : currentOption.label)
            : placeholder}
        </span>
        <span className={cx('taali-select-chevron', open ? 'rotate-180' : '')} aria-hidden>
          <ChevronDown size={16} />
        </span>
      </button>
      {open && resolvedMenuStyle && typeof document !== 'undefined'
        ? createPortal(
            <div
              ref={menuRef}
              className="taali-select-menu"
              style={resolvedMenuStyle}
              role="listbox"
              tabIndex={-1}
              id={`taali-singleselect-listbox-${controlId}`}
              aria-activedescendant={
                activeIndex >= 0
                  ? `${controlId}-option-${normalizedOptions[activeIndex]?.value}`
                  : undefined
              }
              onKeyDown={onMenuKeyDown}
            >
              {normalizedOptions.map((option, index) => {
                const isSelected = option.value === currentKey;
                return (
                  <button
                    key={`${controlId}-${option.value}`}
                    id={`${controlId}-option-${option.value}`}
                    ref={(node) => { optionRefs.current[index] = node; }}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    tabIndex={index === activeIndex ? 0 : -1}
                    className={cx(
                      'taali-select-option',
                      isSelected ? 'taali-select-option-selected' : ''
                    )}
                    disabled={option.disabled}
                    onClick={() => { if (!option.disabled) choose(option.value); }}
                    onMouseEnter={() => { if (!option.disabled) setActiveIndex(index); }}
                  >
                    <span className="truncate">
                      {typeof renderOption === 'function' ? renderOption(option.raw) : option.label}
                    </span>
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

export const Spinner = ({ size = 24, className = '', label }) => (
  <MotionSpinner size={size} className={className} label={label} />
);

// PageLoader — the one shared cold-load state: a centred circular spinner.
// Every page/section load in the app uses this so the loading experience
// stays consistent. `minHeight` reserves vertical space so the spinner sits
// mid-view and the swap-in to real content doesn't jump.
export const PageLoader = ({ size = 28, minHeight = '17.5rem', label = 'Loading…', className = '' }) => (
  <div
    className={cx('flex items-center justify-center', className)}
    style={{ minHeight }}
    role="status"
    aria-label={label}
  >
    <Spinner size={size} />
  </div>
);

/**
 * Controlled tabs for switching between local content panels.
 *
 * Arrow keys use automatic activation because this primitive is intended for
 * compact panels that are already available in the current task context.
 */
export const TabBar = ({
  tabs = [],
  activeTab,
  onChange,
  ariaLabel = 'Tabs',
  className = '',
  density = 'default',
  variant = 'default',
}) => {
  const tabRefs = useRef(new Map());
  const enabledTabs = tabs.filter((tab) => !tab.disabled);
  const activeTabIsEnabled = enabledTabs.some((tab) => tab.id === activeTab);
  const fallbackTabId = enabledTabs[0]?.id;

  const activateAndFocus = (tab) => {
    if (!tab || tab.disabled) return;
    onChange?.(tab.id);
    tabRefs.current.get(tab.id)?.focus();
  };

  const handleKeyDown = (event, currentTab) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    if (enabledTabs.length === 0) return;

    event.preventDefault();
    const currentIndex = Math.max(
      0,
      enabledTabs.findIndex((tab) => tab.id === currentTab.id),
    );
    let nextIndex = currentIndex;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = enabledTabs.length - 1;
    if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % enabledTabs.length;
    if (event.key === 'ArrowLeft') {
      nextIndex = (currentIndex - 1 + enabledTabs.length) % enabledTabs.length;
    }
    activateAndFocus(enabledTabs[nextIndex]);
  };

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      aria-orientation="horizontal"
      className={cx(
        'taali-tabbar',
        `taali-tabbar--${variant}`,
        density === 'compact' ? 'taali-tabbar--compact' : '',
        className,
      )}
    >
      {tabs.map((tab) => {
        const isActive = activeTab === tab.id;
        const isDisabled = Boolean(tab.disabled);
        const isTabStop = !isDisabled
          && (activeTabIsEnabled ? isActive : tab.id === fallbackTabId);
        return (
          <button
            key={tab.id}
            ref={(node) => {
              if (node) tabRefs.current.set(tab.id, node);
              else tabRefs.current.delete(tab.id);
            }}
            role="tab"
            aria-label={tab.ariaLabel || (
              typeof tab.label === 'string' && tab.meta != null
                ? `${tab.label}, ${tab.meta}`
                : undefined
            )}
            aria-selected={isActive}
            aria-disabled={isDisabled || undefined}
            aria-controls={tab.panelId}
            id={tab.tabId || tab.id}
            type="button"
            disabled={isDisabled}
            tabIndex={isTabStop ? 0 : -1}
            onClick={() => activateAndFocus(tab)}
            onKeyDown={(event) => handleKeyDown(event, tab)}
            className={cx(
              'taali-tabbar__tab',
              isActive ? 'is-active' : '',
              tab.className,
            )}
          >
            <span className="taali-tabbar__label">{tab.label}</span>
            {tab.meta != null ? <span className="taali-tabbar__meta">{tab.meta}</span> : null}
          </button>
        );
      })}
    </div>
  );
};

/**
 * A compact single-choice control for modes and filters. These are buttons,
 * not content tabs, so every enabled option remains directly keyboardable.
 */
export const SegmentedControl = ({
  options = [],
  value,
  onChange,
  ariaLabel = 'Options',
  className = '',
  density = 'default',
  fullWidth = false,
}) => (
  <div
    role="group"
    aria-label={ariaLabel}
    className={cx(
      'taali-segmented-control',
      density === 'compact' ? 'taali-segmented-control--compact' : '',
      fullWidth ? 'taali-segmented-control--full' : '',
      className,
    )}
  >
    {options.map((option) => {
      const isActive = value === option.value;
      const isDisabled = Boolean(option.disabled);
      return (
        <button
          key={option.value}
          type="button"
          aria-label={option.ariaLabel || (
            typeof option.label === 'string' && option.meta != null
              ? `${option.label}, ${option.meta}`
              : undefined
          )}
          aria-pressed={isActive}
          disabled={isDisabled}
          title={option.title}
          className={cx(
            'taali-segmented-control__option',
            isActive ? 'is-active' : '',
            option.className,
          )}
          onClick={() => {
            if (!isDisabled) onChange?.(option.value);
          }}
        >
          <span className="taali-segmented-control__label">{option.label}</span>
          {option.meta != null ? (
            <span className="taali-segmented-control__meta">{option.meta}</span>
          ) : null}
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
  const panelVariants = useMemo(() => createSheetVariants(side), [side]);

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

  return (
    <AnimatePresence>
      {open ? (
        <m.div
          key="sheet-overlay"
          variants={backdropVariants}
          initial="hidden"
          animate="visible"
          exit="exit"
          className={cx('fixed inset-0 z-50 bg-[rgba(12,18,32,0.38)] backdrop-blur-sm', overlayClassName)}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) onClose();
          }}
        >
          <m.div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label={title}
            tabIndex={-1}
            variants={panelVariants}
            initial="hidden"
            animate="visible"
            exit="exit"
            className={cx(
              'absolute inset-x-0 bottom-0 flex max-h-[92vh] flex-col overflow-hidden border border-[var(--taali-border-soft)] bg-[var(--taali-surface-elevated)] shadow-[var(--taali-shadow-strong)] focus:outline-none md:inset-y-3 md:h-[calc(100%-1.5rem)] md:max-h-none md:w-[42.5rem] md:rounded-[var(--taali-radius-panel)]',
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
          </m.div>
        </m.div>
      ) : null}
    </AnimatePresence>
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
  initialFocusRef = null,
}) => {
  const panelRef = useRef(null);
  const previousFocusRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;

    previousFocusRef.current = document.activeElement;
    lockBodyScrollForSheet();

    const focusables = panelRef.current?.querySelectorAll(FOCUSABLE_SELECTOR);
    if (initialFocusRef?.current && typeof initialFocusRef.current.focus === 'function') {
      initialFocusRef.current.focus();
    } else if (focusables && focusables.length > 0) {
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
  }, [initialFocusRef, onClose, open]);

  return (
    <AnimatePresence>
      {open ? (
        <m.div
          key="dialog-overlay"
          variants={backdropVariants}
          initial="hidden"
          animate="visible"
          exit="exit"
          className={cx('fixed inset-0 z-[60] bg-[rgba(12,18,32,0.42)] px-4 py-6 backdrop-blur-sm', overlayClassName)}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) onClose();
          }}
        >
          <div className="flex min-h-full items-center justify-center">
            <m.div
              ref={panelRef}
              role="dialog"
              aria-modal="true"
              aria-label={title}
              tabIndex={-1}
              variants={dialogVariants}
              initial="hidden"
              animate="visible"
              exit="exit"
              className={cx(
                'flex w-full max-w-[34rem] flex-col overflow-hidden rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface-elevated)] shadow-[var(--taali-shadow-strong)] focus:outline-none',
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
            </m.div>
          </div>
        </m.div>
      ) : null}
    </AnimatePresence>
  );
};
