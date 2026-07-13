import { useSyncExternalStore } from 'react';

export const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

const listeners = new Set();
let mediaQuery = null;
let listening = false;

const getMediaQuery = () => {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null;
  if (!mediaQuery) mediaQuery = window.matchMedia(REDUCED_MOTION_QUERY);
  return mediaQuery;
};

const emitPreferenceChange = () => {
  listeners.forEach((listener) => listener());
};

const startListening = () => {
  const query = getMediaQuery();
  if (!query || listening) return;
  if (typeof query.addEventListener === 'function') {
    query.addEventListener('change', emitPreferenceChange);
  } else {
    query.addListener(emitPreferenceChange);
  }
  listening = true;
};

const stopListening = () => {
  if (!listening || !mediaQuery) return;
  if (typeof mediaQuery.removeEventListener === 'function') {
    mediaQuery.removeEventListener('change', emitPreferenceChange);
  } else {
    mediaQuery.removeListener(emitPreferenceChange);
  }
  listening = false;
  // Re-resolve the query for a future subscriber. Besides keeping the module
  // testable, this avoids retaining a Window-owned object after the last
  // Motion surface unmounts.
  mediaQuery = null;
};

const subscribe = (onStoreChange) => {
  listeners.add(onStoreChange);
  startListening();
  return () => {
    listeners.delete(onStoreChange);
    if (listeners.size === 0) stopListening();
  };
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
