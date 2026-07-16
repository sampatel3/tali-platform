import React, { useRef } from 'react';

import { Button, Dialog } from '../../shared/ui/TaaliPrimitives';

// Lightweight modal-confirm. Replaces ``window.confirm`` so the dialog
// matches the app's design tokens (no Chrome-style native chrome) and
// can be dismissed with Esc / click-outside.
//
// Keep the component mounted and drive `open` so the shared Dialog can finish
// its exit. Pass the action label so the primary reads Delete/Discard rather
// than a generic OK.
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
  const cancelRef = useRef(null);

  return (
    <Dialog
      open={open}
      onClose={onCancel}
      title={title || 'Confirm'}
      initialFocusRef={confirmRef}
      panelClassName="max-w-[26.25rem]"
      footer={(
        <div className="cp-modal-actions">
          <Button ref={cancelRef} type="button" variant="ghost" onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button
            ref={confirmRef}
            type="button"
            variant={destructive ? 'danger' : 'primary'}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      )}
    >
      {detail ? <div className="cp-modal-detail">{detail}</div> : null}
    </Dialog>
  );
};

export default ConfirmDialog;
