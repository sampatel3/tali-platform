import React, {
  Children,
  createContext,
  forwardRef,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import {
  AnimatePresence,
  LayoutGroup,
  animate,
  m,
  stagger,
  useAnimate,
  useInView,
  useMotionValue,
  useMotionValueEvent,
} from 'motion/react';

import {
  chatItemVariants,
  disclosureVariants,
  fadeVariants,
  listItemVariants,
  motionTransition,
  reducedFadeVariants,
  scaleVariants,
} from './presets';
import { motionElementFor } from './effects';
import { MOTION_DURATION, MOTION_EASE, MOTION_STAGGER } from './tokens';
import { useReducedMotionSync } from './useReducedMotionSync';

const cx = (...values) => values.filter(Boolean).join(' ');

/** A true, once-only in-view entrance for narrative/marketing content. */
export function Reveal({
  as = 'div',
  children,
  className,
  delay = 0,
  x = 0,
  y = 12,
  amount = 0.01,
  once = true,
  reduced: reducedOverride,
  onFocusCapture,
  ...props
}) {
  const ref = useRef(null);
  const inView = useInView(ref, { amount, once });
  const [focusVisible, setFocusVisible] = useState(false);
  const systemReduced = useReducedMotionSync();
  const reduced = systemReduced || Boolean(reducedOverride);
  const visible = reduced || focusVisible || inView;
  const Component = motionElementFor(as);

  return (
    <Component
      ref={ref}
      className={className}
      initial={false}
      animate={visible ? { opacity: 1, x: 0, y: 0 } : { opacity: 0, x, y }}
      transition={reduced ? motionTransition.instant : { ...motionTransition.reveal, delay }}
      data-motion-reveal={x ? 'horizontal' : 'vertical'}
      data-motion-reveal-state={visible ? 'visible' : 'hidden'}
      onFocusCapture={(event) => {
        // An offscreen reveal may contain the next tabbable control. Make the
        // whole region visible in the same focus event rather than allowing a
        // keyboard user to land on an opacity-zero button for a frame.
        setFocusVisible(true);
        onFocusCapture?.(event);
      }}
      {...props}
    >
      {children}
    </Component>
  );
}

/** Staggers existing direct children without adding wrappers or changing semantics. */
export function MotionStagger({
  active = true,
  as = 'div',
  children,
  className,
  delay = 0,
  distance = 12,
  reduced: reducedOverride,
  step = MOTION_STAGGER.default,
  ...props
}) {
  const [scope, animateChildren] = useAnimate();
  const systemReduced = useReducedMotionSync();
  const reduced = systemReduced || Boolean(reducedOverride);
  const Component = motionElementFor(as);
  const childSignature = Children.toArray(children)
    .map((child, index) => child?.key ?? index)
    .join('|');
  const animatedNodesRef = useRef(new WeakSet());
  const controlsRef = useRef(new Set());

  useLayoutEffect(() => {
    if (!active) return undefined;
    const nodes = Array.from(scope.current?.children || []);
    if (!nodes.length) return undefined;
    const targets = reduced
      ? nodes
      : nodes.filter((node) => !animatedNodesRef.current.has(node));
    if (!targets.length) return undefined;
    targets.forEach((node) => animatedNodesRef.current.add(node));
    if (reduced) {
      controlsRef.current.forEach((controls) => controls.stop());
      controlsRef.current.clear();
    }
    const controls = animateChildren(
      targets,
      reduced
        ? { opacity: 1, y: 0 }
        : { opacity: [0, 1], y: [distance, 0] },
      reduced
        ? motionTransition.instant
        : {
            duration: MOTION_DURATION.reveal,
            ease: MOTION_EASE.enter,
            delay: stagger(step, { startDelay: delay }),
          },
    );
    controlsRef.current.add(controls);
    controls.then(
      () => controlsRef.current.delete(controls),
      () => controlsRef.current.delete(controls),
    );
    return undefined;
  }, [active, animateChildren, childSignature, delay, distance, reduced, scope, step]);

  useEffect(() => () => {
    controlsRef.current.forEach((controls) => controls.stop());
    controlsRef.current.clear();
  }, []);

  return (
    <Component ref={scope} className={className} {...props}>
      {children}
    </Component>
  );
}

/** Keyed content continuity for tabs, loading states, and selected details. */
export function PresenceSwap({
  presenceKey,
  children,
  className,
  mode = 'wait',
  initial = false,
  ...props
}) {
  const reduced = useReducedMotionSync();
  return (
    <AnimatePresence initial={initial} mode={mode}>
      <m.div
        key={presenceKey}
        className={className}
        variants={reduced ? reducedFadeVariants : fadeVariants}
        initial="hidden"
        animate="visible"
        exit="exit"
        {...props}
      >
        {children}
      </m.div>
    </AnimatePresence>
  );
}

/** Measured expand/collapse that remains mounted long enough to animate out. */
export function MotionDisclosure({ open, children, className, id, ...props }) {
  const reduced = useReducedMotionSync();
  const variants = reduced
    ? {
        collapsed: { height: 'auto', opacity: 1 },
        open: { height: 'auto', opacity: 1, transition: motionTransition.instant },
        exit: { height: 0, opacity: 0, transition: motionTransition.instant },
      }
    : disclosureVariants;

  return (
    <AnimatePresence initial={false}>
      {open ? (
        <m.div
          key="disclosure-content"
          id={id}
          className={cx('motion-disclosure', className)}
          variants={variants}
          initial={reduced ? false : 'collapsed'}
          animate="open"
          exit="exit"
          {...props}
        >
          {children}
        </m.div>
      ) : null}
    </AnimatePresence>
  );
}

const TabsContext = createContext(null);

/** Shared tablist behavior: roving focus plus a layout-linked active marker. */
export function MotionTabs({
  value,
  onValueChange,
  children,
  className,
  'aria-label': ariaLabel,
  ...props
}) {
  const generatedId = useId().replace(/:/g, '');
  const ref = useRef(null);

  const onKeyDown = (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const tabs = Array.from(ref.current?.querySelectorAll('[role="tab"]:not(:disabled)') || []);
    if (!tabs.length) return;
    const current = Math.max(0, tabs.indexOf(document.activeElement));
    const targetIndex = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? tabs.length - 1
        : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    tabs[targetIndex].focus();
    tabs[targetIndex].click();
  };

  return (
    <TabsContext.Provider value={{ value, onValueChange, layoutId: `motion-tab-${generatedId}` }}>
      <LayoutGroup id={`motion-tabs-${generatedId}`}>
        <div
          ref={ref}
          role="tablist"
          aria-label={ariaLabel}
          className={className}
          onKeyDown={onKeyDown}
          {...props}
        >
          {children}
        </div>
      </LayoutGroup>
    </TabsContext.Provider>
  );
}

export function MotionTab({ value, children, className, indicatorClassName, ...props }) {
  const reduced = useReducedMotionSync();
  const context = useContext(TabsContext);
  if (!context) throw new Error('MotionTab must be rendered inside MotionTabs.');
  const active = context.value === value;

  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      tabIndex={active ? 0 : -1}
      className={className}
      onClick={() => context.onValueChange?.(value)}
      {...props}
    >
      {children}
      {active ? (
        <m.span
          aria-hidden="true"
          className={cx('motion-tab-indicator', indicatorClassName)}
          layoutId={context.layoutId}
          transition={reduced ? motionTransition.instant : motionTransition.layout}
        />
      ) : null}
    </button>
  );
}

/** Layout-aware list shell; list items opt into presence individually. */
export function MotionList({ as = 'div', children, className, initial = false, ...props }) {
  const reduced = useReducedMotionSync();
  const Component = motionElementFor(as);
  return (
    <LayoutGroup>
      <Component layout={reduced ? false : true} className={className} {...props}>
        <AnimatePresence initial={initial} mode={reduced ? 'sync' : 'popLayout'}>
          {children}
        </AnimatePresence>
      </Component>
    </LayoutGroup>
  );
}

/**
 * Immediate, one-shot arrival for chat transcript rows. Unlike MotionListItem,
 * this primitive never derives a delay from the row's absolute transcript
 * position; loaded history stays settled through MotionList's `initial=false`.
 */
export const MotionChatItem = forwardRef(function MotionChatItem({
  as = 'div',
  children,
  className,
  initial = 'hidden',
  ...props
}, forwardedRef) {
  const reduced = useReducedMotionSync();
  const Component = motionElementFor(as);
  return (
    <Component
      ref={forwardedRef}
      className={className}
      variants={reduced ? reducedFadeVariants : chatItemVariants}
      initial={reduced ? false : initial}
      animate="visible"
      exit="exit"
      data-motion-chat-item={reduced ? 'settled' : 'arrival'}
      {...props}
    >
      {children}
    </Component>
  );
});

export const MotionListItem = forwardRef(function MotionListItem({
  as = 'div',
  children,
  className,
  density = 'default',
  index = 0,
  initial = 'hidden',
  ...props
}, forwardedRef) {
  const reduced = useReducedMotionSync();
  const Component = motionElementFor(as);
  return (
    <Component
      ref={forwardedRef}
      layout={reduced ? false : true}
      className={className}
      custom={{ index, density }}
      variants={reduced ? reducedFadeVariants : listItemVariants}
      initial={reduced ? false : initial}
      animate="visible"
      exit="exit"
      transition={{ layout: reduced ? motionTransition.instant : motionTransition.layout }}
      {...props}
    >
      {children}
    </Component>
  );
});

/**
 * A quiet attention count: it is settled on hydration, enters when a previously
 * empty count appears, and gives a single acknowledgement pop when the count
 * increases. Decreases remain still so reading/clearing notifications feels
 * calm rather than celebratory.
 */
export function MotionAttentionBadge({
  value = 0,
  className,
  prefix,
  format = Math.round,
  ...props
}) {
  const numericValue = Math.max(0, Number(value) || 0);
  const reduced = useReducedMotionSync();
  const previousRef = useRef(numericValue);
  const [scope, animateBadge] = useAnimate();
  const appeared = previousRef.current <= 0 && numericValue > 0;

  useEffect(() => {
    const previous = previousRef.current;
    previousRef.current = numericValue;
    if (reduced || previous <= 0 || numericValue <= previous || !scope.current) return undefined;
    const controls = animateBadge(
      scope.current,
      { scale: [1, 1.14, 1], y: [0, -1, 0] },
      { ...motionTransition.base, duration: MOTION_DURATION.base },
    );
    return () => controls.stop();
  }, [animateBadge, numericValue, reduced, scope]);

  if (numericValue <= 0) return null;
  return (
    <m.span
      ref={scope}
      className={className}
      variants={reduced ? reducedFadeVariants : scaleVariants}
      initial={reduced || !appeared ? false : 'hidden'}
      animate="visible"
      data-motion-attention="count"
      data-motion-value={numericValue}
      {...props}
    >
      {prefix}
      {format(numericValue)}
    </m.span>
  );
}

/** Interpolates from the previous value; first render is already settled. */
export function MotionNumber({
  value = 0,
  format = Math.round,
  initialValue,
  duration = MOTION_DURATION.data,
  reduced: reducedOverride,
  className,
  ...props
}) {
  const numericValue = Number(value) || 0;
  const systemReduced = useReducedMotionSync();
  const reduced = systemReduced || Boolean(reducedOverride);
  const startingValue = reduced || initialValue == null ? numericValue : Number(initialValue) || 0;
  const motionValue = useMotionValue(startingValue);
  const textRef = useRef(null);
  const formatRef = useRef(format);
  formatRef.current = format;

  useMotionValueEvent(motionValue, 'change', (latest) => {
    if (textRef.current) textRef.current.textContent = String(formatRef.current(latest));
  });

  useEffect(() => {
    if (reduced) {
      motionValue.set(numericValue);
      if (textRef.current) textRef.current.textContent = String(formatRef.current(numericValue));
      return undefined;
    }
    if (Object.is(motionValue.get(), numericValue)) {
      if (textRef.current) textRef.current.textContent = String(formatRef.current(numericValue));
      return undefined;
    }
    const controls = animate(motionValue, numericValue, { ...motionTransition.data, duration });
    return () => controls.stop();
  }, [duration, motionValue, numericValue, reduced]);

  return (
    <span className={className} aria-label={String(format(numericValue))} {...props}>
      <span ref={textRef} aria-hidden="true">{format(startingValue)}</span>
    </span>
  );
}
