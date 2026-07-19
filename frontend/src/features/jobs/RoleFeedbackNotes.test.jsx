import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  listFeedbackNotes: vi.fn(),
  createFeedbackNote: vi.fn(),
}));

vi.mock('../../shared/api', () => ({
  roles: {
    listFeedbackNotes: mocks.listFeedbackNotes,
    createFeedbackNote: mocks.createFeedbackNote,
  },
}));

import RoleFeedbackNotes from './RoleFeedbackNotes';

beforeEach(() => {
  mocks.listFeedbackNotes.mockReset();
  mocks.createFeedbackNote.mockReset();
});

describe('RoleFeedbackNotes', () => {
  it('loads feedback and presents the newest note first', async () => {
    mocks.listFeedbackNotes.mockResolvedValue({
      data: [
        {
          id: 1,
          note: 'Older observation',
          author_name: 'Ari',
          created_at: '2026-07-14T09:00:00Z',
        },
        {
          id: 2,
          note: 'Newest observation',
          author_name: 'Mina',
          created_at: '2026-07-15T09:00:00Z',
        },
      ],
    });

    render(<RoleFeedbackNotes roleId={7} roleVersion={12} />);

    expect(await screen.findByText('Newest observation')).toBeInTheDocument();
    expect(mocks.listFeedbackNotes).toHaveBeenCalledWith(7);
    const notes = screen.getAllByRole('listitem');
    expect(notes[0]).toHaveTextContent('Newest observation');
    expect(notes[1]).toHaveTextContent('Older observation');
  });

  it('submits trimmed feedback with the rendered role version', async () => {
    const onRoleVersionChange = vi.fn();
    mocks.listFeedbackNotes.mockResolvedValue({ data: [] });
    mocks.createFeedbackNote.mockResolvedValue({
      data: {
        id: 3,
        note: 'Prioritize platform ownership',
        role_version: 13,
        created_at: '2026-07-16T09:00:00Z',
      },
    });

    render(
      <RoleFeedbackNotes
        roleId={7}
        roleVersion={12}
        onRoleVersionChange={onRoleVersionChange}
      />,
    );

    await screen.findByText('No feedback yet. Notes you add appear here.');
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '  Prioritize platform ownership  ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add feedback' }));

    await waitFor(() => {
      expect(mocks.createFeedbackNote).toHaveBeenCalledWith(
        7,
        'Prioritize platform ownership',
        12,
      );
    });
    expect(await screen.findByText('Prioritize platform ownership')).toBeInTheDocument();
    expect(onRoleVersionChange).toHaveBeenCalledWith(13);
    expect(screen.getByRole('textbox')).toHaveValue('');
  });

  it('surfaces an optimistic-concurrency conflict and refreshes through the parent', async () => {
    const onRoleConflict = vi.fn().mockResolvedValue(undefined);
    mocks.listFeedbackNotes.mockResolvedValue({ data: [] });
    mocks.createFeedbackNote.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: {
            code: 'ROLE_VERSION_CONFLICT',
            message: 'This job changed. Review version 14.',
          },
        },
      },
    });

    render(
      <RoleFeedbackNotes
        roleId={7}
        roleVersion={12}
        onRoleConflict={onRoleConflict}
      />,
    );

    await screen.findByText('No feedback yet. Notes you add appear here.');
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'New note' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add feedback' }));

    expect(await screen.findByText('This job changed. Review version 14.')).toBeInTheDocument();
    expect(onRoleConflict).toHaveBeenCalledTimes(1);
  });
});
