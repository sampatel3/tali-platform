import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AgentDecisionCard } from './AgentDecisionCard';

const rejectDecision = {
  id: 7,
  application_id: 90,
  candidate_name: 'Aisha Khan',
  confidence: 0.91,
  confidence_band: 'high',
  cost_usd_cents: 3,
  created_at: '2026-07-16T10:00:00Z',
  decision_type: 'reject',
  model_version: 'deterministic',
  prompt_version: 'policy-v1',
  reasoning: 'A required skill is missing.',
  role_family: {
    owner: { id: 31, name: 'Data Platform Lead' },
    related: [{ id: 47, name: 'AI Engineer' }],
  },
};

describe('legacy candidate decision card role-family warning', () => {
  it('names the complete linked family before a reject can be approved', () => {
    render(
      <AgentDecisionCard
        decision={rejectDecision}
        onApprove={vi.fn()}
        onOverride={vi.fn()}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Data Platform Lead #31 (original) and AI Engineer #47 (related)',
    );
    expect(screen.getByRole('button', { name: /Approve agent recommendation/i }))
      .toHaveAttribute(
        'title',
        expect.stringMatching(/Data Platform Lead #31.*AI Engineer #47/i),
      );
  });

  it('does not show a reject warning for a non-reject recommendation', () => {
    render(
      <AgentDecisionCard
        decision={{ ...rejectDecision, decision_type: 'send_assessment' }}
        onApprove={vi.fn()}
        onOverride={vi.fn()}
      />,
    );

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
