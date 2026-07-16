import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import TestMemoryRouter from '../../test/TestMemoryRouter';
import { BlogIndexPage } from './BlogIndexPage';

describe('BlogIndexPage landmarks', () => {
  it('puts the page content in the sole main landmark, outside the footer', () => {
    const { container } = render(
      <TestMemoryRouter initialEntries={['/blog']}>
        <BlogIndexPage onNavigate={vi.fn()} />
      </TestMemoryRouter>,
    );

    const main = screen.getByRole('main');
    expect(within(main).getByRole('heading', { name: 'Writing on AI-native work' })).toBeInTheDocument();
    expect(within(main).getByRole('heading', { name: 'Guides' })).toBeInTheDocument();
    expect(container.querySelectorAll('main')).toHaveLength(1);
    expect(container.querySelector('footer main')).toBeNull();
  });
});
