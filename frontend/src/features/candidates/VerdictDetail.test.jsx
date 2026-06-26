import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { VerdictDetail } from './VerdictDetail';

describe('VerdictDetail', () => {
  it('renders the reasoning, the decision trace and only the unverified claims', () => {
    render(
      <VerdictDetail
        decision={{
          reasoning: 'Strong AWS, gaps in AI/ML.',
          evidence: {
            policy_basis: 'role-fit 72 vs threshold 60 → send_assessment',
            rule_path: ['point:send_assessment', 'rule:skipped:must_have_blocked', 'rule:fired:role_fit_score >= role_fit_min'],
          },
        }}
        integrity={{ trust_band: 'low', to_verify: 1 }}
        claimsToVerify={[
          { claim_text: 'CKA', corroboration: 'uncorroborated', reasoning: 'no credential id' },
          { claim_text: 'AWS SA', corroboration: 'corroborated' },
        ]}
      />
    );
    expect(screen.getByText('Strong AWS, gaps in AI/ML.')).toBeTruthy();
    expect(screen.getByText(/role-fit 72 vs threshold 60/)).toBeTruthy();
    expect(screen.getByText('CKA')).toBeTruthy();
    // the corroborated claim is filtered out
    expect(screen.queryByText('AWS SA')).toBeNull();
  });

  it('renders nothing when there is no decision or integrity', () => {
    const { container } = render(<VerdictDetail />);
    expect(container.firstChild).toBeNull();
  });
});
