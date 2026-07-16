import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { SearchCoverage, ToolResultRender } from './Thread';


describe('SearchCoverage', () => {
  it('labels an exhaustive zero-model database search', () => {
    render(
      <SearchCoverage
        data={{ database_matches: 1534, returned: 25, deep_checked: 0, capped: false }}
      />,
    );
    expect(screen.getByText('25 shown')).toBeInTheDocument();
    expect(screen.getByText('1534 database matches')).toBeInTheDocument();
    expect(screen.getByText(/full database search/)).toBeInTheDocument();
  });

  it('discloses bounded verification', () => {
    render(
      <SearchCoverage
        data={{ database_matches: 80, returned: 12, deep_checked: 50, capped: true }}
      />,
    );
    expect(screen.getByText(/50 deep-checked · partial verification/)).toBeInTheDocument();
  });

  it('separates completed evidence checks from verifier failures and shows warnings', () => {
    render(
      <SearchCoverage
        data={{
          database_matches: 3,
          returned: 2,
          deep_checked: 3,
          evidence_succeeded: 2,
          evidence_failed: 1,
          capped: true,
          warnings: [{
            code: 'rerank_partial',
            message: '1 of 3 evidence checks failed; the candidate remains unclassified.',
          }],
        }}
      />,
    );

    expect(screen.getByText('2 evidence checks completed · 1 failed')).toBeInTheDocument();
    expect(
      screen.getByText('1 of 3 evidence checks failed; the candidate remains unclassified.'),
    ).toBeInTheDocument();
  });
});

describe('grounded search results', () => {
  it('renders evidence and the shareable report from a top-candidate tool result', () => {
    render(
      <ToolResultRender
        part={{
          toolName: 'find_top_candidates',
          result: {
            shown: 1,
            rank_by: 'taali',
            evidence_model: 'grounder-v1',
            database_matches: 1,
            criteria_requested: ['Led a platform launch'],
            criteria_checked: ['Led a platform launch'],
            criteria_unchecked: [],
            deep_checked: 1,
            evidence_succeeded: 1,
            qualified: 1,
            capped: false,
            report_url: '/report/search-grounded',
            candidates: [{
              application_id: 42,
              rank: 1,
              candidate_name: 'Priya Raman',
              taali_score: 91,
              criteria: [{
                criterion: 'Led a platform launch',
                status: 'met',
                grounded: true,
                evidence: [{ quote: 'Led the platform launch across three regions.', source: 'cv' }],
              }],
            }],
          },
        }}
      />,
    );

    expect(screen.getByText(/Led the platform launch across three regions/)).toBeInTheDocument();
    expect(screen.getByText(/grounded vs CV \+ notes/)).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Open shareable grounded candidate report' }),
    ).toHaveAttribute('href', '/report/search-grounded');
  });
});
