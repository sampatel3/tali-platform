import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { SearchCoverage } from './Thread';


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
});
