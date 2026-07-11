import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import OutreachThanksPage from './OutreachThanksPage';

describe('OutreachThanksPage', () => {
  it('renders the standalone thanks confirmation', () => {
    render(<OutreachThanksPage />);
    expect(screen.getByTestId('outreach-thanks')).toBeInTheDocument();
    expect(screen.getByText(/Thanks — we'll be in touch\./)).toBeInTheDocument();
  });
});
