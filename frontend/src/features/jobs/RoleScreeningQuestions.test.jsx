import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../shared/api', () => ({
  roles: {
    listScreeningQuestions: vi.fn(),
    createScreeningQuestion: vi.fn(),
    updateScreeningQuestion: vi.fn(),
    deleteScreeningQuestion: vi.fn(),
  },
}));

import { roles as rolesApi } from '../../shared/api';
import RoleScreeningQuestions from './RoleScreeningQuestions';

const existingQuestion = {
  id: 11,
  prompt: 'Are you authorized to work in the UAE?',
  kind: 'boolean',
  options: null,
  required: true,
  knockout: false,
  knockout_expected: null,
};

describe('RoleScreeningQuestions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    rolesApi.listScreeningQuestions.mockResolvedValue({ data: [existingQuestion] });
    rolesApi.createScreeningQuestion.mockImplementation(async (_roleId, payload) => ({
      data: { id: 12, ...payload },
    }));
    rolesApi.updateScreeningQuestion.mockImplementation(async (_roleId, id, payload) => ({
      data: { id, ...payload },
    }));
    rolesApi.deleteScreeningQuestion.mockResolvedValue({ data: null });
  });

  it('loads and manages deterministic screening questions through the role CRUD API', async () => {
    render(<RoleScreeningQuestions roleId={7} />);

    expect(await screen.findByText(existingQuestion.prompt)).toBeInTheDocument();
    expect(rolesApi.listScreeningQuestions).toHaveBeenCalledWith(7);
    expect(screen.getByText(/passing answers are never exposed to candidates/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Candidate-facing question'), {
      target: { value: 'Are you able to work Gulf Standard Time?' },
    });
    fireEvent.click(screen.getByLabelText('Deterministic knockout'));
    fireEvent.click(screen.getByRole('button', { name: 'Add question' }));

    await waitFor(() => expect(rolesApi.createScreeningQuestion).toHaveBeenCalledWith(7, {
      prompt: 'Are you able to work Gulf Standard Time?',
      kind: 'boolean',
      options: null,
      required: true,
      knockout: true,
      knockout_expected: ['yes'],
    }));
    expect(await screen.findByText('Are you able to work Gulf Standard Time?')).toBeInTheDocument();

    const existingRow = screen.getByText(existingQuestion.prompt).parentElement.parentElement;
    fireEvent.click(within(existingRow).getByRole('button', { name: 'Edit' }));
    fireEvent.change(screen.getByLabelText('Candidate-facing question'), {
      target: { value: 'Do you have UAE work authorization?' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save question' }));

    await waitFor(() => expect(rolesApi.updateScreeningQuestion).toHaveBeenCalledWith(
      7,
      11,
      expect.objectContaining({ prompt: 'Do you have UAE work authorization?' }),
    ));
    expect(await screen.findByText('Do you have UAE work authorization?')).toBeInTheDocument();

    const updatedRow = screen.getByText('Do you have UAE work authorization?').parentElement.parentElement;
    fireEvent.click(within(updatedRow).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(rolesApi.deleteScreeningQuestion).toHaveBeenCalledWith(7, 11));
    expect(screen.queryByText('Do you have UAE work authorization?')).not.toBeInTheDocument();
  });
});
