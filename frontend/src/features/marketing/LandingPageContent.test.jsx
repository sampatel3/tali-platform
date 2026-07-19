import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import TestMemoryRouter from '../../test/TestMemoryRouter';
import { LandingPage } from './LandingPageContent';

describe('LandingPage landmarks', () => {
  it('keeps footer navigation headings at the page section level', () => {
    render(
      <TestMemoryRouter initialEntries={['/']}>
        <LandingPage onNavigate={vi.fn()} />
      </TestMemoryRouter>,
    );

    const footer = screen.getByRole('contentinfo');
    expect(within(footer).getByRole('heading', { level: 2, name: 'Product' })).toBeInTheDocument();
    expect(within(footer).getByRole('heading', { level: 2, name: 'Company' })).toBeInTheDocument();
  });
});
