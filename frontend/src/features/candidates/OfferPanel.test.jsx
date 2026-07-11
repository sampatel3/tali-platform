import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { offers as offersApi } from '../../shared/api';
import { OfferPanel } from './OfferPanel';

vi.mock('../../shared/api', () => ({
  offers: {
    listForApplication: vi.fn(),
    create: vi.fn(),
    transition: vi.fn(),
    hrisExport: vi.fn(),
    esignRequest: vi.fn(),
  },
}));

describe('OfferPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists an offer and transitions it', async () => {
    offersApi.listForApplication
      .mockResolvedValueOnce([
        { id: 3, version: 1, status: 'draft', currency: 'AED', base_salary_amount: 200000, pay_frequency: 'year' },
      ])
      .mockResolvedValueOnce([
        { id: 3, version: 1, status: 'sent', currency: 'AED', base_salary_amount: 200000, pay_frequency: 'year' },
      ]);
    offersApi.transition.mockResolvedValue({});

    render(<OfferPanel applicationId={5} />);
    expect(await screen.findByText('draft')).toBeInTheDocument();

    fireEvent.click(screen.getByText('sent'));
    await waitFor(() => expect(offersApi.transition).toHaveBeenCalledWith(3, 'sent'));
    expect(await screen.findByText('sent')).toBeInTheDocument();
  });

  it('opens the HRIS payload dialog', async () => {
    offersApi.listForApplication.mockResolvedValue([
      { id: 3, version: 1, status: 'accepted', currency: 'AED', base_salary_amount: 200000 },
    ]);
    offersApi.hrisExport.mockResolvedValue({ offer: { id: 3, hris_ready: true } });

    render(<OfferPanel applicationId={5} />);
    expect(await screen.findByText('accepted')).toBeInTheDocument();
    fireEvent.click(screen.getByText('HRIS payload'));
    await waitFor(() => expect(offersApi.hrisExport).toHaveBeenCalledWith(3));
    expect(await screen.findByText(/hris_ready/)).toBeInTheDocument();
  });
});
