import React, { useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Button, Dialog, Sheet } from '../shared/ui/TaaliPrimitives';

function TwoSheetHarness() {
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);

  return (
    <div>
      <Button type="button" onClick={() => setLeftOpen(true)}>Open left</Button>
      <Button type="button" onClick={() => setRightOpen(true)}>Open right</Button>

      <Sheet
        open={leftOpen}
        onClose={() => setLeftOpen(false)}
        side="left"
        title="Left sheet"
        footer={<div>Left footer</div>}
      >
        <div>Left content</div>
      </Sheet>

      <Sheet
        open={rightOpen}
        onClose={() => setRightOpen(false)}
        title="Right sheet"
        footer={<div>Right footer</div>}
      >
        <div>Right content</div>
      </Sheet>
    </div>
  );
}

function DialogHarness() {
  const [open, setOpen] = useState(false);

  return (
    <div>
      <Button type="button" onClick={() => setOpen(true)}>Open dialog</Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Confirm action"
        footer={<Button type="button">Confirm</Button>}
      >
        <p>Dialog content</p>
      </Dialog>
    </div>
  );
}

describe('Sheet', () => {
  it('restores page scrolling after multiple sheets close in any order', () => {
    render(<TwoSheetHarness />);

    fireEvent.click(screen.getByRole('button', { name: 'Open left' }));
    expect(document.body.style.overflow).toBe('hidden');

    fireEvent.click(screen.getByRole('button', { name: 'Open right' }));
    expect(document.body.style.overflow).toBe('hidden');

    const leftDialog = screen.getByRole('dialog', { name: 'Left sheet' });
    fireEvent.click(within(leftDialog).getByRole('button', { name: 'Close' }));
    expect(document.body.style.overflow).toBe('hidden');

    const rightDialog = screen.getByRole('dialog', { name: 'Right sheet' });
    fireEvent.click(within(rightDialog).getByRole('button', { name: 'Close' }));
    expect(document.body.style.overflow).toBe('');
  });

  it('restores focus and unmounts after its exit when Escape closes it', async () => {
    render(<TwoSheetHarness />);

    const trigger = screen.getByRole('button', { name: 'Open right' });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = screen.getByRole('dialog', { name: 'Right sheet' });
    expect(within(dialog).getByRole('button', { name: 'Close' })).toHaveFocus();

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(document.body.style.overflow).toBe('');
    expect(trigger).toHaveFocus();
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Right sheet' })).not.toBeInTheDocument();
    });
  });
});

describe('Dialog', () => {
  it('closes from its backdrop and restores the trigger after the exit', async () => {
    render(<DialogHarness />);

    const trigger = screen.getByRole('button', { name: 'Open dialog' });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = screen.getByRole('dialog', { name: 'Confirm action' });
    expect(document.body.style.overflow).toBe('hidden');
    expect(within(dialog).getByRole('button', { name: 'Close' })).toHaveFocus();

    const backdrop = dialog.parentElement?.parentElement;
    fireEvent.mouseDown(backdrop);

    expect(document.body.style.overflow).toBe('');
    expect(trigger).toHaveFocus();
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Confirm action' })).not.toBeInTheDocument();
    });
  });
});
