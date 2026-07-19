import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { publicIntakeApi } from '../requisitions/api';
import { REQUISITION_ATTACHMENT_ACCEPT } from '../requisitions/requisitionAttachments';
import { ClientIntakePage } from './ClientIntakePage';

vi.mock('../requisitions/api', () => ({
  publicIntakeApi: {
    get: vi.fn(),
    chat: vi.fn(),
    submit: vi.fn(),
  },
}));

const createObjectURL = vi.fn();
const revokeObjectURL = vi.fn();

const initialIntake = {
  organization_name: 'Acme',
  messages: [{ role: 'assistant', content: 'Tell me about the role.' }],
  captured: {},
  gaps: [],
  completeness: 0,
  status: 'draft',
};

const renderIntake = async () => {
  const result = render(<ClientIntakePage />);
  await screen.findByRole('heading', { name: /tell us about the role/i });
  return result;
};

describe('ClientIntakePage attachment policy', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState(null, '', '/intake/public-token');
    publicIntakeApi.get.mockResolvedValue(initialIntake);
    createObjectURL.mockReturnValue('blob:intake-preview');
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: revokeObjectURL,
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    });
  });

  it('uses the shared accept list and validates before staging files', async () => {
    const { container } = await renderIntake();
    const input = container.querySelector('input[type="file"]');

    expect(input).toHaveAttribute('accept', REQUISITION_ATTACHMENT_ACCEPT);
    expect(input.accept).not.toContain('image/*');

    const tooMany = Array.from(
      { length: 7 },
      (_, index) => new File(['note'], `note-${index}.txt`, { type: 'text/plain' }),
    );
    fireEvent.change(input, { target: { files: tooMany } });
    expect(await screen.findByText(/attach up to 6 files/i)).toBeInTheDocument();
    expect(createObjectURL).not.toHaveBeenCalled();

    const docx = new File(['spec'], 'role.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    });
    const textAlias = new File(['notes'], 'brief.text', { type: 'text/plain' });
    fireEvent.change(input, { target: { files: [docx, textAlias] } });

    expect(await screen.findByText('role.docx')).toBeInTheDocument();
    expect(screen.getByText('brief.text')).toBeInTheDocument();
    expect(screen.queryByText(/attach up to 6 files/i)).not.toBeInTheDocument();
  });

  it('restores an image preview on a safe upload error and revokes it after retry succeeds', async () => {
    publicIntakeApi.chat
      .mockRejectedValueOnce({
        response: { status: 415, data: { detail: 'Only supported attachment formats can be processed.' } },
      })
      .mockResolvedValueOnce({
        ...initialIntake,
        messages: [
          ...initialIntake.messages,
          { role: 'user', content: '', attachments: [{ name: 'role.png', kind: 'image' }] },
          { role: 'assistant', content: 'Thanks — I captured that.' },
        ],
      });
    const { container } = await renderIntake();
    const input = container.querySelector('input[type="file"]');
    const image = new File(['image'], 'role.png', { type: 'image/png' });

    fireEvent.change(input, { target: { files: [image] } });
    expect(await screen.findByRole('img', { name: 'role.png' })).toHaveAttribute(
      'src',
      'blob:intake-preview',
    );

    fireEvent.click(screen.getByRole('button', { name: /Send 1 attachment/i }));

    expect(await screen.findByText('Only supported attachment formats can be processed.')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'role.png' })).toBeInTheDocument();
    expect(revokeObjectURL).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /Send 1 attachment/i }));

    await waitFor(() => expect(publicIntakeApi.chat).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith('blob:intake-preview'));
    expect(screen.queryByRole('img', { name: 'role.png' })).not.toBeInTheDocument();
  });
});
