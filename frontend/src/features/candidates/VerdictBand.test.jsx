import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { VerdictBand } from './VerdictBand';

describe('VerdictBand', () => {
  it('leads with the recommendation, demotes the other scores to tiles, and reuses IntegrityFlags', () => {
    render(
      <VerdictBand
        taaliScore={50}
        roleFitScore={72}
        assessmentScore={27}
        reqMet={8}
        reqTotal={13}
        recommendationLabel="Send assessment"
        confidence={1}
        integrity={{ trust_band: 'low', warnings: ['Verify the AWS certifications'] }}
      />
    );
    expect(screen.getByText('Send assessment')).toBeTruthy();
    expect(screen.getByText('Role fit')).toBeTruthy();
    expect(screen.getByText('72')).toBeTruthy();
    expect(screen.getByText('27')).toBeTruthy();
    // the shared trust readout is rendered, not a bespoke chip
    expect(screen.getByText('Verify the AWS certifications')).toBeTruthy();
  });

  it('renders without integrity (client / demo view)', () => {
    render(<VerdictBand taaliScore={0} recommendationLabel="Continue review" />);
    expect(screen.getByText('Continue review')).toBeTruthy();
  });
});
