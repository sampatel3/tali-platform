// Shared Motion kit for the auth-free "app on Motion" previews
// (/jobs-preview, /report-preview, /analytics-preview). Mirrors the helpers the
// merged /home-preview (HomeMotionPreview) inlines, factored out so the three
// new previews share ONE reveal/ticker/reduced-motion implementation and a
// single floating preview-switcher chip. Nothing here is used by a production
// page — it exists only for the previews.

import React, { useEffect, useState } from 'react';
import { stagger } from 'motion/react';

import './previewMotion.css';

export const EASE_OUT = [0.16, 1, 0.3, 1];

// Synchronous prefers-reduced-motion read. Motion's own useReducedMotion
// resolves in a layout effect (null on first paint), too late to seed the
// deterministic final state the tickers/reveals need for reduced motion.
export const useReducedMotionSync = () => {
  const query = '(prefers-reduced-motion: reduce)';
  const read = () =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query).matches
      : false;
  const [reduced, setReduced] = useState(read);
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const mq = window.matchMedia(query);
    const onChange = () => setReduced(mq.matches);
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener('change', onChange);
      else if (mq.removeListener) mq.removeListener(onChange);
    };
  }, []);
  return reduced;
};

// Counts up to `to` once on mount, then holds. Reduced motion → the final
// value immediately, no tween. `format` maps the animated number to display
// text (default: rounded integer) so a preview can render "82%", "$1.9k", etc.
export const NumberTicker = ({ to, reduced, format = (n) => Math.round(n).toLocaleString(), duration = 1100 }) => {
  const [display, setDisplay] = useState(reduced ? to : 0);
  useEffect(() => {
    if (reduced) {
      setDisplay(to);
      return undefined;
    }
    let raf = 0;
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic
      setDisplay(to * eased);
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [to, reduced, duration]);
  return <>{format(display)}</>;
};

// One-shot fade+rise reveal. Deliberately a PLAIN div with a one-shot CSS
// entrance animation (`.pv-reveal`, keyframe `pvFadeUp`, fill `both`) rather
// than a Motion state-gated wrapper. Motion's `animate` gating proved fragile
// around the heavy real product components these previews embed (tickers/charts
// re-render and could leave a wrapper stuck mid-fade or at its initial state).
// A CSS animation runs once on mount, honours fill:both, and can never get
// stuck — the reliable choice for a preview wrapper. Reduced motion → plain
// visible div (the keyframe is gated behind prefers-reduced-motion in the CSS).
export const Reveal = ({ children, className, style, delay = 0, y = 16, reduced = false }) => {
  if (reduced) return <div className={className} style={style}>{children}</div>;
  return (
    <div
      className={`pv-reveal ${className || ''}`.trim()}
      style={{ ...style, '--pv-y': `${y}px`, animationDelay: `${delay}s` }}
    >
      {children}
    </div>
  );
};

// Alias kept so call sites read intent; identical behaviour.
export const ScrollReveal = ({ y = 24, ...props }) => <Reveal y={y} {...props} />;

// Variants for a staggered grid of children (KPI tiles, role cards). Parent
// gets `staggerContainer`, each child `staggerItem`.
export const staggerContainer = (step = 0.07, startDelay = 0.1) => ({
  hidden: {},
  show: { transition: { delayChildren: stagger(step, { startDelay }) } },
});
export const staggerItem = {
  hidden: { opacity: 0, y: 14, scale: 0.99 },
  show: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.45, ease: EASE_OUT } },
};

const PREVIEW_LINKS = [
  { href: '/home-preview', label: 'Home', key: 'home' },
  { href: '/jobs-preview', label: 'Jobs', key: 'jobs' },
  { href: '/report-preview', label: 'Report', key: 'report' },
  { href: '/analytics-preview', label: 'Analytics', key: 'analytics' },
  { href: '/landing-preview', label: 'Landing', key: 'landing' },
];

// Floating chip on each new preview so the founder can tour the whole app on
// Motion. Anchor links (full navigations) — the previews are separate lazy
// routes, and a hard nav guarantees each remounts cleanly from its OFF/empty
// initial state. `current` bolds the active surface.
export const PreviewSwitcher = ({ current, badge = 'PREVIEW · on Motion' }) => (
  <nav className="pv-switch" aria-label="Motion previews">
    <span className="pv-switch-dot" aria-hidden="true" />
    <span className="pv-switch-badge">{badge}</span>
    <span className="pv-switch-links">
      {PREVIEW_LINKS.map((link) => (
        <a
          key={link.key}
          href={link.href}
          className={`pv-switch-link${link.key === current ? ' is-current' : ''}`}
          aria-current={link.key === current ? 'page' : undefined}
        >
          {link.label}
        </a>
      ))}
    </span>
  </nav>
);
