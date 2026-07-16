import {
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { createScopedAnimate, MotionConfigContext } from 'motion/react';

import { useReducedMotionSync } from './useReducedMotionSync';

/**
 * The full Motion `useAnimate` hook installs its own reduced-motion media-query
 * listener even though every Taali surface already uses the subscription-backed
 * shared preference. Keep the same scoped animation API while using that single
 * source of truth and honouring MotionConfig's always/never/user policy.
 */
export function useScopedAnimate() {
  const [scope] = useState(() => ({ current: null, animations: [] }));
  const systemReduced = useReducedMotionSync();
  const { reducedMotion, skipAnimations } = useContext(MotionConfigContext);
  const reduceMotion = reducedMotion === 'never'
    ? false
    : reducedMotion === 'always' || systemReduced;
  const animate = useMemo(() => createScopedAnimate({
    scope,
    reduceMotion,
    skipAnimations,
  }), [reduceMotion, scope, skipAnimations]);

  useEffect(() => () => {
    scope.animations.forEach((animation) => animation.stop());
    scope.animations.length = 0;
  }, [scope]);

  return [scope, animate];
}

export default useScopedAnimate;
