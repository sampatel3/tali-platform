import React, { useEffect, useState } from 'react';

// Sticky nav. Translucent blurred page-bg; a 1px bottom border fades in once the
// page is scrolled past 8px (the `.scrolled` class). Center links are hidden
// below 820px (CSS). CTAs route via onNavigate. Section links call onSection(id)
// — the wrapper smooth-scrolls (Lenis when live, native fallback otherwise) to
// that section's top, just below the sticky nav. `active` (from the wrapper's
// scroll-spy) marks the in-view link with `.is-active`.

export const NAV_LINKS = [
  { id: 'g-funnel', label: 'Agentic hiring' },
  { id: 'g-fluency', label: 'AI fluency' },
  { id: 'g-control', label: 'Control' },
  { id: 'g-proof', label: 'Proof' },
];

export const VariantGNav = ({ onNavigate, onSection, active }) => {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const onScroll = () => setScrolled(window.scrollY > 8);
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  const go = (target) => () => onNavigate && onNavigate(target);
  const jump = (id) => (e) => {
    e.preventDefault();
    if (onSection) onSection(id);
  };

  return (
    <nav className={`nav${scrolled ? ' scrolled' : ''}`}>
      <div className="wrap nav-in">
        <a className="brand" href="#g-top" aria-label="taali" onClick={jump('g-top')}>
          <div className="brand-mark">t</div>
          <div className="brand-word">taali<span className="dot">.</span></div>
        </a>
        <div className="nav-links">
          {NAV_LINKS.map((l) => (
            <a
              key={l.id}
              href={`#${l.id}`}
              onClick={jump(l.id)}
              className={active === l.id ? 'is-active' : undefined}
              aria-current={active === l.id ? 'true' : undefined}
            >
              {l.label}
            </a>
          ))}
        </div>
        <div className="nav-right">
          <button type="button" className="btn btn-ghost" onClick={go('/login')}>Log in</button>
          <button type="button" className="btn btn-primary" onClick={go('/signup')}>
            See it live <span className="arw">→</span>
          </button>
        </div>
      </div>
    </nav>
  );
};

export default VariantGNav;
