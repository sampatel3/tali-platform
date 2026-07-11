import React, { useEffect, useRef, useState } from 'react';
import { m, stagger, useAnimate, useInView } from 'motion/react';

// Synchronous prefers-reduced-motion hook. Motion's own useReducedMotion resolves
// in a layout effect (null on first render), which is too late to seed initial
// state deterministically; this reads matchMedia synchronously in the useState
// initialiser (matching how variants C/D detect it) and subscribes for changes.
export const useReducedMotion = () => {
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

// ---------------------------------------------------------------------------
// Motion primitives for landing variant E. Two ideas, used everywhere:
//
//   • <Reveal> / <Stagger> — one-shot section entrances. An `m.div` that fades +
//     lifts in the first time it scrolls into view (`whileInView` + `once`), then
//     never replays. This is ~90% of the motion on the page.
//
//   • useAutoplay — the Cursor-style "the mock is alive" loop. A `useAnimate`
//     timeline started by `useInView(scope, { amount })`, looped with an async
//     `while (alive)` and STOPPED on cleanup, so it plays only while on screen and
//     pauses (unmounts its loop) the moment it scrolls away. NOT scroll-scrubbed.
//
// Accessibility: the whole variant is wrapped in <MotionConfig reducedMotion="user">,
// so Reveal/Stagger auto-drop transforms under prefers-reduced-motion. useAutoplay
// additionally branches on `useReducedMotion()` and renders the mock's FINAL state
// with no loop — see the `[data-animated]` contract below.
// ---------------------------------------------------------------------------

const EASE_OUT = [0.16, 1, 0.3, 1];

// A single fade-lift entrance. `as` lets callers pick the element; defaults div.
export const Reveal = ({
  children,
  as = 'div',
  className,
  delay = 0,
  amount = 0.3,
  y = 20,
  ...rest
}) => {
  const Comp = m[as] || m.div;
  return (
    <Comp
      className={className}
      initial={{ opacity: 0, y, scale: 0.99 }}
      whileInView={{ opacity: 1, y: 0, scale: 1 }}
      viewport={{ once: true, amount }}
      transition={{ duration: 0.5, ease: EASE_OUT, delay }}
      {...rest}
    >
      {children}
    </Comp>
  );
};

// Stagger container — children declared with <StaggerItem> cascade in.
export const Stagger = ({
  children,
  as = 'div',
  className,
  amount = 0.25,
  step = 0.08,
  ...rest
}) => {
  const Comp = m[as] || m.div;
  return (
    <Comp
      className={className}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, amount }}
      variants={{
        hidden: {},
        show: { transition: { delayChildren: stagger(step) } },
      }}
      {...rest}
    >
      {children}
    </Comp>
  );
};

export const StaggerItem = ({ children, as = 'div', className, y = 18, ...rest }) => {
  const Comp = m[as] || m.div;
  return (
    <Comp
      className={className}
      variants={{
        hidden: { opacity: 0, y, scale: 0.99 },
        show: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.5, ease: EASE_OUT } },
      }}
      {...rest}
    >
      {children}
    </Comp>
  );
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------------------------------------------------------------------------
// useAutoplay — arm-and-loop a mock. Returns { scope, reduced, inView }.
//
//   const scope = useAutoplay(() => [
//     ['.thing', { opacity: [0, 1], y: [8, 0] }, { duration: 0.4, delay: stagger(0.1) }],
//     ...
//   ]).scope;
//
// Contract with CSS: while the mock WILL animate (JS mounted, not reduced), the
// scope root carries `data-animated="true"` and the stylesheet hides its `.lve-anim`
// children. The timeline reveals them. When `reduced` is true we DROP the attribute
// so every child renders in its natural (final) CSS state and no loop runs. The
// loop only runs while `inView && enabled`; cleanup sets `alive=false` and stops
// the in-flight controls, so scrolling away halts it.
// ---------------------------------------------------------------------------
export const useAutoplay = (buildSequence, { amount = 0.5, loopDelay = 1.6, enabled = true } = {}) => {
  const [scope, animate] = useAnimate();
  const inView = useInView(scope, { amount });
  const reduced = useReducedMotion();
  // Keep the latest builder without re-subscribing the effect every render.
  const buildRef = useRef(buildSequence);
  buildRef.current = buildSequence;

  useEffect(() => {
    const root = scope.current;
    if (!root) return undefined;

    if (reduced) {
      root.removeAttribute('data-animated'); // show final composed state
      return undefined;
    }
    // Arm the initial-hidden state (CSS keys off this attribute).
    root.setAttribute('data-animated', 'true');

    if (!inView || !enabled) return undefined;

    let alive = true;
    let controls = null;
    (async () => {
      while (alive) {
        try {
          controls = animate(buildRef.current());
          await controls;
        } catch {
          /* stopped on cleanup — expected */
        }
        if (!alive) break;
        await sleep(loopDelay * 1000);
      }
    })();

    return () => {
      alive = false;
      if (controls && typeof controls.stop === 'function') controls.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inView, reduced, enabled, amount, loopDelay]);

  return { scope, reduced, inView };
};

// A number that counts up to `value` the first time it scrolls into view, then
// holds. Reduced motion → renders the final value immediately, no tween.
export const Ticker = ({ value, format = (v) => Math.round(v).toLocaleString('en-US'), className }) => {
  const ref = useRef(null);
  const inView = useInView(ref, { once: true, amount: 0.6 });
  const reduced = useReducedMotion();
  const [display, setDisplay] = useState(reduced ? value : 0);

  useEffect(() => {
    if (reduced) {
      setDisplay(value);
      return undefined;
    }
    if (!inView) return undefined;
    let raf = 0;
    const start = performance.now();
    const dur = 1300;
    const tick = (now) => {
      const t = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic
      setDisplay(value * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [inView, reduced, value]);

  return (
    <span ref={ref} className={className}>
      {format(display)}
    </span>
  );
};
