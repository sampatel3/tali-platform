import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AssessmentQueueCard, RecruitingOverviewCard } from './OperationsCards';


describe('operational chat result cards', () => {
  it('renders the overview and its attention count', () => {
    render(
      <RecruitingOverviewCard
        data={{
          scope: { role_name: null },
          roles: { total: 3 },
          candidates: { total: 120 },
          applications: { total: 84, pipeline_stages: { review: 7 } },
          assessments: { total: 24, needs_attention: 4 },
          frontend_url: 'https://tali.test/home',
        }}
      />,
    );
    expect(screen.getByText('Recruiting operations')).toBeInTheDocument();
    expect(screen.getByText('roles')).toBeInTheDocument();
    expect(screen.getByText('120')).toBeInTheDocument();
    expect(screen.getByText('need attention')).toBeInTheDocument();
    expect(screen.getByText('review · 7')).toBeInTheDocument();
  });

  it('renders actionable assessment rows without developer payloads', () => {
    render(
      <AssessmentQueueCard
        data={{
          total: 1,
          frontend_url: 'https://tali.test/assessments',
          items: [
            {
              assessment_id: 7,
              candidate_name: 'Ada Lovelace',
              role_name: 'Platform Engineer',
              task_name: 'API exercise',
              status: 'completed',
              attention_required: true,
              attention_reasons: ['scoring_failed'],
              frontend_url: 'https://tali.test/candidates/3?tab=assessment',
            },
          ],
        }}
      />,
    );
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByText('scoring failed')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Ada Lovelace/ })).toHaveAttribute(
      'href',
      'https://tali.test/candidates/3?tab=assessment',
    );
  });
});
