import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
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
  });
});
