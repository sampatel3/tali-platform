import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { scorecards as scorecardsApi } from '../../shared/api';
import { ScorecardPanel } from './ScorecardPanel';

vi.mock('../../shared/api', () => ({
  scorecards: { list: vi.fn(), summary: vi.fn(), upsert: vi.fn(), submit: vi.fn(), remove: vi.fn() },
}));

const emptySummary = { submitted_count: 0, recommendations: {}, mean_lean: null, mean_overall_rating: null };

describe('ScorecardPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('submits a scorecard (upsert then submit)', async () => {
    scorecardsApi.list.mockResolvedValue([]);
    scorecardsApi.summary.mockResolvedValue(emptySummary);
    scorecardsApi.upsert.mockResolvedValue({ id: 9 });
    scorecardsApi.submit.mockResolvedValue({ id: 9, submitted_at: 'now' });

    render(<ScorecardPanel applicationId={5} />);
    expect(await screen.findByText('Your scorecard')).toBeInTheDocument();

    const selects = screen.getAllByRole('combobox');
    fireEvent.change(selects[0], { target: { value: 'yes' } }); // recommendation
    fireEvent.click(screen.getByText('Submit'));

    await waitFor(() => expect(scorecardsApi.upsert).toHaveBeenCalledWith(5, expect.objectContaining({ recommendation: 'yes' })));
    expect(scorecardsApi.submit).toHaveBeenCalledWith(9);
  });

  it('renders the panel summary when cards are submitted', async () => {
    scorecardsApi.list.mockResolvedValue([{ id: 1, recommendation: 'strong_yes', overall_rating: 4, submitted_at: 'x', notes: 'great' }]);
    scorecardsApi.summary.mockResolvedValue({ submitted_count: 1, recommendations: { strong_yes: 1 }, mean_overall_rating: 4 });

    render(<ScorecardPanel applicationId={5} />);
    expect(await screen.findByText(/Panel summary/)).toBeInTheDocument();
    expect(screen.getByText('great')).toBeInTheDocument();
  });
});
