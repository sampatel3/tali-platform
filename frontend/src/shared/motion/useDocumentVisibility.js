import { useSyncExternalStore } from 'react';

const listeners = new Set();
let listening = false;

const emitVisibilityChange = () => {
  listeners.forEach((listener) => listener());
};

const subscribe = (listener) => {
  listeners.add(listener);
  if (!listening && typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', emitVisibilityChange);
    listening = true;
  }
  return () => {
    listeners.delete(listener);
    if (listening && listeners.size === 0 && typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', emitVisibilityChange);
      listening = false;
    }
  };
};

const getSnapshot = () => typeof document === 'undefined' || document.visibilityState !== 'hidden';

/** One shared document listener pauses ambient loops while the tab is hidden. */
export const useDocumentVisibility = () => (
  useSyncExternalStore(subscribe, getSnapshot, () => true)
);

export default useDocumentVisibility;
