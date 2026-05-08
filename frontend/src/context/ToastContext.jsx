import React, { createContext, useCallback, useContext, useState } from 'react';

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

  const showToast = useCallback((message, type = 'info') => {
    const id = Date.now() + Math.random();
    const text = String(message);
    setToasts((prev) => [...prev, { id, message: text, type }]);
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
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 5000);
  }, []);

  const dismiss = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

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

function ToastContainer({ toasts, onDismiss }) {
  if (!toasts.length) return null;
  return (
    <div
      className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2 max-w-[min(400px,90vw)]"
      role="region"
      aria-label="Notifications"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`
            rounded-lg border-2 px-4 py-3 shadow-lg text-sm
            ${t.type === 'error' ? 'border-red-300 bg-red-50 text-red-900' : ''}
            ${t.type === 'success' ? 'border-green-300 bg-green-50 text-green-900' : ''}
            ${t.type === 'info' || !t.type ? 'border-[var(--taali-border)] bg-[var(--taali-surface)] text-[var(--taali-text)]' : ''}
          `}
        >
          <p className="break-words">{t.message}</p>
          <button
            type="button"
            onClick={() => onDismiss(t.id)}
            className="mt-2 text-xs font-medium underline focus:outline-none focus:ring-2 focus:ring-offset-1"
          >
            Dismiss
          </button>
        </div>
      ))}
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
