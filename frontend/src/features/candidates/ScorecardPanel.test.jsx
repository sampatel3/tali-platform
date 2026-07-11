import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ScorecardPanel } from './ScorecardPanel';

const emptySummary = {
  submitted_count: 0,
  recommendations: {},
  mean_lean: null,
  mean_overall_rating: null,
};

function makeRolesApi() {
  return {
    listScorecards: vi.fn(),
    getScorecardSummary: vi.fn(),
    upsertScorecard: vi.fn(),
    submitScorecard: vi.fn(),
  };
}

describe('ScorecardPanel', () => {
  let rolesApi;
  beforeEach(() => {
    rolesApi = makeRolesApi();
  });

  // The recommendation control is a custom Select (button trigger + a listbox
  // of option buttons). The first listbox trigger on the panel is the
  // recommendation select. Open it and click the option.
  const pickRecommendation = (label) => {
    const triggers = document.querySelectorAll('button[aria-haspopup="listbox"]');
    fireEvent.click(triggers[0]);
    const listbox = screen.getByRole('listbox');
    const option = within(listbox)
      .getAllByRole('option')
      .find((el) => el.textContent.trim() === label);
    fireEvent.click(option);
  };

  it('submits a scorecard (upsert then submit) using the caller-keyed API', async () => {
    rolesApi.listScorecards.mockResolvedValue({ data: [] });
    rolesApi.getScorecardSummary.mockResolvedValue({ data: emptySummary });
    rolesApi.upsertScorecard.mockResolvedValue({ data: { id: 9 } });
    rolesApi.submitScorecard.mockResolvedValue({ data: { id: 9, submitted_at: 'now' } });

    render(<ScorecardPanel applicationId={5} rolesApi={rolesApi} />);
    expect(await screen.findByText('Your scorecard')).toBeInTheDocument();

    pickRecommendation('Yes');
    fireEvent.click(screen.getByText('Submit'));

    await waitFor(() =>
      expect(rolesApi.upsertScorecard).toHaveBeenCalledWith(
        5,
        expect.objectContaining({ overall_recommendation: 'yes' }),
      ),
    );
    expect(rolesApi.submitScorecard).toHaveBeenCalledWith(5, 9);
  });

  it('keeps Submit disabled for a no_decision abstention', async () => {
    rolesApi.listScorecards.mockResolvedValue({ data: [] });
    rolesApi.getScorecardSummary.mockResolvedValue({ data: emptySummary });

    render(<ScorecardPanel applicationId={5} rolesApi={rolesApi} />);
    expect(await screen.findByText('Your scorecard')).toBeInTheDocument();

    pickRecommendation('No decision');
    expect(screen.getByText('Submit')).toBeDisabled();
  });

  it('renders the panel summary and submitted cards', async () => {
    rolesApi.listScorecards.mockResolvedValue({
      data: [
        {
          id: 1,
          overall_recommendation: 'strong_yes',
          overall_rating: 4,
          submitted_at: 'x',
          notes: 'great',
        },
      ],
    });
    rolesApi.getScorecardSummary.mockResolvedValue({
      data: { submitted_count: 1, recommendations: { strong_yes: 1 }, mean_overall_rating: 4 },
    });

    render(<ScorecardPanel applicationId={5} rolesApi={rolesApi} />);
    expect(await screen.findByText(/Panel summary/)).toBeInTheDocument();
    expect(screen.getByText('great')).toBeInTheDocument();
  });

  it('hides the editor and shows read-only cards on a share view', async () => {
    rolesApi.listScorecards.mockResolvedValue({
      data: [{ id: 1, overall_recommendation: 'yes', submitted_at: 'x', notes: 'ok' }],
    });
    rolesApi.getScorecardSummary.mockResolvedValue({
      data: { submitted_count: 1, recommendations: { yes: 1 }, mean_overall_rating: null },
    });

    render(<ScorecardPanel applicationId={5} rolesApi={rolesApi} readOnly />);
    expect(await screen.findByText(/Panel summary/)).toBeInTheDocument();
    expect(screen.queryByText('Your scorecard')).not.toBeInTheDocument();
  });
});
