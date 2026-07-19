import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { CandidateSummaryFallback } from './CandidateSummaryFallback';

describe('CandidateSummaryFallback', () => {
  it('avoids duplicating a recruiter decision summary', () => {
    const { container } = render(
      <CandidateSummaryFallback
        agentDecision={{ candidate_summary: 'Decision-time synthesis.' }}
        isClientView={false}
        recruiterSummaryText="Holistic fallback."
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it('keeps the fallback for client views where decision detail is private', () => {
    render(
      <CandidateSummaryFallback
        agentDecision={{ candidate_summary: 'Private decision synthesis.' }}
        isClientView
        recruiterSummaryText="  Client-safe holistic   summary. "
      />,
    );

    expect(screen.getByRole('region', { name: 'Candidate summary' }))
      .toHaveTextContent('Client-safe holistic summary.');
  });

  it('renders nothing when there is no meaningful fallback', () => {
    const { container } = render(
      <CandidateSummaryFallback
        agentDecision={null}
        isClientView={false}
        recruiterSummaryText="   "
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
