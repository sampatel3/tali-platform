import React, { forwardRef, useCallback, useRef } from 'react';
import { m, useInView } from 'motion/react';

import { AGENT_LOOP_DURATION, MOTION_DURATION, MOTION_EASE } from './tokens';
import { useDocumentVisibility } from './useDocumentVisibility';
import { useReducedMotionSync } from './useReducedMotionSync';

const freezePreset = (animate, rest, transition, inactive = rest) => Object.freeze({
  animate: Object.freeze(animate),
  inactive: Object.freeze(inactive),
  rest: Object.freeze(rest),
  transition: Object.freeze(transition),
});

/**
 * The only approved continuous-motion vocabulary in the product. Each loop
 * communicates active agent identity, an agent-authored recommendation, or
 * work in flight; none is generic decoration or a substitute for a label.
 */
export const agentLoopPresets = Object.freeze({
  flow: freezePreset(
    { x: ['-12%', '12%', '-12%'] },
    { x: '0%' },
    {
      duration: AGENT_LOOP_DURATION.flow,
      ease: MOTION_EASE.loop,
      repeat: Infinity,
      repeatType: 'loop',
    },
    { x: '0%' },
  ),
  glow: freezePreset(
    {
      opacity: [0.45, 1, 0.45],
      scale: [0.985, 1.015, 0.985],
    },
    { opacity: 0.72, scale: 1 },
    {
      duration: AGENT_LOOP_DURATION.glow,
      ease: MOTION_EASE.loop,
      repeat: Infinity,
      repeatType: 'loop',
    },
    { opacity: 0, scale: 1 },
  ),
  pulse: freezePreset(
    { opacity: [0.55, 1, 0.55], scale: [0.9, 1.16, 0.9] },
    { opacity: 1, scale: 1 },
    {
      duration: AGENT_LOOP_DURATION.pulse,
      ease: MOTION_EASE.loop,
      repeat: Infinity,
      repeatType: 'loop',
    },
    { opacity: 1, scale: 1 },
  ),
  ring: freezePreset(
    { opacity: [0.7, 0], scale: [1, 1.3] },
    { opacity: 0, scale: 1 },
    {
      duration: AGENT_LOOP_DURATION.ring,
      ease: MOTION_EASE.exit,
      repeat: Infinity,
      repeatType: 'loop',
    },
    { opacity: 0, scale: 1 },
  ),
  ambient: freezePreset(
    {
      x: ['-4%', '4%', '-4%'],
      y: ['-2%', '2%', '-2%'],
    },
    { x: '0%', y: '0%' },
    {
      duration: AGENT_LOOP_DURATION.ambient,
      ease: MOTION_EASE.loop,
      repeat: Infinity,
      repeatType: 'loop',
    },
    { x: '0%', y: '0%' },
  ),
});

export const resolveAgentLoop = (
  kind = 'pulse',
  { active = true, reduced = false, inView = true } = {},
) => {
  const preset = agentLoopPresets[kind] || agentLoopPresets.pulse;
  const running = Boolean(active && !reduced && inView);
  return {
    animate: running ? preset.animate : (active ? preset.rest : preset.inactive),
    transition: running ? preset.transition : { duration: MOTION_DURATION.instant },
    state: running ? 'running' : 'rest',
  };
};

const MOTION_ELEMENTS = Object.freeze({
  aside: m.aside,
  button: m.button,
  div: m.div,
  span: m.span,
});

const LAYERED_AGENT_LOOPS = new Set(['flow', 'glow', 'ambient']);

/**
 * Motion.dev-native active-agent loop. Offscreen and reduced-motion instances
 * settle to a deterministic static state, and meaningful text always remains
 * outside (or inside) the decorative animation rather than depending on it.
 */
export const AgentLoop = forwardRef(function AgentLoop({
  active = true,
  as = 'span',
  children,
  className,
  delay = 0,
  kind = 'pulse',
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
  const loop = resolveAgentLoop(kind, { active, reduced, inView: inView && documentVisible });
  const Component = MOTION_ELEMENTS[as] || m.span;
  const transition = loop.state === 'running' && delay
    ? { ...loop.transition, delay }
    : loop.transition;
  const layered = LAYERED_AGENT_LOOPS.has(kind);

  return (
    <Component
      ref={setRef}
      className={layered
        ? `agent-motion-host agent-motion-${kind}${className ? ` ${className}` : ''}`
        : className}
      initial={false}
      animate={layered ? undefined : loop.animate}
      transition={layered ? undefined : transition}
      data-motion-loop={kind}
      data-motion-state={loop.state}
      aria-hidden={ariaHidden ?? (children == null ? 'true' : undefined)}
      {...props}
    >
      {layered && kind === 'glow' ? (
        <m.span
          aria-hidden="true"
          className="agent-motion-glow-layer"
          initial={false}
          animate={loop.animate}
          transition={transition}
        />
      ) : null}
      {layered && kind !== 'glow' ? (
        <span aria-hidden="true" className="agent-motion-layer-clip">
          <m.span
            className="agent-motion-transform-layer"
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

/** Adapter for shared polymorphic Button call sites that need agent flow. */
export const AgentFlowButton = forwardRef(function AgentFlowButton(props, ref) {
  return <AgentLoop ref={ref} as="button" kind="flow" {...props} />;
});
AgentFlowButton.rendersNativeButton = true;
