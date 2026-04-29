import React from 'react';

import { Button, Dialog } from './TaaliPrimitives';

/**
 * Generic confirmation dialog for batch-action buttons.
 *
 * Usage:
 *   <ConfirmActionDialog
 *     open={open}
 *     title="Pre-screen new candidates"
 *     description="This will run pre-screen on 23 candidates that haven't been pre-screened yet."
 *     bullets={[ {label: 'Will pre-screen', value: 23}, {label: 'Skipped (no CV)', value: 4} ]}
 *     warning="Re-running will overwrite the current pre-screen result."
 *     confirmLabel="Run pre-screen"
 *     loading={loading}
 *     onClose={() => setOpen(false)}
 *     onConfirm={handleRun}
 *   />
 *
 * Designed to be paired with a dry-run API call: open the dialog, fire the
 * dry-run, render the resulting counts in `bullets`, then on confirm call
 * the action without dry_run.
 */
export function ConfirmActionDialog({
  open,
  title,
  description,
  bullets = [],
  warning = null,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  loading = false,
  loadingLabel = 'Running...',
  variant = 'primary',
  disabled = false,
  onClose,
  onConfirm,
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      footer={(
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            type="button"
            variant={variant}
            disabled={disabled || loading}
            onClick={onConfirm}
          >
            {loading ? loadingLabel : confirmLabel}
          </Button>
        </div>
      )}
    >
      <div className="space-y-3 text-sm">
        {bullets && bullets.length ? (
          <ul className="confirm-action__bullets">
            {bullets.map((b, i) => (
              <li key={`${b.label}-${i}`} className="confirm-action__bullet">
                <span className="confirm-action__bullet-label">{b.label}</span>
                <span className="confirm-action__bullet-value">
                  {typeof b.value === 'number' ? b.value.toLocaleString() : b.value}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        {warning ? (
          <p className="confirm-action__warning" role="status">{warning}</p>
        ) : null}
      </div>
    </Dialog>
  );
}
