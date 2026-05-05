import React, { useEffect, useRef } from 'react';

// Lightweight modal-confirm. Replaces ``window.confirm`` so the dialog
// matches the app's design tokens (no Chrome-style native chrome) and
// can be dismissed with Esc / click-outside.
//
// Usage: render ``<ConfirmDialog>`` only when ``open === true`` and pass
// the action label so the primary button reads "Delete" / "Discard"
// rather than a generic "OK".
const ConfirmDialog = ({
  open,
  title,
  detail,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  destructive = false,
  onConfirm,
  onCancel,
}) => {
  const confirmRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') onCancel?.();
      if (e.key === 'Enter') onConfirm?.();
    };
    document.addEventListener('keydown', onKey);
    // Autofocus the destructive primary so Enter triggers it without a
    // detour through the cancel button.
    confirmRef.current?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onCancel, onConfirm]);

  if (!open) return null;

  return (
    <div
      className="cp-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="cp-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel?.();
      }}
    >
      <div className="cp-modal">
        {title ? (
          <div id="cp-modal-title" className="cp-modal-title">
            {title}
          </div>
        ) : null}
        {detail ? <div className="cp-modal-detail">{detail}</div> : null}
        <div className="cp-modal-actions">
          <button type="button" className="cp-btn-ghost" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            type="button"
            className={destructive ? 'cp-btn-danger' : 'cp-btn-primary'}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
};

export default ConfirmDialog;
