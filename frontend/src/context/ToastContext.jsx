import React, { createContext, useCallback, useContext, useState } from 'react';

const ToastContext = createContext(null);

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const showToast = useCallback((message, type = 'info') => {
    const id = Date.now();
    setToasts((prev) => [...prev, { id, message: String(message), type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 5000);
  }, []);

  const dismiss = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ showToast, toasts, dismiss }}>
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
  if (!ctx) return { showToast: (msg) => window.alert(msg), toasts: [], dismiss: () => {} };
  return ctx;
}
