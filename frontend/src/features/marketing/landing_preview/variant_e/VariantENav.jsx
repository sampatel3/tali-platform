import React, { useEffect, useState } from 'react';

// ---------------------------------------------------------------------------
// Sticky marketing nav for variant E. Transparent at the top; on scroll it
// gains a blurred, bordered surface (the `is-scrolled` class). Center links are
// plain (they scroll to same-page sections or are inert anchors); the right
// cluster is "Log in" + the primary "See it live" CTA. Mobile → hamburger drawer.
// Scoped to E — a production MarketingNav exists but is auth/brand-coupled and
// heavier than this preview needs, so we keep a lean local one.
// ---------------------------------------------------------------------------

const NAV_LINKS = [
  { label: 'The funnel', section: 'lve-funnel' },
  { label: 'AI fluency', section: 'lve-wedge' },
  { label: 'Control', section: 'lve-control' },
  { label: 'Proof', section: 'lve-proof' },
];

const Brand = ({ onNavigate }) => (
  <button type="button" className="lve-brand" onClick={() => onNavigate('landing')} aria-label="Taali home">
    <span className="lve-brand-mark" aria-hidden="true" />
    <span className="lve-brand-name">
      Taali<em>.</em>
    </span>
  </button>
);

const scrollToId = (id) => {
  if (typeof document === 'undefined') return;
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
};

export const VariantENav = ({ onNavigate }) => {
  const [scrolled, setScrolled] = useState(false);
  const [drawer, setDrawer] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    if (!drawer) return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [drawer]);

  const go = (link) => {
    setDrawer(false);
    if (link.section) scrollToId(link.section);
  };

  return (
    <>
      <nav className={`lve-nav${scrolled ? ' is-scrolled' : ''}`}>
        <div className="lve-nav-inner">
          <div className="lve-nav-left">
            <Brand onNavigate={onNavigate} />
          </div>

          <div className="lve-nav-links">
            {NAV_LINKS.map((link) => (
              <button type="button" key={link.label} className="lve-nav-link" onClick={() => go(link)}>
                {link.label}
              </button>
            ))}
          </div>

          <div className="lve-nav-right">
            <button type="button" className="lve-nav-login" onClick={() => onNavigate('login')}>
              Log in
            </button>
            <button type="button" className="lve-btn lve-btn--primary lve-btn--sm" onClick={() => onNavigate('demo-lead')}>
              See it live
            </button>
          </div>

          <button
            type="button"
            className="lve-nav-burger"
            aria-label="Open menu"
            aria-expanded={drawer}
            onClick={() => setDrawer(true)}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path d="M3 6h18M3 12h18M3 18h18" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      </nav>

      {drawer ? (
        <div className="lve-drawer" role="dialog" aria-modal="true" aria-label="Menu">
          <div className="lve-drawer-head">
            <Brand onNavigate={onNavigate} />
            <button type="button" className="lve-drawer-close" aria-label="Close menu" onClick={() => setDrawer(false)}>
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
              </svg>
            </button>
          </div>
          <div className="lve-drawer-links">
            {NAV_LINKS.map((link) => (
              <button type="button" key={link.label} className="lve-drawer-link" onClick={() => go(link)}>
                {link.label}
              </button>
            ))}
          </div>
          <div className="lve-drawer-cta">
            <button type="button" className="lve-btn lve-btn--ghost" onClick={() => { setDrawer(false); onNavigate('login'); }}>
              Log in
            </button>
            <button type="button" className="lve-btn lve-btn--primary" onClick={() => { setDrawer(false); onNavigate('demo-lead'); }}>
              See it live
            </button>
          </div>
        </div>
      ) : null}
    </>
  );
};

export default VariantENav;
