import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ConfirmActionDialog } from './ConfirmActionDialog';

describe('ConfirmActionDialog', () => {
  it('ignores backdrop and Escape dismissal while an action is loading', () => {
    const onClose = vi.fn();
    const props = {
      open: true,
      title: 'Run pre-screen',
      loading: true,
      onClose,
      onConfirm: vi.fn(),
    };
    const { rerender } = render(<ConfirmActionDialog {...props} />);

    const dialog = screen.getByRole('dialog', { name: 'Run pre-screen' });
    const backdrop = dialog.parentElement?.parentElement;

    fireEvent.keyDown(document, { key: 'Escape' });
    fireEvent.mouseDown(backdrop);
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();

    rerender(<ConfirmActionDialog {...props} loading={false} />);
    fireEvent.keyDown(document, { key: 'Escape' });

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
