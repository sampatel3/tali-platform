import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ImpactCard } from './cards';

describe('related-role chat cards', () => {
  it.each([
    ['workable', 'Workable'],
    ['bullhorn', 'Bullhorn'],
  ])('names %s as the owning candidate-pool provider', (provider, label) => {
    render(
      <ImpactCard
        card={{
          type: 'related_role_preview',
          ats_provider: provider,
          proposed_name: 'Platform Engineer · Related',
          candidates_total: 4,
          candidates_with_cv: 3,
          candidates_missing_cv: 1,
        }}
      />,
    );

    expect(screen.getByText(new RegExp(`original ${label} role`, 'i'))).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`coupled to the original ${label} job`, 'i')))
      .toBeInTheDocument();
  });
});
