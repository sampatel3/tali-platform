// Scroll-progress primitives for variant D's pinned scenes.
//
// The scene is driven by ONE number: `p`, the fraction (0→1) the visitor has
// scrolled a tall wrapper past the viewport. `useScrollProgress` measures the
// wrapper's rect against the viewport on a rAF-throttled, passive scroll/resize
// listener and hands `p` to a callback. The callback (in the component) writes
// CSS custom properties + a few textContents — never React state — so scrubbing
// is transform/opacity only and never triggers a re-render.
//
// Nothing here depends on IntersectionObserver. `p` is always clamped to [0,1],
// so the correct static state renders at p=0 and p=1 even if a listener never
// fires.
import { useEffect, useRef, useState } from 'react';

export const clamp = (v, lo = 0, hi = 1) => (v < lo ? lo : v > hi ? hi : v);

// Smoothstep — used for gentle crossfades between beats.
export const smooth = (t) => {
  const x = clamp(t);
  return x * x * (3 - 2 * x);
};

export const easeOut = (t) => 1 - Math.pow(1 - clamp(t), 3);

// Per-beat local progress: p mapped into [0,1] within beat n's 0.2-wide slot.
export const beatLocal = (p, n) => clamp((p - n * 0.2) / 0.2, 0, 1);

// Per-beat opacity with small crossfade overlaps. Beat 0 is visible from the
// start (only fades out); the last beat stays visible through p=1 (only fades
// in); interior beats fade in and out.
export const beatVis = (p, n, count = 5) => {
  const f = 0.035;
  const s = n * 0.2;
  const e = s + 0.2;
  const fadeIn = smooth((p - (s - f)) / (2 * f));
  const fadeOut = 1 - smooth((p - (e - f)) / (2 * f));
  if (n === 0) return clamp(fadeOut);
  if (n === count - 1) return clamp(fadeIn);
  return clamp(Math.min(fadeIn, fadeOut));
};

const prefersReducedMotion = () =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// Static mode = reduced-motion OR a viewport too short to sticky-scrub reliably.
// Below this height the pinned math gets cramped (captions collide with the
// rail), so we fall back to the stacked static composition. This is the guard
// the founder's 80%-zoom / short-landscape cases hit.
const MIN_SCRUB_HEIGHT = 560;

export const useStaticMode = () => {
  const [staticMode, setStaticMode] = useState(() => {
    if (typeof window === 'undefined') return true;
    return prefersReducedMotion() || window.innerHeight < MIN_SCRUB_HEIGHT;
  });
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const evaluate = () =>
      setStaticMode(prefersReducedMotion() || window.innerHeight < MIN_SCRUB_HEIGHT);
    evaluate();
    window.addEventListener('resize', evaluate, { passive: true });
    let mq;
    if (typeof window.matchMedia === 'function') {
      mq = window.matchMedia('(prefers-reduced-motion: reduce)');
      if (mq.addEventListener) mq.addEventListener('change', evaluate);
    }
    return () => {
      window.removeEventListener('resize', evaluate);
      if (mq && mq.removeEventListener) mq.removeEventListener('change', evaluate);
    };
  }, []);
  return staticMode;
};

// Drives `onFrame(p)` on every rAF-throttled scroll/resize while `enabled`.
// `onFrame` is read through a ref so a changing callback identity never
// re-subscribes the listener.
export const useScrollProgress = (ref, enabled, onFrame) => {
  const onFrameRef = useRef(onFrame);
  onFrameRef.current = onFrame;

  useEffect(() => {
    if (!enabled || typeof window === 'undefined') return undefined;
    const el = ref.current;
    if (!el) return undefined;

    let raf = 0;
    const frame = () => {
      raf = 0;
      const rect = el.getBoundingClientRect();
      const total = rect.height - window.innerHeight;
      const p =
        total > 0 ? clamp(-rect.top / total, 0, 1) : rect.top <= 0 ? 1 : 0;
      onFrameRef.current(p);
    };
    const onScroll = () => {
      if (!raf) raf = window.requestAnimationFrame(frame);
    };

    frame(); // paint correct state immediately (p at current scroll position)
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
      if (raf) window.cancelAnimationFrame(raf);
    };
  }, [enabled, ref]);
};
