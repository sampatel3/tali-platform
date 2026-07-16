import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Select } from './TaaliPrimitives';

describe('Select trigger attributes', () => {
  it('forwards a disabled reason title to the interactive trigger', () => {
    render(
      <Select
        value="platform"
        disabled
        title="Your role permissions do not allow this change."
        aria-label="Assign hiring department"
      >
        <option value="platform">Platform</option>
        <option value="research">Research</option>
      </Select>,
    );

    const trigger = screen.getByRole('button', { name: 'Assign hiring department' });
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveAttribute(
      'title',
      'Your role permissions do not allow this change.',
    );

    fireEvent.click(trigger);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });
});
