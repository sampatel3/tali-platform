import React, { useEffect, useId, useMemo, useRef } from 'react';

import { PageLink } from './PageLink';
import './SectionNavigation.css';

const cx = (...parts) => parts.filter(Boolean).join(' ');

const SUPPORTED_TONES = new Set(['neutral', 'info', 'success', 'warning', 'danger']);

const shortHash = (value) => {
  let hash = 5381;
  for (const char of String(value || '')) {
    hash = ((hash << 5) + hash) ^ char.codePointAt(0);
  }
  return (hash >>> 0).toString(36);
};

const safeIdPart = (value, fallback = 'section') => {
  const raw = String(value ?? '').trim();
  const cleaned = raw
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '');

  if (cleaned && cleaned === raw) return cleaned;
  return `${cleaned || fallback}-${shortHash(raw || fallback)}`;
};

const normalizeTone = (tone) => {
  const candidate = String(tone || '').toLowerCase();
  return SUPPORTED_TONES.has(candidate) ? candidate : 'neutral';
};

const visibleItems = (items) => (
  (Array.isArray(items) ? items : []).filter(
    (item) => item && item.id != null && !item.hidden,
  )
);

const resolveActiveItem = (items, activeId) => {
  const visible = visibleItems(items);
  const requested = visible.find(
    (item) => String(item.id) === String(activeId) && !item.disabled,
  );
  return requested || visible.find((item) => !item.disabled) || null;
};

const groupDescriptor = (item) => {
  const group = item?.group;
  if (group == null || group === '') return { key: '__ungrouped__', label: '' };
  if (typeof group === 'object') {
    const label = group.label ?? group.id ?? '';
    return { key: String(group.id ?? label), label: String(label) };
  }
  return { key: String(group), label: String(group) };
};

// Group contiguous runs so the caller's item order remains authoritative.
const groupItems = (items) => {
  const groups = [];
  visibleItems(items).forEach((item) => {
    const descriptor = groupDescriptor(item);
    const previous = groups[groups.length - 1];
    if (!previous || previous.key !== descriptor.key) {
      groups.push({ ...descriptor, items: [item], sequence: groups.length });
      return;
    }
    previous.items.push(item);
  });
  return groups;
};

const markerFor = (item, index, variant) => {
  const Icon = item.Icon;
  if (Icon) return <Icon size={15} strokeWidth={1.9} aria-hidden="true" />;
  if (item.icon != null) return <span aria-hidden="true">{item.icon}</span>;
  if (variant === 'bar' || item.marker === false) return null;
  return <span aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>;
};

const badgeDescriptor = (item) => {
  if (item?.badge == null || item.badge === '') return null;
  if (typeof item.badge === 'object' && !React.isValidElement(item.badge)) {
    return {
      label: item.badge.label ?? item.badge.value ?? '',
      ariaLabel: item.badge.ariaLabel,
      tone: normalizeTone(item.badge.tone ?? item.tone),
    };
  }
  return {
    label: item.badge,
    ariaLabel: undefined,
    tone: normalizeTone(item.tone),
  };
};

const itemElementId = (prefix, itemId) => `${prefix}-item-${safeIdPart(itemId, 'item')}`;
const panelElementId = (prefix, itemId) => `${prefix}-panel-${safeIdPart(itemId, 'panel')}`;

const useSafeIdPrefix = (requestedPrefix) => {
  const generatedId = useId();
  return useMemo(() => {
    if (requestedPrefix != null && String(requestedPrefix).trim()) {
      return safeIdPart(requestedPrefix, 'focused-section');
    }
    return safeIdPart(`focused-section-${generatedId}`, 'focused-section');
  }, [generatedId, requestedPrefix]);
};

/**
 * Page-level section navigation. URL-backed items are links and controlled
 * items are buttons; this deliberately does not use ARIA tab semantics.
 */
export const FocusedSectionNav = ({
  items = [],
  activeId,
  onChange,
  ariaLabel = 'Page sections',
  idPrefix,
  className = '',
  variant = 'rail',
  sticky = true,
}) => {
  const resolvedPrefix = useSafeIdPrefix(idPrefix);
  const activeItem = resolveActiveItem(items, activeId);
  const activeKey = activeItem == null ? '' : String(activeItem.id);
  const groupedItems = groupItems(items);
  const navRef = useRef(null);
  const activeItemRef = useRef(null);
  let visibleIndex = 0;

  // On narrow screens the rail becomes an overflow strip. Keep a selection
  // restored from a URL or browser history inside that strip's viewport.
  useEffect(() => {
    const nav = navRef.current;
    const active = activeItemRef.current;
    if (!nav || !active) return;

    const navRect = nav.getBoundingClientRect();
    const activeRect = active.getBoundingClientRect();
    const navWidth = navRect.width || (navRect.right - navRect.left);
    if (navWidth <= 0) return;
    if (activeRect.left >= navRect.left && activeRect.right <= navRect.right) return;

    const activeWidth = activeRect.width || (activeRect.right - activeRect.left);
    const delta = activeRect.left - navRect.left - ((navWidth - activeWidth) / 2);
    const left = Math.max(0, nav.scrollLeft + delta);
    const reduceMotion = typeof window !== 'undefined'
      && typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    if (typeof nav.scrollTo === 'function') {
      nav.scrollTo({ left, behavior: reduceMotion ? 'auto' : 'smooth' });
    } else {
      nav.scrollLeft = left;
    }
  }, [activeKey]);

  const renderItem = (item) => {
    const itemIndex = visibleIndex;
    visibleIndex += 1;
    const active = String(item.id) === activeKey;
    const disabled = Boolean(item.disabled);
    const tone = normalizeTone(item.tone);
    const marker = markerFor(item, itemIndex, variant);
    const badge = badgeDescriptor(item);
    const itemClassName = cx(
      'focused-section-nav__item',
      marker ? 'has-marker' : '',
      active ? 'is-active' : '',
      disabled ? 'is-disabled' : '',
      item.tone ? `tone-${tone}` : '',
      item.className,
    );
    const content = (
      <>
        {marker ? <span className="focused-section-nav__marker">{marker}</span> : null}
        <span className="focused-section-nav__copy">
          <span className="focused-section-nav__label">{item.label}</span>
          {item.description ? (
            <span className="focused-section-nav__description">{item.description}</span>
          ) : null}
        </span>
        {(item.meta != null && item.meta !== '') || badge ? (
          <span className="focused-section-nav__trailing">
            {item.meta != null && item.meta !== '' ? (
              <span className="focused-section-nav__meta">{item.meta}</span>
            ) : null}
            {badge ? (
              <span
                className={`focused-section-nav__badge focused-section-nav__badge--${badge.tone}`}
                aria-label={badge.ariaLabel}
              >
                {badge.label}
              </span>
            ) : null}
          </span>
        ) : null}
      </>
    );
    const sharedProps = {
      id: itemElementId(resolvedPrefix, item.id),
      ref: active ? activeItemRef : undefined,
      className: itemClassName,
      'aria-current': active ? 'page' : undefined,
      'aria-disabled': disabled || undefined,
      'data-section-id': String(item.id),
      'data-tone': item.tone ? tone : undefined,
    };

    if (item.to && !disabled) {
      return (
        <PageLink
          key={item.id}
          {...sharedProps}
          to={item.to}
          onClick={item.onClick}
        >
          {content}
        </PageLink>
      );
    }

    return (
      <button
        key={item.id}
        {...sharedProps}
        type="button"
        disabled={disabled}
        aria-pressed={active}
        onClick={(event) => {
          if (disabled) return;
          item.onClick?.(event);
          if (!event.defaultPrevented) onChange?.(item.id);
        }}
      >
        {content}
      </button>
    );
  };

  return (
    <nav
      ref={navRef}
      className={cx(
        'focused-section-nav',
        `focused-section-nav--${variant}`,
        sticky ? 'is-sticky' : '',
        className,
      )}
      aria-label={ariaLabel}
    >
      <div className="focused-section-nav__list">
        {groupedItems.map((group) => (
          <div
            key={`${group.key}-${group.sequence}`}
            className="focused-section-nav__group"
            role={group.label ? 'group' : undefined}
            aria-label={group.label || undefined}
          >
            {group.label ? (
              <div className="focused-section-nav__group-label" aria-hidden="true">
                {group.label}
              </div>
            ) : null}
            <div className="focused-section-nav__group-items">
              {group.items.map(renderItem)}
            </div>
          </div>
        ))}
      </div>
    </nav>
  );
};

/**
 * Couples a section index to one labelled content region. The parent owns
 * URL/state and the active section's rendering lifecycle.
 */
export const FocusedSectionLayout = ({
  items = [],
  activeId,
  onChange,
  ariaLabel = 'Page sections',
  idPrefix,
  className = '',
  navClassName = '',
  contentClassName = '',
  variant = 'rail',
  sticky = true,
  children,
}) => {
  const resolvedPrefix = useSafeIdPrefix(idPrefix);
  const activeItem = resolveActiveItem(items, activeId);
  const activeItemId = activeItem?.id;
  const labelledBy = activeItem ? itemElementId(resolvedPrefix, activeItemId) : undefined;

  return (
    <div className={cx('focused-sections', `focused-sections--${variant}`, className)}>
      <FocusedSectionNav
        items={items}
        activeId={activeItemId}
        onChange={onChange}
        ariaLabel={ariaLabel}
        idPrefix={resolvedPrefix}
        className={navClassName}
        variant={variant}
        sticky={sticky}
      />
      <div
        id={activeItem ? panelElementId(resolvedPrefix, activeItemId) : `${resolvedPrefix}-panel`}
        className={cx('focused-sections__content', contentClassName)}
        role="region"
        aria-labelledby={labelledBy}
        aria-label={labelledBy ? undefined : ariaLabel}
      >
        {children}
      </div>
    </div>
  );
};

export default FocusedSectionLayout;
