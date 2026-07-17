import { describe, expect, it } from 'vitest';

import {
  relatedRolePublishAuthorization,
  relatedRoleRescoreAuthorization,
} from './paidWorkAuthorization';

const publishPreview = {
  source_role_id: 31,
  source_role_name: 'Current source role',
  source_role_version: 9,
  candidates_total: 12,
  candidates_scoreable: 10,
  estimated_cost_usd: 0.83,
  minimum_initial_budget_cents: 83,
  proposed_monthly_budget_cents: 5000,
  ongoing_score_cost_usd: 0.083,
};

describe('related-role paid-work authority', () => {
  it('binds publish authority to the fresh preview instead of a stale brief', () => {
    const authorization = relatedRolePublishAuthorization({
      source_role: {
        role_id: 30,
        name: 'Stale source role',
        version: 8,
      },
    }, publishPreview, '50.00');

    expect(authorization.request).toMatchObject({
      expected_source_role_id: 31,
      expected_source_role_name: 'Current source role',
      expected_source_role_version: 9,
    });
  });

  it('binds rescore count and cost confirmation to the live cohort', () => {
    const authorization = relatedRoleRescoreAuthorization(
      { id: 47, version: 7 },
      {
        role_version: 7,
        total: 1,
        scoreable_total: 1,
        cohort_total: 3,
        cohort_scoreable: 2,
        estimated_rescore_cost_usd: 0.17,
      },
    );

    expect(authorization).toEqual({
      request: {
        expected_version: 7,
        approved_max_scoreable_count: 2,
      },
      candidatesTotal: 3,
      scoreableCount: 2,
      estimatedCostUsd: 0.17,
    });
  });
});
