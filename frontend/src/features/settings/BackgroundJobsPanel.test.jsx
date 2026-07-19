import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { WorkableOpCounters } from './BackgroundJobsPanel';

describe('CV-gap background-job progress', () => {
  it('renders exact processed, rejected, skipped, and failed progress', () => {
    render(
      <WorkableOpCounters
        data={{
          op_type: 'reject_cv_gap',
          total_count: 5,
          processed_count: 4,
          rejected_count: 2,
          skipped_count: 1,
          failure_count: 1,
          failures: [{ application_id: 91, reason: 'Bullhorn did not accept' }],
        }}
      />,
    );

    expect(screen.getByText('CV-gap rejection: 4 / 5 processed')).toBeInTheDocument();
    expect(screen.getByText('2 rejected · 1 skipped · 1 failed')).toBeInTheDocument();
    expect(
      screen.getByText('Application #91: Bullhorn did not accept'),
    ).toBeInTheDocument();
  });
});
