// Shared Motion kit for the auth-free "app on Motion" previews
// (/jobs-preview, /report-preview, /analytics-preview). Mirrors the helpers the
// merged /home-preview (HomeMotionPreview) inlines, factored out so the three
// new previews share the legacy preview switcher/variant helpers while reveal,
// number, and reduced-motion behavior come from the production motion system.
// It also exposes a
// single floating preview-switcher chip. Nothing here is used by a production
// page — it exists only for the previews.

import React from 'react';
import { stagger } from 'motion/react';
import { MotionNumber } from './primitives';
import { MOTION_DURATION, MOTION_EASE, MOTION_STAGGER } from './tokens';

import './previewMotion.css';

export const EASE_OUT = MOTION_EASE.enter;

// Synchronous prefers-reduced-motion read. Motion's own useReducedMotion
// resolves in a layout effect (null on first paint), too late to seed the
// deterministic final state the tickers/reveals need for reduced motion.
// Counts up to `to` once on mount, then holds. Reduced motion → the final
// value immediately, no tween. `format` maps the animated number to display
// text (default: rounded integer) so a preview can render "82%", "$1.9k", etc.
export const NumberTicker = ({ to, reduced, format = (n) => Math.round(n).toLocaleString(), duration = 1100 }) => {
  return (
    <MotionNumber
      value={to}
      initialValue={0}
      duration={duration / 1000}
      reduced={reduced}
      format={format}
    />
  );
};

// Variants for a staggered grid of children (KPI tiles, role cards). Parent
// gets `staggerContainer`, each child `staggerItem`.
export const staggerContainer = (step = MOTION_STAGGER.default, startDelay = 0.1) => ({
  hidden: {},
  show: { transition: { delayChildren: stagger(step, { startDelay }) } },
});
export const staggerItem = {
  hidden: { opacity: 0, y: 14, scale: 0.99 },
  show: { opacity: 1, y: 0, scale: 1, transition: { duration: MOTION_DURATION.reveal, ease: EASE_OUT } },
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
