import React from 'react';
import { Link, useInRouterContext } from 'react-router-dom';
import { pathForPage } from '../../app/routing';

// Drop-in replacement for `<button onClick={() => onNavigate(page, options)}>`
// that renders a real <a href> so ctrl/cmd/middle-click open in a new tab.
// Pass `page` + `options` exactly like `onNavigate` — the href is derived
// from pathForPage(). onClick stays available for side effects (e.g.
// closing a menu, updating filter state) and fires after React Router's
// SPA navigation kicks in.
//
// When mounted outside a Router (notably in tests that render a single
// component without MemoryRouter), falls back to a plain <a href> so the
// component is still ctrl/cmd+click-friendly even without SPA routing.
export const PageLink = React.forwardRef(function PageLink(
  { page, options, to, onClick, children, className, style, ...rest },
  ref,
) {
  const inRouter = useInRouterContext();
  const href = to ?? (page ? pathForPage(page, options) : null) ?? '#';
  if (!inRouter) {
    return (
      <a
        ref={ref}
        href={href}
        onClick={onClick}
        className={className}
        style={style}
        {...rest}
      >
        {children}
      </a>
    );
  }
  return (
    <Link
      ref={ref}
      to={href}
      onClick={onClick}
      className={className}
      style={style}
      {...rest}
    >
      {children}
    </Link>
  );
});

export default PageLink;
