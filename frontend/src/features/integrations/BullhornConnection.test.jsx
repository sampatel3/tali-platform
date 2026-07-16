import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../shared/api', () => ({
  organizations: {
    connectBullhorn: vi.fn(),
    getBullhornStatus: vi.fn(),
    syncBullhorn: vi.fn(),
    getBullhornSyncStatus: vi.fn(),
    cancelBullhornSync: vi.fn(),
    getBullhornStageMap: vi.fn(),
    replaceBullhornStageMap: vi.fn(),
  },
}));

// The component tells the shared job-status context to track a Bullhorn sync so
// it shows in the global BackgroundJobsPanel. Mock the context (same convention
// as BackgroundJobsToaster.test) to assert that hand-off.
const useJobStatusMock = vi.fn();
vi.mock('../../contexts/JobStatusContext', () => ({
  useJobStatus: () => useJobStatusMock(),
}));

// The shared design primitives are decorative here — stub to keep the test on
// behaviour, not styling.
vi.mock('../../shared/ui/TaaliPrimitives', () => ({
  Spinner: () => <span data-testid="spinner" />,
}));
vi.mock('../../shared/ui/RecruiterDesignPrimitives', () => ({
  SyncPulse: () => <span data-testid="pulse" />,
  formatRelativeDateTime: (v) => String(v),
}));

import { organizations as orgsApi } from '../../shared/api';
import { BullhornConnection } from './BullhornConnection';

describe('BullhornConnection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    orgsApi.getBullhornStatus.mockResolvedValue({ data: {} });
    orgsApi.getBullhornStageMap.mockResolvedValue({
      data: { pipeline_stages: ['applied', 'review', 'advanced'], mappings: [], unmapped_statuses: [] },
    });
    // Default: a context with a spyable trackBullhornSync.
    useJobStatusMock.mockReturnValue({ trackBullhornSync: vi.fn() });
  });

  it('renders the credential connect form when not connected', () => {
    render(<BullhornConnection orgData={{ bullhorn_connected: false }} />);
    expect(screen.getByText('Bullhorn not connected')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('taali.api')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('OAuth client id')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('OAuth client secret')).toBeInTheDocument();
    // The password field exists and is a password input (never plain text).
    const pw = screen.getByPlaceholderText('Used once — never stored');
    expect(pw).toHaveAttribute('type', 'password');
    // Connect button present.
    expect(screen.getByRole('button', { name: /Connect Bullhorn/i })).toBeInTheDocument();
  });

  it('blocks connect until every field is filled', async () => {
    render(<BullhornConnection orgData={{ bullhorn_connected: false }} />);
    fireEvent.click(screen.getByRole('button', { name: /Connect Bullhorn/i }));
    await screen.findByText(/Enter the API username/i);
    expect(orgsApi.connectBullhorn).not.toHaveBeenCalled();
  });

  it('submits the connect payload with the password and clears the field on success', async () => {
    orgsApi.connectBullhorn.mockResolvedValue({ data: { status: 'connected' } });
    // Guard window.location.reload (jsdom throws "not implemented" otherwise).
    const reload = vi.fn();
    Object.defineProperty(window, 'location', {
      value: { ...window.location, reload },
      writable: true,
    });

    render(<BullhornConnection orgData={{ bullhorn_connected: false }} />);
    fireEvent.change(screen.getByPlaceholderText('taali.api'), { target: { value: 'api-user' } });
    fireEvent.change(screen.getByPlaceholderText('OAuth client id'), { target: { value: 'cid' } });
    fireEvent.change(screen.getByPlaceholderText('OAuth client secret'), { target: { value: 'csecret' } });
    fireEvent.change(screen.getByPlaceholderText('Used once — never stored'), { target: { value: 'pw-123' } });

    fireEvent.click(screen.getByRole('button', { name: /Connect Bullhorn/i }));

    await waitFor(() => expect(orgsApi.connectBullhorn).toHaveBeenCalledTimes(1));
    expect(orgsApi.connectBullhorn).toHaveBeenCalledWith({
      username: 'api-user',
      client_id: 'cid',
      client_secret: 'csecret',
      password: 'pw-123',
    });
    // Password field cleared post-connect (no lingering secret in state).
    await waitFor(() =>
      expect(screen.getByPlaceholderText('Used once — never stored')).toHaveValue(''),
    );
  });

  it('surfaces the backend connect error message', async () => {
    orgsApi.connectBullhorn.mockRejectedValue({
      response: { data: { detail: "The Bullhorn API user is missing the 'PUT' entitlement on Note." } },
    });
    render(<BullhornConnection orgData={{ bullhorn_connected: false }} />);
    fireEvent.change(screen.getByPlaceholderText('taali.api'), { target: { value: 'u' } });
    fireEvent.change(screen.getByPlaceholderText('OAuth client id'), { target: { value: 'c' } });
    fireEvent.change(screen.getByPlaceholderText('OAuth client secret'), { target: { value: 's' } });
    fireEvent.change(screen.getByPlaceholderText('Used once — never stored'), { target: { value: 'p' } });
    fireEvent.click(screen.getByRole('button', { name: /Connect Bullhorn/i }));
    await screen.findByText(/missing the 'PUT' entitlement on Note/i);
  });

  it('shows the connected state with a Sync now button and loads the stage map', async () => {
    orgsApi.getBullhornStatus.mockResolvedValue({
      data: { bullhorn_connected: true, unmapped_status_count: 1, unmapped_statuses: ['Submitted'], event_subscription_active: true },
    });
    orgsApi.getBullhornStageMap.mockResolvedValue({
      data: {
        pipeline_stages: ['applied', 'review', 'advanced'],
        mappings: [{ remote_status: 'Placed', taali_stage: 'advanced', is_reject: false }],
        unmapped_statuses: ['Submitted'],
      },
    });

    render(<BullhornConnection orgData={{ bullhorn_connected: true }} />);
    expect(screen.getByText('Bullhorn connected')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sync now/i })).toBeInTheDocument();

    // The stage-map editor renders the existing mapping row.
    await waitFor(() => expect(screen.getByDisplayValue('Placed')).toBeInTheDocument());
    // The unmapped status is offered as an add chip.
    expect(screen.getByRole('button', { name: /\+ Submitted/i })).toBeInTheDocument();
  });

  it('saves the stage map via replaceBullhornStageMap', async () => {
    orgsApi.getBullhornStatus.mockResolvedValue({ data: { bullhorn_connected: true } });
    orgsApi.getBullhornStageMap.mockResolvedValue({
      data: {
        pipeline_stages: ['applied', 'review', 'advanced'],
        mappings: [{ remote_status: 'Placed', taali_stage: 'advanced', is_reject: false }],
        unmapped_statuses: [],
      },
    });
    orgsApi.replaceBullhornStageMap.mockResolvedValue({ data: { unmapped_statuses: [] } });

    render(<BullhornConnection orgData={{ bullhorn_connected: true }} />);
    await waitFor(() => expect(screen.getByDisplayValue('Placed')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Save stage mapping/i }));
    await waitFor(() => expect(orgsApi.replaceBullhornStageMap).toHaveBeenCalledTimes(1));
    expect(orgsApi.replaceBullhornStageMap).toHaveBeenCalledWith([
      { remote_status: 'Placed', taali_stage: 'advanced', is_reject: false },
    ]);
  });

  it('starts a sync and registers it with the global job-status context', async () => {
    const trackBullhornSync = vi.fn();
    useJobStatusMock.mockReturnValue({ trackBullhornSync });
    orgsApi.getBullhornStatus.mockResolvedValue({ data: { bullhorn_connected: true } });
    orgsApi.syncBullhorn.mockResolvedValue({ data: { status: 'started' } });

    render(<BullhornConnection orgData={{ bullhorn_connected: true }} />);
    const syncBtn = await screen.findByRole('button', { name: /Sync now/i });
    fireEvent.click(syncBtn);

    await waitFor(() => expect(orgsApi.syncBullhorn).toHaveBeenCalledTimes(1));
    // The sync is handed to the shared context so it surfaces in the global
    // BackgroundJobsPanel, not just this tab's local strip.
    expect(trackBullhornSync).toHaveBeenCalledTimes(1);
  });

  it('keeps status and mappings readable for members without exposing mutations', async () => {
    orgsApi.getBullhornStatus.mockResolvedValue({
      data: { bullhorn_connected: true, unmapped_status_count: 1, unmapped_statuses: ['Submitted'] },
    });
    orgsApi.getBullhornStageMap.mockResolvedValue({
      data: {
        pipeline_stages: ['applied', 'review', 'advanced'],
        mappings: [{ remote_status: 'Placed', taali_stage: 'advanced', is_reject: false }],
        unmapped_statuses: ['Submitted'],
      },
    });

    render(<BullhornConnection orgData={{ bullhorn_connected: true }} canManage={false} />);

    expect(await screen.findByDisplayValue('Placed')).toBeDisabled();
    expect(screen.getByText(/Only a workspace owner can connect Bullhorn/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /Sync now|Stop sync|Save stage mapping|Remove|\+ Submitted/i })).not.toBeInTheDocument();
    expect(orgsApi.getBullhornStatus).toHaveBeenCalledTimes(1);
    expect(orgsApi.getBullhornStageMap).toHaveBeenCalledTimes(1);
  });

  it('does not show the credential form to a member when Bullhorn is disconnected', () => {
    render(<BullhornConnection orgData={{ bullhorn_connected: false }} canManage={false} />);

    expect(screen.getByText('Bullhorn not connected')).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('taali.api')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Connect Bullhorn/i })).not.toBeInTheDocument();
  });
});
