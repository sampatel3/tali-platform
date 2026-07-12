// Legacy compatibility hook. Production surfaces now use MotionNumber so
// values interpolate from their previous state and share the system tokens.
// Keep this export temporarily for downstream imports; do not add new callers.
//
// CRITICAL difference from previewMotion.jsx's NumberTicker: this re-runs the
// tween whenever `to` changes, not only on mount. Real pages feed these values
// from async fetches that settle AFTER first paint (Home pulse stats, Analytics
// summary, report scores) — a mount-only ticker would freeze on the initial 0
// and never count to the settled value.

import { useEffect, useState } from 'react';

import { MOTION_DURATION } from './tokens';
import { useReducedMotionSync } from './useReducedMotionSync';

export { useReducedMotionSync };

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
export const useCountUp = (to, { reduced = false, duration = MOTION_DURATION.data * 1000, format = (n) => Math.round(n).toLocaleString() } = {}) => {
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
