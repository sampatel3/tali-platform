import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const showToast = vi.fn();

vi.mock('../../shared/api', () => ({
  tasks: {
    list: vi.fn(),
    facets: vi.fn(),
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
    tasksApi.facets.mockResolvedValue({
      data: { roles: [], difficulties: [], task_types: [], has_more: false, next_offset: null },
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

  it('loads later task pages explicitly without duplicating the first page', async () => {
    const firstPage = Array.from({ length: 24 }, (_, index) => ({
      id: index + 1,
      name: `Task ${index + 1}`,
      role: 'Engineering',
    }));
    tasksApi.list
      .mockResolvedValueOnce({ data: firstPage })
      .mockResolvedValueOnce({ data: [{ id: 25, name: 'Task 25', role: 'Engineering' }] });

    render(<TasksPage />);
    const loadMore = await screen.findByRole('button', { name: /Load more tasks \(24 shown\)/i });
    fireEvent.click(loadMore);

    await waitFor(() => expect(screen.getByText('Task 25')).toBeInTheDocument());
    expect(tasksApi.list).toHaveBeenNthCalledWith(1, {
      limit: 24,
      offset: 0,
      search: undefined,
      role: undefined,
      difficulty: undefined,
      task_type: undefined,
    });
    expect(tasksApi.list).toHaveBeenNthCalledWith(2, {
      limit: 24,
      offset: 24,
      search: undefined,
      role: undefined,
      difficulty: undefined,
      task_type: undefined,
    });
    expect(screen.queryByRole('button', { name: /Load more tasks/i })).not.toBeInTheDocument();
  });

  it('searches the complete server catalogue instead of only loaded rows', async () => {
    tasksApi.list
      .mockResolvedValueOnce({ data: [] })
      .mockResolvedValueOnce({ data: [{ id: 99, name: 'Remote Match', role: 'Engineering' }] });
    render(<TasksPage />);
    await screen.findByText('No tasks in the library yet.');

    fireEvent.change(screen.getByPlaceholderText('Search tasks, stacks, or scenarios'), {
      target: { value: 'Remote Match' },
    });

    await waitFor(() => expect(screen.getByText('Remote Match')).toBeInTheDocument());
    expect(tasksApi.list).toHaveBeenLastCalledWith({
      limit: 24,
      offset: 0,
      search: 'Remote Match',
      role: undefined,
      difficulty: undefined,
      task_type: undefined,
    });
  });

  it('uses SQL facets and server filtering for values absent from page one', async () => {
    tasksApi.facets.mockResolvedValue({
      data: {
        roles: ['AI Full Stack Engineer', 'Later-page role'],
        difficulties: ['medium'],
        task_types: ['repo'],
        has_more: false,
        next_offset: null,
      },
    });
    tasksApi.list
      .mockResolvedValueOnce({ data: [] })
      .mockResolvedValueOnce({ data: [{ id: 77, name: 'Later task', role: 'Later-page role' }] });
    render(<TasksPage />);
    await screen.findByText('No tasks in the library yet.');

    fireEvent.click(screen.getByRole('button', { name: 'Role · All' }));
    fireEvent.click(screen.getByRole('option', { name: 'Later Page Role' }));

    await waitFor(() => expect(screen.getByText('Later task')).toBeInTheDocument());
    expect(tasksApi.list).toHaveBeenLastCalledWith({
      limit: 24,
      offset: 0,
      search: undefined,
      role: 'Later-page role',
      difficulty: undefined,
      task_type: undefined,
    });
  });
});
