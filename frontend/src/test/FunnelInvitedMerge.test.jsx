import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';

import {
  PIPELINE_FUNNEL_STAGES,
  invitedStageValue,
} from '../shared/metrics';
import { FunnelBoard } from '../shared/ui/FunnelBoard';

describe('funnel: Invited subsumes Completed', () => {
  it('drops Completed as a top-level tile, leads with Sourced', () => {
    // `sourced` (Phase 3b) leads the funnel; `completed` is folded into Invited
    // and is not a top-level tile.
    const keys = PIPELINE_FUNNEL_STAGES.map((s) => s.key);
    expect(keys).toEqual(['sourced', 'applied', 'scored', 'invited', 'advanced', 'rejected']);
  });

  it('Invited value = invited + completed', () => {
    expect(invitedStageValue({ invited: 31, completed: 4 })).toBe(35);
    expect(invitedStageValue({ invited: 26, in_assessment: 5, completed: 4 })).toBe(30); // in_assessment already folded into `invited` by the backend
    expect(invitedStageValue({})).toBe(0);
  });

  it('renders the merged Invited count and a completed sub-chip, no Completed tile', () => {
    const { queryByText, getByText } = render(
      <FunnelBoard
        variant="flat"
        stageCounts={{ applied: 100, scored: 40, invited: 31, completed: 4, in_assessment: 5 }}
        decisionsByType={{}}
      />,
    );
    // Invited tile shows 35 (31 + 4), completed is a sub-count.
    expect(getByText('35')).toBeTruthy();
    expect(getByText('4 completed')).toBeTruthy();
    expect(getByText('5 started')).toBeTruthy();
    // No standalone Completed stage label.
    expect(queryByText('Completed')).toBeNull();
  });

  it('hides delivered/opened sub-chips when there is no webhook data', () => {
    const { queryByText } = render(
      <FunnelBoard
        variant="flat"
        stageCounts={{ invited: 10, in_assessment: 2, invited_delivered: 0, invited_opened: 0 }}
        decisionsByType={{}}
      />,
    );
    expect(queryByText(/delivered/)).toBeNull();
    expect(queryByText(/opened/)).toBeNull();
  });
});
