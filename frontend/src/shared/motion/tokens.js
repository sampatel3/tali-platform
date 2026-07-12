/**
 * Taali motion tokens.
 *
 * Motion's React API consumes seconds and unitless pixel distances, so the
 * JavaScript values intentionally use those units. Keep these values in sync
 * with motion.css, which exposes the same vocabulary to CSS-only interactions.
 */
export const MOTION_DURATION = Object.freeze({
  instant: 0.08,
  fast: 0.14,
  base: 0.2,
  spatial: 0.28,
  reveal: 0.48,
  data: 0.75,
});

// Continuous motion is reserved for active agent work. These intentionally
// live outside the interaction duration scale: a 7s flow is an ambient state
// signal, not feedback that a user must wait for.
export const AGENT_LOOP_DURATION = Object.freeze({
  pulse: 1.6,
  ring: 1.6,
  glow: 3.6,
  flow: 7,
  ambient: 18,
});

export const MOTION_EASE = Object.freeze({
  enter: Object.freeze([0.16, 1, 0.3, 1]),
  standard: Object.freeze([0.2, 0, 0, 1]),
  exit: Object.freeze([0.4, 0, 1, 1]),
  emphasized: Object.freeze([0.2, 0.7, 0.2, 1]),
  confirm: Object.freeze([0.2, 1.3, 0.4, 1]),
  loop: Object.freeze([0.45, 0, 0.55, 1]),
});

export const MOTION_SPRING = Object.freeze({
  layout: Object.freeze({
    type: 'spring',
    stiffness: 420,
    damping: 36,
    mass: 0.8,
  }),
});

export const MOTION_DISTANCE = Object.freeze({
  micro: 2,
  small: 4,
  medium: 12,
  large: 24,
});

export const MOTION_STAGGER = Object.freeze({
  dense: 0.035,
  default: 0.06,
  maxItems: 8,
});

export const motionTokens = Object.freeze({
  duration: MOTION_DURATION,
  agentLoopDuration: AGENT_LOOP_DURATION,
  ease: MOTION_EASE,
  spring: MOTION_SPRING,
  distance: MOTION_DISTANCE,
  stagger: MOTION_STAGGER,
});

// Lower-case aliases keep consuming code compact without weakening the
// canonical, searchable token names above.
export const duration = MOTION_DURATION;
export const agentLoopDuration = AGENT_LOOP_DURATION;
export const ease = MOTION_EASE;
export const spring = MOTION_SPRING;
export const distance = MOTION_DISTANCE;
export const staggerToken = MOTION_STAGGER;
