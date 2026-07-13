import React, { forwardRef, useCallback, useRef } from 'react';
import { m, useInView } from 'motion/react';

import { motionTransition } from './presets';
import { MOTION_DURATION, MOTION_EASE } from './tokens';
import { useDocumentVisibility } from './useDocumentVisibility';
import { useReducedMotionSync } from './useReducedMotionSync';

const MOTION_ELEMENTS = Object.freeze({
  aside: m.aside,
  a: m.a,
  article: m.article,
  button: m.button,
  div: m.div,
  footer: m.footer,
  g: m.g,
  header: m.header,
  li: m.li,
  main: m.main,
  nav: m.nav,
  ol: m.ol,
  p: m.p,
  polygon: m.polygon,
  section: m.section,
  span: m.span,
  svg: m.svg,
  ul: m.ul,
});

export const motionElementFor = (as = 'div') => MOTION_ELEMENTS[as] || m.div;

const loopPreset = (animate, rest, transition) => Object.freeze({
  animate: Object.freeze(animate),
  rest: Object.freeze(rest),
  transition: Object.freeze(transition),
});

/** Non-agent continuous motion. Agent identity must continue to use AgentLoop. */
export const motionLoopPresets = Object.freeze({
  spin: loopPreset(
    { rotate: [0, 360] },
    { rotate: 0 },
    { duration: 0.8, ease: 'linear', repeat: Infinity, repeatType: 'loop' },
  ),
  pulse: loopPreset(
    { opacity: [0.68, 1, 0.68], scale: [0.96, 1.04, 0.96] },
    { opacity: 1, scale: 1 },
    { duration: 1.6, ease: MOTION_EASE.loop, repeat: Infinity, repeatType: 'loop' },
  ),
  signal: loopPreset(
    {
      opacity: [0.62, 1, 0.62],
      scale: [0.94, 1.08, 0.94],
    },
    { opacity: 1, scale: 1 },
    { duration: 1.6, ease: MOTION_EASE.standard, repeat: Infinity, repeatType: 'loop' },
  ),
  bob: loopPreset(
    { y: [0, 4, 0] },
    { y: 0 },
    { duration: 1.8, ease: MOTION_EASE.loop, repeat: Infinity, repeatType: 'loop' },
  ),
  shimmer: loopPreset(
    { x: ['-65%', '65%'] },
    { x: '0%' },
    { duration: 1.3, ease: MOTION_EASE.loop, repeat: Infinity, repeatType: 'loop' },
  ),
});

export const resolveMotionLoop = (
  kind = 'spin',
  { active = true, reduced = false, inView = true } = {},
) => {
  const preset = motionLoopPresets[kind] || motionLoopPresets.spin;
  const running = Boolean(active && !reduced && inView);
  return {
    animate: running ? preset.animate : preset.rest,
    transition: running ? preset.transition : motionTransition.instant,
    state: running ? 'running' : 'rest',
  };
};

/**
 * Shared continuous-motion primitive for progress/loading/status feedback.
 * It pauses offscreen and settles under reduced motion. Agent state should use
 * AgentLoop so product meaning remains distinct from generic activity.
 */
export const MotionLoop = forwardRef(function MotionLoop({
  active = true,
  as = 'span',
  children,
  className,
  delay = 0,
  duration,
  kind = 'spin',
  reduced: reducedOverride,
  'aria-hidden': ariaHidden,
  ...props
}, forwardedRef) {
  const visibilityRef = useRef(null);
  const setRef = useCallback((node) => {
    visibilityRef.current = node;
    if (typeof forwardedRef === 'function') forwardedRef(node);
    else if (forwardedRef) forwardedRef.current = node;
  }, [forwardedRef]);
  const inView = useInView(visibilityRef, { amount: 0, initial: false });
  const documentVisible = useDocumentVisibility();
  const systemReduced = useReducedMotionSync();
  const reduced = systemReduced || Boolean(reducedOverride);
  const loop = resolveMotionLoop(kind, { active, reduced, inView: inView && documentVisible });
  const Component = motionElementFor(as);
  const layered = kind === 'shimmer';
  const transition = loop.state === 'running'
    ? {
        ...loop.transition,
        ...(duration == null ? {} : { duration }),
        ...(delay ? { delay } : {}),
      }
    : loop.transition;

  return (
    <Component
      ref={setRef}
      className={layered
        ? `motion-loop-host motion-loop-${kind}${className ? ` ${className}` : ''}`
        : className}
      initial={false}
      animate={layered ? undefined : loop.animate}
      transition={layered ? undefined : transition}
      data-motion-loop={kind}
      data-motion-state={loop.state}
      aria-hidden={ariaHidden ?? (children == null ? 'true' : undefined)}
      {...props}
    >
      {layered ? (
        <span aria-hidden="true" className="motion-loop-layer-clip">
          <m.span
            className="motion-loop-transform-layer"
            initial={false}
            animate={loop.animate}
            transition={transition}
          />
        </span>
      ) : null}
      {children}
    </Component>
  );
});

MotionLoop.rendersNativeButton = true;

export const MotionSpinner = forwardRef(function MotionSpinner({
  className = '',
  label,
  size = 24,
  style,
  ...props
}, ref) {
  return (
    <MotionLoop
      ref={ref}
      kind="spin"
      className={`motion-spinner ${className}`.trim()}
      style={{ width: size, height: size, ...style }}
      role={label ? 'status' : undefined}
      aria-label={label || undefined}
      aria-hidden={label ? false : true}
      {...props}
    />
  );
});

export const MotionSkeleton = forwardRef(function MotionSkeleton({ className = '', ...props }, ref) {
  return (
    <MotionLoop
      ref={ref}
      kind="shimmer"
      className={`motion-skeleton ${className}`.trim()}
      aria-hidden="true"
      {...props}
    />
  );
});

/** One-shot, in-view progress growth for bars, rails, and data plots. */
export const MotionProgress = forwardRef(function MotionProgress({
  active = true,
  amount = 0.15,
  as = 'span',
  axis = 'x',
  children,
  className,
  delay = 0,
  once = true,
  reduced: reducedOverride,
  style,
  value = 1,
  ...props
}, forwardedRef) {
  const visibilityRef = useRef(null);
  const setRef = useCallback((node) => {
    visibilityRef.current = node;
    if (typeof forwardedRef === 'function') forwardedRef(node);
    else if (forwardedRef) forwardedRef.current = node;
  }, [forwardedRef]);
  const inView = useInView(visibilityRef, { amount, once });
  const systemReduced = useReducedMotionSync();
  const reduced = systemReduced || Boolean(reducedOverride);
  const Component = motionElementFor(as);
  const scaleProperty = axis === 'y' ? 'scaleY' : 'scaleX';
  const visible = Boolean(reduced || (active && inView));
  const normalizedValue = Math.max(0, Math.min(1, Number(value) || 0));

  return (
    <Component
      ref={setRef}
      className={className}
      initial={false}
      animate={{ [scaleProperty]: visible ? normalizedValue : 0 }}
      transition={reduced ? motionTransition.instant : { ...motionTransition.data, delay }}
      style={{
        transformOrigin: axis === 'y' ? 'center bottom' : 'left center',
        ...style,
      }}
      data-motion-progress={axis}
      data-motion-value={normalizedValue}
      {...props}
    >
      {children}
    </Component>
  );
});

export const MOTION_EFFECT_DURATION = Object.freeze({
  spinner: 0.8,
  skeleton: 1.3,
  progress: MOTION_DURATION.data,
});
