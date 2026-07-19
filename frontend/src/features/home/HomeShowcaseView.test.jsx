import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { HomeShowcaseView, ShowcaseDock } from './HomeShowcaseView';

describe('ShowcaseDock agent lanes', () => {
  beforeEach(() => {
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
  });

  it('keeps background work compact and preserves a draft while changing lanes', () => {
    const { container } = render(<ShowcaseDock onAct={vi.fn()} />);

    expect(screen.getByRole('tab', { name: 'Chat' })).toHaveAttribute('aria-selected', 'true');
    const composer = screen.getByRole('textbox', { name: 'Chat message' });
    fireEvent.change(composer, { target: { value: 'Keep this draft while I check the feed' } });

    fireEvent.click(screen.getByRole('tab', { name: /Agent feed/ }));
    const feed = screen.getByRole('tabpanel', { name: 'Agent feed' });
    expect(within(feed).getByText('Run stopped before completion').closest('button')).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    expect(within(feed).queryByText(/Six decisions were retained/)).not.toBeInTheDocument();

    expect(within(feed).getByText('1 candidate decision ready')).toBeInTheDocument();
    fireEvent.click(within(feed).getByRole('button', { name: 'Decisions' }));
    const decisionRow = within(feed).getByText('Maya Chen · Advance recommended').closest('button');
    expect(container.querySelector('.rq-hybrid-detail')).not.toBeInTheDocument();
    fireEvent.click(decisionRow);
    expect(within(feed).getByRole('link', { name: 'Review in queue' })).toHaveAttribute(
      'href',
      '/home?role=109&pending=28',
    );

    fireEvent.click(screen.getByRole('tab', { name: 'Chat' }));
    expect(screen.getByRole('textbox', { name: 'Chat message' })).toHaveValue(
      'Keep this draft while I check the feed',
    );
  });
});

describe('HomeShowcaseView document structure', () => {
  it('exposes the standalone showcase as the page main landmark', () => {
    render(<HomeShowcaseView />);

    expect(screen.getByRole('main')).toHaveClass('home-app');
    expect(screen.getByRole('heading', { level: 1, name: 'Good morning.' })).toBeInTheDocument();
  });
});
