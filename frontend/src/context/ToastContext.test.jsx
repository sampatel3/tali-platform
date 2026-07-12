import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider, useToast } from './ToastContext';

function ToastHarness() {
  const { activities, showToast, toasts } = useToast();

  return (
    <div>
      <button type="button" onClick={() => showToast('Candidate synced', 'info')}>
        Show info
      </button>
      <button type="button" onClick={() => showToast('Sync failed', 'error')}>
        Show error
      </button>
      <output data-testid="toast-count">{toasts.length}</output>
      <output data-testid="activity-count">{activities.length}</output>
    </div>
  );
}

const renderToasts = () => render(
  <ToastProvider>
    <ToastHarness />
  </ToastProvider>,
);

afterEach(() => {
  vi.useRealTimers();
});

describe('ToastProvider', () => {
  it('keeps severity roles and dismisses an error toast on request', async () => {
    renderToasts();

    expect(screen.getByRole('region', { name: 'Notifications' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show error' }));

    expect(screen.getByRole('alert')).toHaveTextContent('Sync failed');
    expect(screen.getByTestId('toast-count')).toHaveTextContent('1');
    expect(screen.getByTestId('activity-count')).toHaveTextContent('1');

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));

    expect(screen.getByTestId('toast-count')).toHaveTextContent('0');
    await waitFor(() => {
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
  });

  it('auto-dismisses routine notices after five seconds but keeps errors', () => {
    vi.useFakeTimers();
    renderToasts();

    fireEvent.click(screen.getByRole('button', { name: 'Show info' }));
    fireEvent.click(screen.getByRole('button', { name: 'Show error' }));

    const notifications = within(screen.getByRole('region', { name: 'Notifications' }));
    expect(notifications.getByRole('status')).toHaveTextContent('Candidate synced');
    expect(notifications.getByRole('alert')).toHaveTextContent('Sync failed');
    expect(screen.getByTestId('toast-count')).toHaveTextContent('2');

    act(() => {
      vi.advanceTimersByTime(4999);
    });
    expect(screen.getByTestId('toast-count')).toHaveTextContent('2');

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(screen.getByTestId('toast-count')).toHaveTextContent('1');
    expect(notifications.getByRole('alert')).toHaveTextContent('Sync failed');
  });
});
