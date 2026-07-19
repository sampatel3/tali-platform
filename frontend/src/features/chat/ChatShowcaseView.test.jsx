import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import TestMemoryRouter from '../../test/TestMemoryRouter';
import { ChatShowcaseView } from './ChatShowcaseView';

vi.mock('./GraphView', () => ({
  default: () => <div data-testid="showcase-graph">Candidate relationship graph</div>,
}));

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="showcase-location">{location.search}</output>;
}

const renderAt = (path = '/showcase/chat') => render(
  <TestMemoryRouter initialEntries={[path]}>
    <ChatShowcaseView />
    <LocationProbe />
  </TestMemoryRouter>,
);

const stubMatchMedia = () => {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
};

const expectBefore = (first, second) => {
  expect(
    Boolean(first.compareDocumentPosition(second) & window.Node.DOCUMENT_POSITION_FOLLOWING),
  ).toBe(true);
};

describe('ChatShowcaseView', () => {
  beforeEach(() => {
    stubMatchMedia();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  it('keeps Agents as the default while separating conversation from autonomous feed activity', () => {
    const { container } = renderAt();

    expect(screen.getByRole('main', { name: 'Agent chat showcase' })).toHaveClass('cp-root');
    expect(screen.getByRole('button', { name: /Agents/i })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByLabelText('Agent conversation')).toBeInTheDocument();
    expect(screen.getByText('Evidence complete')).toBeInTheDocument();
    expect(screen.getByText('Grounded report')).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Open shareable grounded candidate report' }),
    ).toHaveAttribute('href', '#grounded-report');
    expect(screen.getByRole('tab', { name: /^Chat$/i })).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByRole('article', { name: 'Choose the next step' })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Chat message' })).toHaveAttribute(
      'placeholder',
      'Ask about this role’s pool, or tell the agent to change something…',
    );
    expect(container.querySelectorAll('[data-motion-chat-item]').length).toBeGreaterThanOrEqual(3);

    fireEvent.click(screen.getByRole('tab', { name: /Agent feed/i }));
    expect(screen.getByTestId('showcase-location')).toHaveTextContent('stream=feed');
    const feedPanel = screen.getByRole('tabpanel', { name: 'Agent feed' });
    expect(feedPanel).toBeVisible();
    const errorRow = screen.getByRole('button', { name: /Agent run stopped before completion.*Error/i });
    expect(errorRow).toBeVisible();
    expect(screen.getByRole('button', { name: /Choose the next step.*Needs you/i })).toBeVisible();
    expect(screen.getByRole('button', { name: /1 candidate decision ready.*Review queue/i })).toBeVisible();
    expect(within(feedPanel).queryByText('Maya Chen')).not.toBeInTheDocument();

    fireEvent.click(errorRow);
    expect(errorRow).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText(/Six decisions were retained/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Details' }));
    expect(screen.getByText('Run 7042')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Explain stop' }));
    expect(screen.getByRole('tab', { name: /^Chat$/i })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('textbox', { name: 'Chat message' })).toHaveValue(
      'Explain why agent run 7042 stopped and what is safe to retry.',
    );

    fireEvent.click(screen.getByRole('button', { name: /^Ask$/i }));
    expect(screen.getByTestId('showcase-location')).toHaveTextContent('mode=ask');
    expect(screen.getByLabelText('Search conversation')).toBeInTheDocument();
  });

  it('direct-loads the Agent Feed as a stable review URL', () => {
    renderAt('/showcase/chat?mode=agents&stream=feed');

    expect(screen.getByRole('tab', { name: /Agent feed/i })).toHaveAttribute('aria-selected', 'true');
    const feedPanel = screen.getByRole('tabpanel', { name: 'Agent feed' });
    expect(feedPanel).toBeVisible();
    expect(
      within(screen.getByRole('group', { name: 'Filter agent feed' })).getAllByRole('button'),
    ).toHaveLength(4);

    fireEvent.click(screen.getByRole('button', { name: 'Decisions' }));
    expect(screen.getByRole('button', { name: /Maya Chen · Advance recommended/i })).toBeInTheDocument();
    expect(feedPanel.querySelector('.rq-hybrid-detail')).not.toBeInTheDocument();
  });

  it('direct-loads Ask and preserves text, tool, partial evidence, conclusion ordering', async () => {
    renderAt('/showcase/chat?mode=ask');

    expect(screen.getByRole('button', { name: /^Ask$/i })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    const conversation = screen.getByLabelText('Search conversation');
    const lead = screen.getByText(/Treating both as hard filters/i);
    const tool = screen.getByRole('article', {
      name: 'Completed tool activity: Ranking top candidates',
    });
    const evidence = screen.getByText('Partial evidence').closest('.ev-card');
    const conclusion = screen.getByText(/partially grounded report keeps that gap visible/i);

    expect(conversation).toContainElement(lead);
    expect(conversation).toContainElement(tool);
    expect(conversation).toContainElement(evidence);
    expect(conversation).toContainElement(conclusion);
    expectBefore(lead, tool);
    expectBefore(tool, evidence);
    expectBefore(evidence, conclusion);

    expect(screen.getByText('Unverified')).toBeInTheDocument();
    expect(
      screen.getByText(/1 of 14 evidence checks did not complete/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Open shareable partially grounded candidate report' }),
    ).toHaveAttribute('href', '#partial-grounded-report');
    expect(screen.getByRole('textbox', { name: 'Chat message' })).toHaveAttribute(
      'placeholder',
      'Ask anything about your candidates…',
    );
    expect(await screen.findByTestId('showcase-graph')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Agents/i }));
    expect(screen.getByTestId('showcase-location')).toHaveTextContent('?mode=agents');
    expect(screen.getByLabelText('Agent conversation')).toBeInTheDocument();
  });
});
