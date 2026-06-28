import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { VerdictDetail } from './VerdictDetail';

describe('VerdictDetail', () => {
  it('renders the plain-English reasoning under a "why this verdict" heading', () => {
    render(
      <VerdictDetail
        decision={{
          reasoning: 'Strong AWS, gaps in AI/ML.',
          evidence: {
            policy_basis: 'role-fit 72 vs threshold 60 → send_assessment',
            rule_path: ['point:send_assessment', 'rule:skipped:must_have_blocked', 'rule:fired:role_fit_score >= role_fit_min'],
          },
        }}
      />
    );
    expect(screen.getByText('Strong AWS, gaps in AI/ML.')).toBeTruthy();
    expect(screen.getByText('WHY THIS VERDICT')).toBeTruthy();
  });

  it('does not render the deterministic rule-path trace (removed as noise)', () => {
    render(
      <VerdictDetail
        decision={{
          reasoning: 'Strong AWS, gaps in AI/ML.',
          evidence: {
            policy_basis: 'role-fit 72 vs threshold 60 → send_assessment',
            rule_path: ['rule:fired:role_fit_score >= role_fit_min'],
          },
        }}
      />
    );
    expect(screen.queryByText(/role-fit 72 vs threshold 60/)).toBeNull();
    expect(screen.queryByText(/rule:fired/)).toBeNull();
  });

  it('renders nothing when there is no decision', () => {
    const { container } = render(<VerdictDetail />);
    expect(container.firstChild).toBeNull();
  });
});
