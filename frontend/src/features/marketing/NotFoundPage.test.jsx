import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import TestMemoryRouter from '../../test/TestMemoryRouter';
import { useAuth } from '../../context/AuthContext';
import { NotFoundPage } from './NotFoundPage';

vi.mock('../../context/AuthContext', () => ({ useAuth: vi.fn() }));

describe('NotFoundPage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('returns public visitors to public destinations', () => {
    useAuth.mockReturnValue({ isAuthenticated: false });
    render(<TestMemoryRouter><NotFoundPage /></TestMemoryRouter>);

    expect(screen.getByRole('link', { name: 'Taali home' })).toHaveAttribute('href', '/');
    expect(screen.getByRole('link', { name: 'Product walkthrough' })).toHaveAttribute('href', '/demo');
  });

  it('returns signed-in users to app destinations', () => {
    useAuth.mockReturnValue({ isAuthenticated: true });
    render(<TestMemoryRouter><NotFoundPage /></TestMemoryRouter>);

    expect(screen.getByRole('link', { name: 'Go to Home' })).toHaveAttribute('href', '/home');
    expect(screen.getByRole('link', { name: 'Go to Jobs' })).toHaveAttribute('href', '/jobs');
  });
});
