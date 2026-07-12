import React, { useEffect, useState } from 'react';

// Sticky nav. Translucent blurred page-bg; a 1px bottom border fades in once the
// page is scrolled past 8px (the `.scrolled` class). Center links are hidden
// below 820px (CSS). CTAs route via onNavigate; section links are in-page
// anchors so smooth-scroll (Lenis) carries to the funnel / fluency / control /
// proof anchors.

const NAV_LINKS = [
  { href: '#funnel', label: 'Agentic hiring' },
  { href: '#fluency', label: 'AI fluency' },
  { href: '#control', label: 'Control' },
  { href: '#proof', label: 'Proof' },
];

export const VariantFNav = ({ onNavigate }) => {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const onScroll = () => setScrolled(window.scrollY > 8);
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  const go = (target) => () => onNavigate && onNavigate(target);

  return (
    <nav className={`nav${scrolled ? ' scrolled' : ''}`}>
      <div className="wrap nav-in">
        <a className="brand" href="#top" aria-label="taali">
          <div className="brand-mark">t</div>
          <div className="brand-word">taali<span className="dot">.</span></div>
        </a>
        <div className="nav-links">
          {NAV_LINKS.map((l) => (
            <a key={l.href} href={l.href}>{l.label}</a>
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

export default VariantFNav;
