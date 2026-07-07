import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { careersApi } from './api';
import { CareersListPage } from './CareersListPage';
import { CareersJobPage } from './CareersJobPage';

vi.mock('./api', () => ({
  careersApi: {
    listJobs: vi.fn(),
    getJob: vi.fn(),
    apply: vi.fn(),
    submitEeo: vi.fn(),
  },
}));

const renderAt = (path, element, routePath) => render(
  <MemoryRouter initialEntries={[path]}>
    <Routes>
      <Route path={routePath} element={element} />
      <Route path="/careers/:orgSlug" element={<div>list</div>} />
    </Routes>
  </MemoryRouter>,
);

describe('CareersListPage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders published jobs', async () => {
    careersApi.listJobs.mockResolvedValue({
      organization: 'Acme',
      jobs: [{ slug: 'eng', title: 'Staff Engineer', department: 'Engineering', location_city: 'Dubai' }],
    });
    renderAt('/careers/acme', <CareersListPage />, '/careers/:orgSlug');
    expect(await screen.findByText('Staff Engineer')).toBeInTheDocument();
    expect(screen.getByText(/Open roles at Acme/)).toBeInTheDocument();
  });

  it('shows a not-found message for an unknown org', async () => {
    careersApi.listJobs.mockRejectedValue({ response: { status: 404 } });
    renderAt('/careers/nope', <CareersListPage />, '/careers/:orgSlug');
    expect(await screen.findByText(/Organisation not found/)).toBeInTheDocument();
  });
});

describe('CareersJobPage apply flow', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders detail + screening question and submits an application', async () => {
    careersApi.getJob.mockResolvedValue({
      title: 'Staff Engineer',
      description: 'Build things.',
      screening_questions: [
        { id: 1, prompt: 'Authorized to work locally?', kind: 'boolean', required: true },
      ],
      job_posting_jsonld: { '@type': 'JobPosting' },
    });
    careersApi.apply.mockResolvedValue({ application_id: 42, created: true, knockout_passed: true });

    renderAt('/careers/acme/eng', <CareersJobPage />, '/careers/:orgSlug/:roleSlug');

    expect(await screen.findByText('Staff Engineer')).toBeInTheDocument();
    expect(screen.getByText('Authorized to work locally? *')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('Your name'), { target: { value: 'Casey R' } });
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), { target: { value: 'casey@x.test' } });
    fireEvent.click(screen.getByText('Submit application'));

    await waitFor(() => expect(careersApi.apply).toHaveBeenCalledWith('acme', 'eng', expect.objectContaining({
      full_name: 'Casey R', email: 'casey@x.test',
    })));
    // After a successful apply the optional EEO step appears.
    expect(await screen.findByText(/Voluntary self-identification/)).toBeInTheDocument();
  });
});
