import { useSyncExternalStore } from 'react';

export const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

const getMediaQuery = () => {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null;
  return window.matchMedia(REDUCED_MOTION_QUERY);
};

const subscribe = (onStoreChange) => {
  const query = getMediaQuery();
  if (!query) return () => {};
  if (typeof query.addEventListener === 'function') {
    query.addEventListener('change', onStoreChange);
    return () => query.removeEventListener('change', onStoreChange);
  }
  query.addListener(onStoreChange);
  return () => query.removeListener(onStoreChange);
};

const getSnapshot = () => Boolean(getMediaQuery()?.matches);

/**
 * Synchronous, subscription-backed reduced-motion preference for imperative
 * timelines, number interpolation, and native scrolling. MotionConfig handles
 * declarative transforms; this hook handles logic that must settle instantly.
 */
export const useReducedMotionSync = () => (
  useSyncExternalStore(subscribe, getSnapshot, () => false)
);

export const prefersReducedMotion = () => getSnapshot();

export const motionSafeScrollBehavior = (behavior = 'smooth') => (
  behavior === 'smooth' && prefersReducedMotion() ? 'auto' : behavior
);
