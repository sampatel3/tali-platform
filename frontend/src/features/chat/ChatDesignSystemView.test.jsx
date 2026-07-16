import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatDesignSystemView } from './ChatDesignSystemView';

const renderDirectRoute = () => render(
  <MemoryRouter initialEntries={['/showcase/chat-system']}>
    <Routes>
      <Route path="/showcase/chat-system" element={<ChatDesignSystemView />} />
      <Route path="*" element={<div>Route not found</div>} />
    </Routes>
  </MemoryRouter>,
);

describe('ChatDesignSystemView', () => {
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
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  it('direct-loads the living chat design-system reference with every core specimen group', () => {
    const { container } = renderDirectRoute();

    const main = screen.getByRole('main');
    expect(within(main).getByRole('heading', {
      level: 1,
      name: /chat design system/i,
    })).toBeInTheDocument();

    expect(within(main).getByRole('region', { name: 'Message anatomy' })).toBeInTheDocument();
    expect(within(main).getByRole('region', { name: 'Artifact anatomy' })).toBeInTheDocument();
    expect(within(main).getByRole('region', { name: 'Conversation and Agent Feed' })).toBeInTheDocument();
    expect(within(main).getByRole('region', { name: 'Composer' })).toBeInTheDocument();

    const densityControls = within(main).getByRole('group', { name: 'Preview density' });
    expect(within(densityControls).getByRole('button', { name: 'Comfortable' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(within(densityControls).getByRole('button', { name: 'Compact' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    expect(container.querySelector('[data-chat-density="comfortable"]')).toBeInTheDocument();
    expect(container.querySelector('[data-chat-density="compact"]')).toBeInTheDocument();
  });

  it('shows the grounded report, a compact autonomous feed, and a real composer', () => {
    renderDirectRoute();

    const artifacts = screen.getByRole('region', { name: 'Artifact anatomy' });
    expect(within(artifacts).getByText('Grounded report')).toBeInTheDocument();

    const lanes = screen.getByRole('region', { name: 'Conversation and Agent Feed' });
    expect(within(lanes).getByRole('tab', { name: /Agent feed/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    const feed = within(lanes).getByRole('tabpanel', { name: 'Agent feed' });
    const errorRow = within(feed).getByText('Agent run stopped before completion').closest('button');
    expect(errorRow).toHaveAttribute('aria-expanded', 'false');
    expect(within(feed).queryByText(/Six decisions were retained/)).not.toBeInTheDocument();
    expect(within(feed).getByText('Choose who to invite')).toBeInTheDocument();
    expect(within(feed).getByText('1 candidate decision ready')).toBeInTheDocument();
    expect(within(feed).queryByText('Maya Chen · Advance recommended')).not.toBeInTheDocument();

    const composer = screen.getByRole('region', { name: 'Composer' });
    expect(within(composer).getByRole('textbox', {
      name: /chat message|answer the agent/i,
    })).toBeInTheDocument();
    expect(within(composer).getByRole('button', { name: /send/i })).toBeInTheDocument();
  });

  it('keeps candidate decisions compact and links them back to the review queue', () => {
    const { container } = renderDirectRoute();
    const lanes = screen.getByRole('region', { name: 'Conversation and Agent Feed' });
    const feed = within(lanes).getByRole('tabpanel', { name: 'Agent feed' });

    fireEvent.click(within(feed).getByRole('button', { name: 'Decisions' }));
    const decisionRow = within(feed).getByText('Maya Chen · Advance recommended').closest('button');
    expect(decisionRow).toHaveAttribute('aria-expanded', 'false');
    expect(within(feed).queryByText(/clears all six must-haves/i)).not.toBeInTheDocument();
    expect(container.querySelector('.rq-hybrid-detail')).not.toBeInTheDocument();

    fireEvent.click(decisionRow);
    expect(within(feed).getByText(/clears all six must-haves/i)).toBeInTheDocument();
    expect(within(feed).getByRole('link', { name: 'Review in queue' })).toHaveAttribute(
      'href',
      '/home?role=109&pending=7421',
    );
    expect(container.querySelector('.rq-hybrid-detail')).not.toBeInTheDocument();
  });

  it('returns feed prompts to Chat without losing the composer or awareness state', async () => {
    const { container } = renderDirectRoute();

    fireEvent.click(screen.getByRole('button', { name: 'Compact' }));
    expect(screen.getByRole('button', { name: 'Compact' })).toHaveAttribute('aria-pressed', 'true');
    expect(container.querySelector('.cds-transcript')).toHaveAttribute('data-chat-density', 'compact');

    const lanes = screen.getByRole('region', { name: 'Conversation and Agent Feed' });
    let feed = within(lanes).getByRole('tabpanel', { name: 'Agent feed' });
    fireEvent.click(within(feed).getByText('The shortlist is ready to move').closest('button'));
    fireEvent.click(within(feed).getByRole('button', { name: 'Compare the two' }));
    expect(within(lanes).getByRole('tab', { name: 'Chat' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('textbox', { name: 'Chat message' })).toHaveValue(
      'Compare Priya and Daniel side by side, including evidence and risks.',
    );

    fireEvent.click(within(lanes).getByRole('tab', { name: /Agent feed/ }));
    feed = within(lanes).getByRole('tabpanel', { name: 'Agent feed' });
    fireEvent.click(within(feed).getByText('Choose who to invite').closest('button'));
    fireEvent.click(within(feed).getByRole('button', { name: 'Invite both' }));
    expect(await screen.findByText('Direction received.')).toBeInTheDocument();
    expect(screen.getByText('The blocker is cleared.')).toBeInTheDocument();

    fireEvent.click(within(lanes).getByRole('tab', { name: 'Chat' }));
    fireEvent.click(within(lanes).getByRole('button', { name: '1 new agent reply' }));
    expect(screen.getByRole('button', { name: 'Show notice again' })).toBeInTheDocument();
  });
});
