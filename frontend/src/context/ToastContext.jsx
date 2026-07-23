import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import {
  AnimatePresence,
  m,
  motionTransition,
  toastVariants,
} from '../shared/motion';

const ToastContext = createContext(null);

// Toasts auto-dismiss after 5s, but they're also the only place users
// learn that something happened (a candidate synced, a role was created,
// a Workable run finished). Once dismissed they're gone, so we mirror
// every toast into a session-scoped activity log that the Home page
// surfaces as "Platform updates" — chatty by nature, hidden by default.
const ACTIVITY_CAP = 200;

// Heuristic categoriser. Keeps the log filterable without forcing every
// existing showToast call site to learn a new arg. Anything that mentions
// a candidate/role/sync/import is treated as routine "platform" chatter
// (hidden by default on Home); errors and explicit "decision" mentions
// stay visible.
const inferActivityKind = (message, type) => {
  if (type === 'error') return 'error';
  const text = String(message || '').toLowerCase();
  if (/\b(candidate|application|cv|invit|sync|import|workable)\b/.test(text)) return 'sync';
  if (/\b(role|job)\b/.test(text)) return 'role';
  if (/\b(decision|approve|override|teach|snooz)\b/.test(text)) return 'decision';
  if (type === 'success') return 'success';
  return 'info';
};

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [activities, setActivities] = useState([]);
  // Error notices persist until dismissed. Keep an active-key index so
  // overlapping failures cannot stack the same message (or duplicate it in
  // Platform updates) before React has committed the first state update.
  const activeToastKeysRef = useRef(new Set());
  const toastKeysByIdRef = useRef(new Map());
  // Auto-dismiss timers, keyed by toast id. A timer that outlives the
  // provider would set state on a dead tree (and in jsdom tests, fire after
  // the environment is torn down), so every pending id is tracked here and
  // cleared on early dismissal or unmount.
  const dismissTimersRef = useRef(new Map());

  const dismiss = useCallback((id) => {
    const key = toastKeysByIdRef.current.get(id);
    if (key) activeToastKeysRef.current.delete(key);
    toastKeysByIdRef.current.delete(id);
    const timer = dismissTimersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      dismissTimersRef.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  useEffect(() => {
    const timers = dismissTimersRef.current;
    return () => {
      timers.forEach((timer) => clearTimeout(timer));
      timers.clear();
    };
  }, []);

  const showToast = useCallback((message, type = 'info') => {
    const text = String(message);
    const key = `${type}:${text}`;
    if (type === 'error' && activeToastKeysRef.current.has(key)) return;

    const id = Date.now() + Math.random();
    activeToastKeysRef.current.add(key);
    toastKeysByIdRef.current.set(id, key);
    setToasts((prev) => [...prev, { id, key, message: text, type }]);
    setActivities((prev) => {
      const entry = {
        id,
        message: text,
        type,
        kind: inferActivityKind(text, type),
        createdAt: new Date().toISOString(),
      };
      const next = [entry, ...prev];
      return next.length > ACTIVITY_CAP ? next.slice(0, ACTIVITY_CAP) : next;
    });
    // Errors are frequently the sole feedback a user gets that an action
    // failed. Auto-dismissing them after 5s means a recruiter who glanced
    // away never learns something broke, so error toasts persist until they
    // explicitly dismiss. Every other severity still auto-clears.
    if (type !== 'error') {
      const timer = setTimeout(() => {
        dismissTimersRef.current.delete(id);
        dismiss(id);
      }, 5000);
      dismissTimersRef.current.set(id, timer);
    }
  }, [dismiss]);

  const clearActivities = useCallback(() => {
    setActivities([]);
  }, []);

  return (
    <ToastContext.Provider value={{
      showToast,
      toasts,
      dismiss,
      activities,
      clearActivities,
    }}>
      {children}
      <ToastContainer toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

// Map toast variants onto the Taali semantic tokens defined in index.css.
// Body text always uses --ink for readability; the variant only colours the
// surface, border, and the small accent dot. See /dev/toasters for the
// full design comparison and rationale.
const VARIANT_TOKENS = {
  success: {
    bg: 'var(--taali-success-soft)',
    border: 'var(--taali-success-border)',
    accent: 'var(--taali-success)',
  },
  error: {
    bg: 'var(--taali-danger-soft)',
    border: 'var(--taali-danger-border)',
    accent: 'var(--taali-danger)',
  },
  warning: {
    bg: 'var(--taali-warning-soft)',
    border: 'var(--taali-warning-border)',
    accent: 'var(--taali-warning)',
  },
  info: {
    bg: 'var(--taali-info-soft)',
    border: 'var(--taali-info-border)',
    accent: 'var(--taali-info)',
  },
};

function ToastContainer({ toasts, onDismiss }) {
  return (
    <div
      className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2 max-w-[min(400px,90vw)]"
      role="region"
      aria-label="Notifications"
    >
      <AnimatePresence initial={false} mode="popLayout">
        {toasts.map((t) => {
          const tokens = VARIANT_TOKENS[t.type] || VARIANT_TOKENS.info;
          return (
            <m.div
              key={t.id}
              layout="position"
              variants={toastVariants}
              initial="hidden"
              animate="visible"
              exit="exit"
              transition={{ layout: motionTransition.layout }}
              className="rounded-lg border px-4 py-3 shadow-sm text-sm"
              style={{
                background: tokens.bg,
                borderColor: tokens.border,
                color: 'var(--ink)',
              }}
              role={t.type === 'error' ? 'alert' : 'status'}
            >
              <p className="break-words">
                <span
                  aria-hidden="true"
                  className="inline-block h-2 w-2 rounded-full mr-2 align-middle"
                  style={{ background: tokens.accent }}
                />
                {t.message}
              </p>
              <button
                type="button"
                onClick={() => onDismiss(t.id)}
                className="taali-text-btn mt-2"
              >
                Dismiss
              </button>
            </m.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    return {
      showToast: () => {},
      toasts: [],
      dismiss: () => {},
      activities: [],
      clearActivities: () => {},
    };
  }
  return ctx;
}
