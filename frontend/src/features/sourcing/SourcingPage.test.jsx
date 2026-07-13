import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../shared/api/prospectsClient', () => ({
  prospects: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    archive: vi.fn(),
    importCsv: vi.fn(),
  },
}));

vi.mock('../../shared/api', () => ({
  roles: {
    list: vi.fn(),
    sourcingSearches: vi.fn(),
    outreachDraft: vi.fn(),
  },
}));

const showToast = vi.fn();
vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast }),
}));

vi.mock('./CampaignsPanel', () => ({
  default: ({ initialCampaignId, onCampaignChange }) => (
    <div>
      <span>Campaigns panel {initialCampaignId || 'list'}</span>
      <button type="button" onClick={() => onCampaignChange(42)}>Open campaign 42</button>
      <button type="button" onClick={() => onCampaignChange(null)}>Back to campaigns</button>
    </div>
  ),
}));

import { prospects as prospectsApi } from '../../shared/api/prospectsClient';
import { roles as rolesApi } from '../../shared/api';
import SourcingPage from './SourcingPage';

const ROWS = [
  {
    id: 1,
    full_name: 'Alice One',
    email: 'alice@example.com',
    phone: '+971 50 123 4567',
    position: 'Engineer',
    location: 'Dubai',
    linkedin_url: 'https://linkedin.com/in/alice',
    notes: 'Platform engineer with Spark experience.',
    source_name: 'csv:leads.csv',
    source_strategy: 'sourced',
    status: 'new',
    created_at: '2026-07-10T10:00:00Z',
    suppressed: null,
  },
  {
    id: 2,
    full_name: 'Bob Two',
    email: 'bob@example.com',
    position: 'Designer',
    source_name: 'manual',
    source_strategy: 'sourced',
    status: 'contacted',
    created_at: '2026-07-09T10:00:00Z',
    suppressed: 'unsubscribed',
  },
];

const openProspects = async () => {
  fireEvent.click(screen.getByRole('tab', { name: /prospects/i }));
  return screen.findByText('Alice One');
};

describe('SourcingPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, '', '/sourcing');
    prospectsApi.list.mockResolvedValue({
      data: { prospects: ROWS, total: 2, limit: 25, offset: 0 },
    });
    prospectsApi.create.mockResolvedValue({ data: { id: 3 } });
    prospectsApi.update.mockResolvedValue({ data: {} });
    prospectsApi.archive.mockResolvedValue({ data: {} });
    rolesApi.list.mockResolvedValue({ data: [{ id: 7, name: 'Senior Data Engineer' }] });
  });

  it('starts with the role-first workflow and a concise page header', async () => {
    render(<SourcingPage />);

    expect(screen.getByRole('heading', { name: /build a better shortlist/i })).toBeInTheDocument();
    expect(screen.getByText(/find people against a live role/i)).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /find candidates/i })).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByLabelText('Pick a role')).toBeInTheDocument();
    expect(prospectsApi.list).not.toHaveBeenCalled();
  });

  it('filters terminal roles and mounts the shared sourcing panel', async () => {
    rolesApi.list.mockResolvedValue({
      data: [
        { id: 7, name: 'Senior Data Engineer', job_status: 'open' },
        { id: 8, name: 'Filled Role', job_status: 'filled' },
        { id: 9, name: 'Cancelled Role', job_status: 'cancelled' },
        { id: 10, name: 'Archived Workable Role', workable_job_state: 'archived' },
      ],
    });
    render(<SourcingPage />);

    const picker = await screen.findByLabelText('Pick a role');
    expect(within(picker).getByRole('option', { name: 'Senior Data Engineer' })).toBeInTheDocument();
    expect(within(picker).queryByRole('option', { name: 'Filled Role' })).not.toBeInTheDocument();
    expect(within(picker).queryByRole('option', { name: 'Cancelled Role' })).not.toBeInTheDocument();

    fireEvent.change(picker, { target: { value: '7' } });
    expect(await screen.findByRole('button', { name: /generate search strings/i })).toBeInTheDocument();
  });

  it('carries a pasted profile into the prospect form and saves it', async () => {
    render(<SourcingPage />);
    const picker = await screen.findByLabelText('Pick a role');
    fireEvent.change(picker, { target: { value: '7' } });

    fireEvent.change(await screen.findByLabelText('Candidate profile or CV text'), {
      target: { value: 'Spark platform engineer with five years in Dubai.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /continue to save prospect/i }));

    expect(screen.getByRole('tab', { name: /prospects/i })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('Notes or profile context')).toHaveValue(
      'Spark platform engineer with five years in Dubai.',
    );
    fireEvent.change(screen.getByLabelText('Full name'), { target: { value: 'Carol Three' } });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'carol@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: /save prospect/i }));

    await waitFor(() => expect(prospectsApi.create).toHaveBeenCalledWith(
      expect.objectContaining({
        full_name: 'Carol Three',
        email: 'carol@example.com',
        notes: 'Spark platform engineer with five years in Dubai.',
        source_name: 'sourcing-assist:role-7',
      }),
    ));
    expect(showToast).toHaveBeenCalledWith('Prospect saved to your shortlist.', 'success');
  });

  it('loads active prospects with server pagination and humanizes provenance', async () => {
    render(<SourcingPage />);
    await openProspects();

    expect(prospectsApi.list).toHaveBeenCalledWith({ limit: 25, offset: 0, status: 'active' });
    expect(screen.getByText('CSV · leads.csv')).toBeInTheDocument();
    expect(screen.getByText('Added manually')).toBeInTheDocument();
    expect(screen.getByText('unsubscribed')).toBeInTheDocument();
  });

  it('edits all prospect details and status', async () => {
    render(<SourcingPage />);
    await openProspects();

    const aliceRow = screen.getByRole('row', { name: /Alice One/i });
    fireEvent.click(within(aliceRow).getByRole('button', { name: 'Edit' }));
    const editForm = screen.getByRole('form', { name: 'Edit prospect' });
    expect(within(editForm).getByLabelText('Phone')).toHaveValue('+971 50 123 4567');
    fireEvent.change(within(editForm).getByLabelText('Position'), { target: { value: 'Staff Engineer' } });
    fireEvent.change(within(editForm).getByLabelText('Prospect status'), { target: { value: 'interested' } });
    fireEvent.click(within(editForm).getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(prospectsApi.update).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ position: 'Staff Engineer', status: 'interested' }),
    ));
  });

  it('archives and restores prospects with visible feedback', async () => {
    render(<SourcingPage />);
    await openProspects();

    const aliceRow = screen.getByRole('row', { name: /Alice One/i });
    fireEvent.click(within(aliceRow).getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(prospectsApi.archive).toHaveBeenCalledWith(1));
    expect(showToast).toHaveBeenCalledWith('Prospect archived.', 'success');

    prospectsApi.list.mockResolvedValueOnce({
      data: { prospects: [{ ...ROWS[0], status: 'archived' }], total: 1 },
    });
    fireEvent.change(screen.getByLabelText('Status filter'), { target: { value: 'archived' } });
    const restore = await screen.findByRole('button', { name: 'Restore' });
    fireEvent.click(restore);
    await waitFor(() => expect(prospectsApi.update).toHaveBeenCalledWith(1, { status: 'new' }));
  });

  it('requests the next page instead of silently stopping at 25', async () => {
    prospectsApi.list.mockResolvedValue({
      data: { prospects: ROWS, total: 60, limit: 25, offset: 0 },
    });
    render(<SourcingPage />);
    await openProspects();

    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    await waitFor(() => expect(prospectsApi.list).toHaveBeenLastCalledWith({
      limit: 25,
      offset: 25,
      status: 'active',
    }));
    expect(await screen.findByText('Page 2 of 3')).toBeInTheDocument();
    await act(() => new Promise((resolve) => window.setTimeout(resolve, 300)));
    expect(screen.getByText('Page 2 of 3')).toBeInTheDocument();
  });

  it('renders a useful empty shortlist state', async () => {
    prospectsApi.list.mockResolvedValue({ data: { prospects: [], total: 0 } });
    render(<SourcingPage />);
    fireEvent.click(screen.getByRole('tab', { name: /prospects/i }));

    expect(await screen.findByText('Your shortlist is empty')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /find candidates/i })).toBeInTheDocument();
  });

  it('imports CSV rows and reports invalid rows', async () => {
    prospectsApi.importCsv.mockResolvedValue({
      data: {
        created: 3,
        linked_to_existing_candidate: 1,
        duplicates_in_file: 1,
        already_prospects: 0,
        invalid_rows: [{ row: 4, reason: 'missing or invalid email' }],
      },
    });
    render(<SourcingPage />);
    await openProspects();

    const file = new File(['full_name,email\nX,x@y.com\n'], 'leads.csv', { type: 'text/csv' });
    fireEvent.change(screen.getByTestId('csv-input'), { target: { files: [file] } });

    await waitFor(() => expect(prospectsApi.importCsv).toHaveBeenCalledWith(file));
    const summary = await screen.findByTestId('import-summary');
    expect(summary).toHaveTextContent('Imported 3');
    expect(summary).toHaveTextContent('Row 4: missing or invalid email');
  });

  it('keeps tabs and campaign detail in the URL', async () => {
    render(<SourcingPage />);
    const findTab = screen.getByRole('tab', { name: /find candidates/i });
    fireEvent.keyDown(findTab, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: /prospects/i })).toHaveAttribute('aria-selected', 'true');
    expect(window.location.search).toContain('tab=prospects');
    await screen.findByText('Alice One');

    fireEvent.click(screen.getByRole('tab', { name: /campaigns/i }));
    fireEvent.click(screen.getByRole('button', { name: /open campaign 42/i }));
    expect(window.location.search).toContain('campaign=42');
    fireEvent.click(screen.getByRole('button', { name: /back to campaigns/i }));
    expect(window.location.search).not.toContain('campaign=');
  });
});
