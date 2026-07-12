import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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

// The "Find candidates" tab lists roles and reuses SourceCandidatesPanel,
// which pulls roles + a toast helper. Mock both so the tab can render.
vi.mock('../../shared/api', () => ({
  roles: {
    list: vi.fn(),
    sourcingSearches: vi.fn(),
    outreachDraft: vi.fn(),
  },
}));

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}));

import { prospects as prospectsApi } from '../../shared/api/prospectsClient';
import { roles as rolesApi } from '../../shared/api';
import SourcingPage from './SourcingPage';

const ROWS = [
  {
    id: 1,
    full_name: 'Alice One',
    email: 'alice@example.com',
    position: 'Engineer',
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

describe('SourcingPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    prospectsApi.list.mockResolvedValue({ data: { prospects: ROWS, total: 2 } });
    rolesApi.list.mockResolvedValue({ data: [{ id: 7, name: 'Senior Data Engineer' }] });
  });

  it('shows a plain-English explainer of what Sourcing is for', async () => {
    render(<SourcingPage />);
    await screen.findByText('Alice One');
    expect(screen.getByText(/candidates you go out and find/i)).toBeInTheDocument();
  });

  it('orients the user with an empty state that links to Find candidates', async () => {
    prospectsApi.list.mockResolvedValue({ data: { prospects: [], total: 0 } });
    render(<SourcingPage />);
    await waitFor(() => expect(prospectsApi.list).toHaveBeenCalled());
    expect(await screen.findByText(/No prospects yet\./i)).toBeInTheDocument();
    // The empty state offers a button that jumps to the Find candidates tab.
    fireEvent.click(screen.getByRole('button', { name: /find candidates/i }));
    expect(await screen.findByText(/ready-to-paste search strings/i)).toBeInTheDocument();
  });

  it('lists roles on the Find candidates tab and mounts the search-string panel', async () => {
    render(<SourcingPage />);
    await screen.findByText('Alice One');

    fireEvent.click(screen.getByRole('tab', { name: /find candidates/i }));
    await waitFor(() => expect(rolesApi.list).toHaveBeenCalled());

    const picker = await screen.findByLabelText('Pick a role');
    fireEvent.change(picker, { target: { value: '7' } });

    // The reused SourceCandidatesPanel renders its search-string generator.
    expect(
      await screen.findByRole('button', { name: /generate search strings/i }),
    ).toBeInTheDocument();
  });

  it('renders prospect rows with a suppressed badge', async () => {
    render(<SourcingPage />);
    await waitFor(() => expect(prospectsApi.list).toHaveBeenCalled());
    expect(await screen.findByText('Alice One')).toBeInTheDocument();
    expect(screen.getByText('Bob Two')).toBeInTheDocument();
    // Suppressed badge shows the reason for the suppressed row.
    expect(screen.getByText('unsubscribed')).toBeInTheDocument();
  });

  it('submits the add-prospect form', async () => {
    prospectsApi.create.mockResolvedValue({ data: { id: 3 } });
    render(<SourcingPage />);
    await screen.findByText('Alice One');

    fireEvent.click(screen.getByRole('button', { name: /add prospect/i }));
    fireEvent.change(screen.getByLabelText('Full name'), { target: { value: 'Carol Three' } });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'carol@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: /save prospect/i }));

    await waitFor(() =>
      expect(prospectsApi.create).toHaveBeenCalledWith(
        expect.objectContaining({ full_name: 'Carol Three', email: 'carol@example.com' }),
      ),
    );
  });

  it('renders the import result summary including invalid rows', async () => {
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
    await screen.findByText('Alice One');

    const file = new File(['full_name,email\nX,x@y.com\n'], 'leads.csv', { type: 'text/csv' });
    const input = screen.getByTestId('csv-input');
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(prospectsApi.importCsv).toHaveBeenCalledWith(file));
    const summary = await screen.findByTestId('import-summary');
    expect(summary).toHaveTextContent('Imported 3');
    expect(summary).toHaveTextContent('Row 4: missing or invalid email');
  });
});
