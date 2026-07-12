import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { compliance } from '../../shared/api';
import { AtsAdminPage } from './AtsAdminPage';

vi.mock('../../shared/api', () => ({
  compliance: {
    listRequests: vi.fn(),
    createRequest: vi.fn(),
    fulfillRequest: vi.fn(),
    rejectRequest: vi.fn(),
    eeoReport: vi.fn(),
  },
}));

describe('AtsAdminPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    compliance.listRequests.mockResolvedValue([]);
    compliance.eeoReport.mockResolvedValue({ total: 0, declined_count: 0 });
  });

  const openCompliance = async () => {
    // Compliance is the only (and default) tab now that offer templates are gone.
    render(<AtsAdminPage />);
    await screen.findByText('Data-subject requests');
  };

  it('lists data-subject requests and fulfils an access request (export dialog)', async () => {
    compliance.listRequests.mockResolvedValue([
      { id: 11, subject_email: 'person@x.test', request_type: 'access', status: 'pending' },
    ]);
    compliance.fulfillRequest.mockResolvedValue({ export: { candidate: { email: 'person@x.test' }, applications: [] } });

    await openCompliance();
    expect(await screen.findByText('person@x.test')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Fulfil'));

    await waitFor(() => expect(compliance.fulfillRequest).toHaveBeenCalledWith(11));
    // The export payload renders in a dialog.
    expect(await screen.findByText('Data export')).toBeInTheDocument();
  });

  it('rejects a pending request', async () => {
    compliance.listRequests.mockResolvedValue([
      { id: 12, subject_email: 'p@x.test', request_type: 'erasure', status: 'pending' },
    ]);
    compliance.rejectRequest.mockResolvedValue({ id: 12, status: 'rejected' });

    await openCompliance();
    fireEvent.click(await screen.findByText('Reject'));
    await waitFor(() => expect(compliance.rejectRequest).toHaveBeenCalledWith(12, expect.any(String)));
  });

  it('shows visible EEO cells and rolls small cohorts into a labelless suppressed bucket', async () => {
    compliance.eeoReport.mockResolvedValue({
      total: 6,
      declined_count: 0,
      gender: { values: { female: 5 }, suppressed_count: 1 },
      race_ethnicity: { values: {}, suppressed_count: 0 },
      veteran_status: { values: {}, suppressed_count: 0 },
      disability_status: { values: {}, suppressed_count: 0 },
    });
    await openCompliance();
    expect(await screen.findByText('female: 5')).toBeInTheDocument();
    // The below-threshold response is anonymous — its label ("male") never renders.
    expect(screen.getByText('1 suppressed')).toBeInTheDocument();
    expect(screen.queryByText('male: 1')).not.toBeInTheDocument();
    expect(screen.queryByText(/^male:/)).not.toBeInTheDocument();
  });
});
