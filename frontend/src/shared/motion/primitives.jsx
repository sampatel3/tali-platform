import React, {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
} from 'react';
import {
  AnimatePresence,
  LayoutGroup,
  animate,
  m,
  useInView,
  useMotionValue,
  useMotionValueEvent,
} from 'motion/react';

import {
  disclosureVariants,
  fadeVariants,
  listItemVariants,
  motionTransition,
  reducedFadeVariants,
} from './presets';
import { MOTION_DURATION } from './tokens';
import { useReducedMotionSync } from './useReducedMotionSync';

const cx = (...values) => values.filter(Boolean).join(' ');

/** A true, once-only in-view entrance for narrative/marketing content. */
export function Reveal({
  children,
  className,
  delay = 0,
  y = 12,
  amount = 0.2,
  once = true,
  reduced: reducedOverride,
  ...props
}) {
  const ref = useRef(null);
  const inView = useInView(ref, { amount, once });
  const systemReduced = useReducedMotionSync();
  const reduced = systemReduced || Boolean(reducedOverride);
  const visible = reduced || inView;

  return (
    <m.div
      ref={ref}
      className={className}
      initial={false}
      animate={visible ? { opacity: 1, y: 0 } : { opacity: 0, y }}
      transition={reduced ? motionTransition.instant : { ...motionTransition.reveal, delay }}
      {...props}
    >
      {children}
    </m.div>
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
export function MotionList({ children, className, initial = false, ...props }) {
  const reduced = useReducedMotionSync();
  return (
    <LayoutGroup>
      <m.div layout={reduced ? false : true} className={className} {...props}>
        <AnimatePresence initial={initial} mode={reduced ? 'sync' : 'popLayout'}>
          {children}
        </AnimatePresence>
      </m.div>
    </LayoutGroup>
  );
}

export function MotionListItem({ children, className, index = 0, density = 'default', ...props }) {
  const reduced = useReducedMotionSync();
  return (
    <m.div
      layout={reduced ? false : true}
      className={className}
      custom={{ index, density }}
      variants={reduced ? reducedFadeVariants : listItemVariants}
      initial={false}
      animate="visible"
      exit="exit"
      transition={{ layout: reduced ? motionTransition.instant : motionTransition.layout }}
      {...props}
    >
      {children}
    </m.div>
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
  const formatRef = useRef(format);
  formatRef.current = format;
  const [rendered, setRendered] = useState(() => format(startingValue));

  useMotionValueEvent(motionValue, 'change', (latest) => setRendered(formatRef.current(latest)));

  useEffect(() => {
    if (reduced) {
      motionValue.set(numericValue);
      setRendered(formatRef.current(numericValue));
      return undefined;
    }
    const controls = animate(motionValue, numericValue, { ...motionTransition.data, duration });
    return () => controls.stop();
  }, [duration, motionValue, numericValue, reduced]);

  return (
    <span className={className} aria-label={String(format(numericValue))} {...props}>
      <span aria-hidden="true">{rendered}</span>
    </span>
  );
}
