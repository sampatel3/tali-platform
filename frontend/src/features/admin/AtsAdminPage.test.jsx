import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { webhooks, offerTemplates, compliance } from '../../shared/api';
import { AtsAdminPage } from './AtsAdminPage';

vi.mock('../../shared/api', () => ({
  webhooks: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(), deliveries: vi.fn() },
  offerTemplates: { list: vi.fn(), create: vi.fn(), remove: vi.fn() },
  compliance: { listRequests: vi.fn(), createRequest: vi.fn(), fulfillRequest: vi.fn(), rejectRequest: vi.fn(), eeoReport: vi.fn() },
}));

describe('AtsAdminPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    webhooks.list.mockResolvedValue([]);
    offerTemplates.list.mockResolvedValue([]);
    compliance.listRequests.mockResolvedValue([]);
    compliance.eeoReport.mockResolvedValue({ total: 0, declined_count: 0 });
  });

  it('renders the webhooks tab and creates a subscription', async () => {
    webhooks.create.mockResolvedValue({ id: 1 });
    render(<AtsAdminPage />);
    expect(await screen.findByText('Add endpoint')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('https://…/hook'), { target: { value: 'https://e.test/h' } });
    fireEvent.change(screen.getByPlaceholderText('shared secret'), { target: { value: 'shh' } });
    fireEvent.click(screen.getByText('Add'));

    await waitFor(() => expect(webhooks.create).toHaveBeenCalledWith(expect.objectContaining({ url: 'https://e.test/h', secret: 'shh' })));
  });

  it('switches to the compliance tab and logs a request', async () => {
    compliance.createRequest.mockResolvedValue({ id: 2 });
    render(<AtsAdminPage />);
    fireEvent.click(await screen.findByText('Compliance'));

    expect(await screen.findByText('Data-subject requests')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('person@example.com'), { target: { value: 'x@y.test' } });
    fireEvent.click(screen.getByText('Log request'));
    await waitFor(() => expect(compliance.createRequest).toHaveBeenCalledWith(expect.objectContaining({ subject_email: 'x@y.test', request_type: 'access' })));
  });
});
