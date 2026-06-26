import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { VerdictDetail } from './VerdictDetail';

describe('VerdictDetail', () => {
  it('renders the reasoning and the deterministic decision trace', () => {
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
    expect(screen.getByText(/role-fit 72 vs threshold 60/)).toBeTruthy();
  });

  it('renders nothing when there is no decision', () => {
    const { container } = render(<VerdictDetail />);
    expect(container.firstChild).toBeNull();
  });
});
