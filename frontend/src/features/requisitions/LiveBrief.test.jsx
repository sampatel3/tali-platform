import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { listDraftWithPendingItem, LiveBrief } from './LiveBrief';

describe('listDraftWithPendingItem', () => {
  it('trims and appends pending text without mutating existing chips', () => {
    const existing = ['Build APIs'];

    expect(listDraftWithPendingItem(existing, '  Own reliability  ')).toEqual([
      'Build APIs',
      'Own reliability',
    ]);
    expect(existing).toEqual(['Build APIs']);
  });

  it('does not append blank pending text', () => {
    expect(listDraftWithPendingItem(['Build APIs'], '   ')).toEqual(['Build APIs']);
  });
});

describe('LiveBrief list editing', () => {
  it('saves text still in the add-item input when Save is clicked directly', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const template = {
      sections: [
        {
          key: 'context',
          label: 'Hiring context',
          fields: [
            {
              key: 'responsibilities',
              label: 'Key responsibilities',
              type: 'list',
              required: true,
              question: 'What are the key responsibilities?',
            },
          ],
        },
      ],
    };
    const brief = {
      completeness: 0,
      custom_fields: {},
      gaps: [{ key: 'responsibilities', label: 'Key responsibilities', section: 'context' }],
    };

    render(
      <LiveBrief
        template={template}
        brief={brief}
        onSave={onSave}
        savingKey={null}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '—' }));
    fireEvent.change(screen.getByPlaceholderText('Add an item, press Enter'), {
      target: { value: 'Own production reliability' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save/i }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(
        'responsibilities',
        ['Own production reliability'],
        true,
      );
    });
  });

  it('keeps pending text and the editor open when the save fails', async () => {
    const onSave = vi.fn().mockResolvedValue(false);
    const template = {
      sections: [{
        key: 'context',
        label: 'Hiring context',
        fields: [{
          key: 'responsibilities',
          label: 'Key responsibilities',
          type: 'list',
          required: true,
        }],
      }],
    };
    const brief = {
      completeness: 0,
      custom_fields: {},
      gaps: [{ key: 'responsibilities', label: 'Key responsibilities', section: 'context' }],
    };

    render(
      <LiveBrief
        template={template}
        brief={brief}
        onSave={onSave}
        savingKey={null}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '—' }));
    const input = screen.getByRole('textbox', { name: 'Add Key responsibilities item' });
    fireEvent.change(input, { target: { value: 'Own production reliability' } });
    fireEvent.click(screen.getByRole('button', { name: /Save/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(screen.getByRole('textbox', { name: 'Add Key responsibilities item' })).toHaveValue(
      'Own production reliability',
    );
  });
});
