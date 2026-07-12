// useCountUp — the PRODUCTION number ticker (the preview kit's NumberTicker
// stays preview-only). A tiny easeOutCubic requestAnimationFrame loop with zero
// dependencies: no `motion/react`, no bundle cost.
//
// CRITICAL difference from previewMotion.jsx's NumberTicker: this re-runs the
// tween whenever `to` changes, not only on mount. Real pages feed these values
// from async fetches that settle AFTER first paint (Home pulse stats, Analytics
// summary, report scores) — a mount-only ticker would freeze on the initial 0
// and never count to the settled value.

import { useEffect, useState } from 'react';

// Synchronous prefers-reduced-motion read, ported verbatim from the preview
// kit. Motion's own useReducedMotion resolves in a layout effect (null on first
// paint), too late — it flashes 0 before settling. Reading matchMedia
// synchronously in the initial state seeds the deterministic final value.
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

/**
 * Count up to `to`, re-running each time `to` changes. Returns the formatted
 * display value. Reduced motion → the final value immediately, no tween.
 *
 * @param {number} to - target value to count to.
 * @param {object} [opts]
 * @param {boolean} [opts.reduced=false] - skip the tween, render `to` at once.
 * @param {number}  [opts.duration=1100] - tween length in ms.
 * @param {(n:number)=>(string|number)} [opts.format] - maps the animated number
 *   to display text (default: rounded, locale-grouped integer), e.g. "82%",
 *   "$1.9k", "1,204".
 * @returns {string|number} the formatted current value.
 */
export const useCountUp = (to, { reduced = false, duration = 1100, format = (n) => Math.round(n).toLocaleString() } = {}) => {
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
  return format(display);
};
