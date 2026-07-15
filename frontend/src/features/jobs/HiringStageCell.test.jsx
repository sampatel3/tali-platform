import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { HiringStageCell } from './HiringStageCell';

const renderCell = (application) => render(
  <table>
    <tbody>
      <tr>
        <HiringStageCell application={application} />
      </tr>
    </tbody>
  </table>,
);

describe('HiringStageCell', () => {
  it('renders the provider-neutral hiring stage while retaining raw ATS provenance', () => {
    renderCell({
      pipeline_stage: 'advanced',
      external_stage_raw: 'offer_extended',
      hiring_stage_context: {
        stage: 'offer',
        provider: 'workable',
      },
    });

    expect(screen.getByText('Offer')).toBeInTheDocument();
    expect(screen.getByText('Offer')).toHaveAttribute('title', 'Workable · Offer Extended');
    expect(screen.queryByText('Offer Extended')).not.toBeInTheDocument();
  });

  it('explains that autonomous native logistics still requires an integration', () => {
    renderCell({
      pipeline_stage: 'advanced',
      hiring_stage_context: {
        stage: null,
        provider: 'native',
        logistics_automation: {
          status: 'integration_required',
          required_integration: 'calendar',
        },
      },
    });

    expect(screen.getByText('—')).toHaveAttribute(
      'title',
      'Calendar integration required for autonomous logistics',
    );
    expect(screen.getByRole('link', { name: 'Calendar setup required' })).toHaveAttribute(
      'href',
      '/settings/integrations',
    );
  });

  it('renders a rejected outcome independently of the downstream stage', () => {
    renderCell({
      application_outcome: 'rejected',
      hiring_stage_context: { stage: 'interviewing', provider: 'bullhorn' },
      bullhorn_status: 'Client Interview',
    });

    expect(screen.getByText('Rejected')).toHaveClass('is-disqualified');
    expect(screen.queryByText('Interviewing')).not.toBeInTheDocument();
  });
});
