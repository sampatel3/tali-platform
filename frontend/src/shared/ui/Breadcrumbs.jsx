import React from 'react';
import { ChevronRight } from 'lucide-react';

import { PageLink } from './PageLink';

// Render a horizontal trail. Items are { label, page?, options?, to? }.
// The last item is the current page — non-clickable, rendered as text.
// Earlier items render as ctrl+click-friendly PageLinks.
export const Breadcrumbs = ({ items, className = '' }) => {
  if (!Array.isArray(items) || items.length === 0) return null;
  return (
    <nav
      aria-label="Breadcrumb"
      className={`mb-3 flex flex-wrap items-center gap-1 text-xs text-[var(--taali-muted)] ${className}`.trim()}
    >
      {items.map((item, index) => {
        const isLast = index === items.length - 1;
        const key = `${item.label}-${index}`;
        if (isLast || (!item.page && !item.to)) {
          return (
            <span
              key={key}
              className="font-medium text-[var(--taali-text)]"
              aria-current={isLast ? 'page' : undefined}
            >
              {item.label}
            </span>
          );
        }
        return (
          <React.Fragment key={key}>
            <PageLink
              page={item.page}
              options={item.options}
              to={item.to}
              className="rounded px-1 py-0.5 transition-colors hover:text-[var(--taali-text)] hover:bg-[var(--taali-surface-hover,rgba(0,0,0,0.04))]"
            >
              {item.label}
            </PageLink>
            <ChevronRight size={12} aria-hidden="true" className="text-[var(--taali-muted)]" />
          </React.Fragment>
        );
      })}
    </nav>
  );
};

// Common header layout used on detail pages: breadcrumbs on the left,
// optional actions (e.g. CopyLinkButton) on the right.
export const BreadcrumbsRow = ({ items, actions }) => (
  <div className="page" style={{ paddingTop: 8, paddingBottom: 0 }}>
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
      <Breadcrumbs items={items} className="mb-0" />
      {actions}
    </div>
  </div>
);

export default Breadcrumbs;
