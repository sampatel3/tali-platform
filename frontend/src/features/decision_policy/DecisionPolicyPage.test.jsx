import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('./api', () => ({
  decisionPolicyApi: {
    active: vi.fn().mockResolvedValue({
      organization_id: 1,
      policy_id: 7,
      revision_id: 7,
      activated_at: '2026-05-08T10:00:00Z',
      policy_json: {
        schema_version: 'v1',
        decision_points: {
          send_assessment: {
            thresholds: { role_fit_min: 65 },
            weights: { role_fit_score: 1.0 },
            rules: [
              { if: 'role_fit_score >= role_fit_min', then: 'queue_send_assessment', priority: 50 },
            ],
            confidence_floor: 0.5,
          },
        },
      },
      timeline: [
        {
          id: 7,
          cause: 'human_edit',
          created_at: '2026-05-08T10:00:00Z',
          feedback_ids: [],
          notes: 'bootstrap',
          parent_revision_id: null,
        },
      ],
    }),
    pending: vi.fn().mockResolvedValue([]),
    signals: vi.fn().mockResolvedValue({
      organization_id: 1,
      window_days: 30,
      buckets: [],
      top_failure_modes: [],
      manual_action_volume: 0,
      agent_decision_volume: 0,
    }),
    activate: vi.fn(),
    discard: vi.fn(),
  },
}));

import DecisionPolicyPage from './DecisionPolicyPage';
import { decisionPolicyApi } from './api';

describe('DecisionPolicyPage', () => {
  it('renders the active policy view by default', async () => {
    render(<DecisionPolicyPage />);
    await waitFor(() => {
      expect(screen.getByText('Decision Policy')).toBeInTheDocument();
    });
    expect(decisionPolicyApi.active).toHaveBeenCalled();
    expect(screen.getByText('Send assessment')).toBeInTheDocument();
    expect(screen.getByText('Role fit min')).toBeInTheDocument();
  });
});
