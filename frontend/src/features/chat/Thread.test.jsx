import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { friendlyError, Message, SearchCoverage, ToolResultRender } from './Thread';


describe('friendlyError', () => {
  it('explains exhausted workspace credits and disables futile retries', () => {
    expect(friendlyError(
      'Your workspace is out of AI credits. Add credits in Settings → Billing to continue.',
    )).toEqual({
      title: 'AI credits needed',
      detail: 'Add credits in Settings → Billing to continue using Chat.',
      retryable: false,
    });
  });
});

describe('stream progress', () => {
  it('explains the current stage before the first model or tool output arrives', () => {
    render(
      <Message
        msg={{
          id: 'assistant-progress',
          role: 'assistant',
          parts: [{
            type: 'progress',
            stage: 'planning',
            label: 'Understanding your request and choosing the right search…',
          }],
        }}
        isStreaming
      />,
    );

    expect(screen.getByText(/Understanding your request and choosing the right search/))
      .toBeInTheDocument();
    expect(screen.queryByText('thinking…')).not.toBeInTheDocument();
  });
});


describe('SearchCoverage', () => {
  it('labels an exhaustive zero-model database search', () => {
    render(
      <SearchCoverage
        data={{ database_matches: 1534, returned: 25, deep_checked: 0, capped: false }}
      />,
    );
    expect(screen.getByText('25 shown')).toBeInTheDocument();
    expect(screen.getByText('1534 retrieval matches')).toBeInTheDocument();
    expect(screen.getByText(/complete retrieval/)).toBeInTheDocument();
  });

  it('discloses bounded verification', () => {
    render(
      <SearchCoverage
        data={{ database_matches: 80, returned: 12, deep_checked: 50, capped: true }}
      />,
    );
    expect(screen.getByText(/50 deep-checked · partial verification/)).toBeInTheDocument();
  });

  it('separates graph-rescued retrieval matches from PostgreSQL matches', () => {
    render(
      <SearchCoverage
        data={{
          total_matched: 1,
          retrieval_matches: 1,
          database_matches: 0,
          returned: 1,
          deep_checked: 0,
          capped: false,
          exhaustive: false,
        }}
      />,
    );

    expect(screen.getByText('1 retrieval match · 0 PostgreSQL')).toBeInTheDocument();
    expect(screen.getByText(/partial retrieval/)).toBeInTheDocument();
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
  it('shows graph-search coverage and does not claim an exact zero when partial', () => {
    render(
      <ToolResultRender
        part={{
          toolName: 'graph_search_candidates',
          result: {
            applications: [],
            total_matched: 0,
            returned: 0,
            exhaustive: false,
            capped: false,
            is_exact_empty: false,
            warnings: [{ message: 'Graph coverage is partial.' }],
          },
        }}
      />,
    );

    expect(screen.getByLabelText('Partial candidate search coverage')).toBeInTheDocument();
    expect(screen.getByText('No candidates retrieved')).toBeInTheDocument();
    expect(screen.getByText(/not a confirmed zero/)).toBeInTheDocument();
    expect(screen.queryByText('No candidates matched')).not.toBeInTheDocument();
  });

  it('keeps the definitive empty message only for an exact zero', () => {
    render(
      <ToolResultRender
        part={{
          toolName: 'nl_search_candidates',
          result: {
            applications: [],
            total_matched: 0,
            returned: 0,
            exhaustive: true,
            capped: false,
            is_exact_empty: true,
          },
        }}
      />,
    );

    expect(screen.getByText('No candidates matched')).toBeInTheDocument();
    expect(screen.queryByText('No candidates retrieved')).not.toBeInTheDocument();
  });

  it('distinguishes a complete verified zero from partial retrieval', () => {
    render(
      <ToolResultRender
        part={{
          toolName: 'nl_search_candidates',
          result: {
            applications: [],
            total_matched: 2,
            retrieval_matches: 2,
            returned: 0,
            deep_checked: 2,
            evidence_succeeded: 2,
            evidence_failed: 0,
            qualified: 0,
            exhaustive: true,
            capped: false,
            is_exact_empty: false,
          },
        }}
      />,
    );

    expect(screen.getByText('No candidates met the verified requirements'))
      .toBeInTheDocument();
    expect(screen.getByText(/Every retrieved candidate was checked/)).toBeInTheDocument();
    expect(screen.queryByText('No candidates retrieved')).not.toBeInTheDocument();
  });

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
