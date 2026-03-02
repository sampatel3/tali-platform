import React, { useState } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Button, Sheet } from '../shared/ui/TaaliPrimitives';

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
});
