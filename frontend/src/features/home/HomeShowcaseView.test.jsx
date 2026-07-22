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

  it('mirrors the current Home Hub hierarchy and opens chat only for a selected role', () => {
    render(<HomeShowcaseView />);

    expect(screen.queryByText('DECISIONS TODAY')).not.toBeInTheDocument();
    expect(screen.getByText('Review queue', { selector: 'h3' })).toBeInTheDocument();
    expect(screen.getByText("Approve, override, or teach the agent's calls — this is where you keep the loop honest.")).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Chat' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByTitle('Senior Backend Engineer — Capped salary at AED 25k · re-screened 4'));

    expect(screen.getByRole('tab', { name: 'Chat' })).toBeInTheDocument();
    expect(screen.queryByText('Jordan Patel')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTitle('Data Engineer — Idle · waiting for new candidates'));

    expect(screen.queryByRole('tab', { name: 'Chat' })).not.toBeInTheDocument();
    expect(screen.getAllByText('Jordan Patel').length).toBeGreaterThan(0);
  });
});
