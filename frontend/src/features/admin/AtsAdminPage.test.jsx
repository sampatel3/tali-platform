import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { offerTemplates } from '../../shared/api';
import { AtsAdminPage } from './AtsAdminPage';

vi.mock('../../shared/api', () => ({
  offerTemplates: { list: vi.fn(), create: vi.fn(), remove: vi.fn() },
}));

describe('AtsAdminPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    offerTemplates.list.mockResolvedValue([]);
  });

  it('renders the offer-templates tab and creates a template', async () => {
    offerTemplates.create.mockResolvedValue({ id: 1 });
    render(<AtsAdminPage />);
    expect(await screen.findByText('New template')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('e.g. Senior Eng — Band A'), {
      target: { value: 'Band A' },
    });
    fireEvent.click(screen.getByText('Add'));

    await waitFor(() =>
      expect(offerTemplates.create).toHaveBeenCalledWith(expect.objectContaining({ name: 'Band A' })),
    );
  });

  it('lists existing templates', async () => {
    offerTemplates.list.mockResolvedValue([
      { id: 7, name: 'Senior Eng', currency: 'AED', base_salary_amount: 180000, pay_frequency: 'year', is_active: true },
    ]);
    render(<AtsAdminPage />);
    expect(await screen.findByText('Senior Eng')).toBeInTheDocument();
  });
});
