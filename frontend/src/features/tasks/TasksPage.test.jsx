import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const showToast = vi.fn();

vi.mock('../../shared/api', () => ({
  tasks: {
    list: vi.fn(),
  },
}));

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast }),
}));

import { tasks as tasksApi } from '../../shared/api';
import { TasksPage } from './TasksPage';

describe('TasksPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    tasksApi.list.mockResolvedValue({
      data: [
        {
          id: 12,
          task_key: 'ai_full_stack_readiness',
          name: 'AI Full Stack Readiness',
          role: 'AI Full Stack Engineer',
          description: 'Stabilize a repo and explain AI-assisted tradeoffs.',
          duration_minutes: 60,
          difficulty: 'medium',
          repo_structure: { files: { 'README.md': '# Task' } },
          evaluation_rubric: { delivery: {}, ai_judgment: {} },
        },
      ],
    });
  });

  it('renders the read-only task library with a full-page preview link', async () => {
    const onNavigate = vi.fn();
    render(<TasksPage onNavigate={onNavigate} />);

    await waitFor(() => {
      expect(screen.getByText(/AI Full Stack Readiness/i)).toBeInTheDocument();
    });

    const previewLink = screen.getByRole('link', { name: /Preview as candidate/i });
    expect(previewLink).toHaveAttribute('target', '_blank');
    expect(previewLink).toHaveAttribute('href', '/tasks/12/preview');

    const roleFilter = screen.getByRole('group', { name: 'Filter tasks by role' });
    expect(roleFilter).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'All roles' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'AI Full Stack Engineer' }));
    expect(screen.getByRole('button', { name: 'AI Full Stack Engineer' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('shows a recoverable error instead of an empty library when loading fails', async () => {
    tasksApi.list
      .mockRejectedValueOnce({
        response: { data: { detail: 'The task library is temporarily unavailable.' } },
      })
      .mockResolvedValueOnce({
        data: [
          {
            id: 12,
            name: 'AI Full Stack Readiness',
            role: 'AI Full Stack Engineer',
          },
        ],
      });

    render(<TasksPage />);

    const error = await screen.findByRole('alert');
    expect(error).toHaveTextContent('We couldn’t load the task library.');
    expect(error).toHaveTextContent('The task library is temporarily unavailable.');
    expect(screen.queryByText('No tasks in the library yet.')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => {
      expect(screen.getByText('AI Full Stack Readiness')).toBeInTheDocument();
    });
    expect(tasksApi.list).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
