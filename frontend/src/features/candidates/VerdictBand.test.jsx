import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { VerdictBand } from './VerdictBand';

describe('VerdictBand', () => {
  it('leads with the recommendation and lifts the trust signal onto the band', () => {
    render(
      <VerdictBand
        taaliScore={50}
        roleFitScore={72}
        assessmentScore={27}
        reqMet={8}
        reqTotal={13}
        recommendationLabel="Send assessment"
        confidence={1}
        integrity={{ trust_band: 'low', to_verify: 8 }}
      />
    );
    expect(screen.getByText('Send assessment')).toBeTruthy();
    expect(screen.getByText(/Verify before advancing · 8 to verify/)).toBeTruthy();
    // the other scores are demoted to tiles, not four competing rings
    expect(screen.getByText('Role fit')).toBeTruthy();
    expect(screen.getByText('72')).toBeTruthy();
    expect(screen.getByText('27')).toBeTruthy();
  });

  it('renders without a decision or integrity (client / demo view)', () => {
    render(<VerdictBand taaliScore={0} recommendationLabel="Continue review" />);
    expect(screen.getByText('Continue review')).toBeTruthy();
    expect(screen.queryByText(/to verify/)).toBeNull();
  });
});
