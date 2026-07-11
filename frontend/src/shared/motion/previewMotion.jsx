// Shared Motion kit for the auth-free "app on Motion" previews
// (/jobs-preview, /report-preview, /analytics-preview). Mirrors the helpers the
// merged /home-preview (HomeMotionPreview) inlines, factored out so the three
// new previews share ONE reveal/ticker/reduced-motion implementation and a
// single floating preview-switcher chip. Nothing here is used by a production
// page — it exists only for the previews.

import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { m, stagger, useInView } from 'motion/react';

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

// THE single reveal trigger for every preview. Returns true when the element
// is scrolled into view (useInView) OR is already in the viewport on mount.
//
// The mount check is the whole point of this hook: `useInView` / `whileInView`
// can miss the very first IntersectionObserver callback for content already in
// view under LazyMotion, which left the above-the-fold sections of a preview
// blank until the first scroll. We read the box in a LAYOUT effect (before
// paint) so an above-the-fold section flips to "revealed" with no flash of
// hidden. Every reveal in these previews is driven off this one hook, so no
// mechanism can ever load hidden. A zero-box (jsdom / SSR) counts as in view so
// tests reveal deterministically.
export const useRevealOnView = (ref, { amount = 0.15 } = {}) => {
  const inView = useInView(ref, { once: true, amount });
  const [mountedInView, setMountedInView] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof window === 'undefined' || typeof el.getBoundingClientRect !== 'function') return;
    const r = el.getBoundingClientRect();
    const zeroBox = r.width === 0 && r.height === 0 && r.top === 0 && r.bottom === 0;
    if (zeroBox || (r.top < (window.innerHeight || 0) && r.bottom > 0)) setMountedInView(true);
  }, [ref]);
  return inView || mountedInView;
};

// One-shot fade+rise reveal driven by useRevealOnView: it reveals immediately
// when in view on mount (no flash — the mount check runs before paint) and on
// scroll otherwise, so it is safe both above and below the fold. Under reduced
// motion it renders a plain wrapper — final state, always visible.
export const Reveal = ({ children, className, style, delay = 0, y = 16, amount = 0.15, reduced = false }) => {
  const ref = useRef(null);
  const shown = useRevealOnView(ref, { amount });
  if (reduced) return <div ref={ref} className={className} style={style}>{children}</div>;
  return (
    <m.div
      ref={ref}
      className={className}
      style={style}
      initial={{ opacity: 0, y }}
      animate={shown ? { opacity: 1, y: 0 } : undefined}
      transition={{ duration: 0.5, ease: EASE_OUT, delay }}
    >
      {children}
    </m.div>
  );
};

// Below-the-fold reveal — the same one hook, just a longer rise. Kept as a
// distinct export so call sites read intent, but it is Reveal underneath so it
// too reveals on mount-if-in-view (never only on scroll).
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
