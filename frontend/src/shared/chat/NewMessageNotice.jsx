import { ArrowDown } from 'lucide-react';

import {
  AnimatePresence,
  fadeUpVariants,
  m,
  reducedFadeVariants,
  useReducedMotionSync,
} from '../motion';

const cx = (...values) => values.filter(Boolean).join(' ');

/**
 * A small, shared transcript affordance for updates that arrive while the
 * reader is away from the bottom. The live region is deliberately limited to
 * this one status instead of making the transcript itself live.
 */
export function NewMessageNotice({
  visible,
  onClick,
  className,
  controls,
  label = 'New agent update',
}) {
  const reduced = useReducedMotionSync();

  return (
    <div className={cx('tk-new-update-anchor', className)}>
      <span
        className="tk-new-update-status"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {visible ? label : ''}
      </span>
      <AnimatePresence initial={false}>
        {visible ? (
          <m.button
            key="new-agent-update"
            type="button"
            className="tk-new-update-btn"
            onClick={onClick}
            aria-controls={controls}
            variants={reduced ? reducedFadeVariants : fadeUpVariants}
            initial={reduced ? false : 'hidden'}
            animate="visible"
            exit="exit"
            data-motion-new-update="true"
          >
            <span>{label}</span>
            <ArrowDown size={14} aria-hidden="true" />
          </m.button>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
