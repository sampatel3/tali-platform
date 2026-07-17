import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { FocusedSectionLayout, FocusedSectionNav } from './SectionNavigation';

const ITEMS = [
  { id: 'overview', label: 'Overview', meta: '4 overrides' },
  { id: 'guidance', label: 'Guidance', description: 'Criteria and feedback', meta: '9' },
  { id: 'budget', label: 'Budget & limits', disabled: true },
];

describe('FocusedSectionNav', () => {
  it('exposes one current controlled section and changes it', () => {
    const onChange = vi.fn();
    render(
      <MemoryRouter>
        <FocusedSectionNav
          items={ITEMS}
          activeId="overview"
          onChange={onChange}
          ariaLabel="Agent settings sections"
        />
      </MemoryRouter>,
    );

    const overview = screen.getByRole('button', { name: /Overview.*4 overrides/i });
    expect(overview).toHaveAttribute('aria-current', 'page');
    expect(overview).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByRole('button', { name: /Guidance.*Criteria and feedback.*9/i }));
    expect(onChange).toHaveBeenCalledWith('guidance');
    expect(screen.getByRole('button', { name: /Budget & limits/i })).toBeDisabled();
  });

  it('renders URL-backed sections as links', () => {
    render(
      <MemoryRouter>
        <FocusedSectionNav
          items={[
            { id: 'one', label: 'One', to: '/settings#one' },
            { id: 'two', label: 'Two', to: '/settings#two' },
          ]}
          activeId="two"
        />
      </MemoryRouter>,
    );

    const link = screen.getByRole('link', { name: 'Two' });
    expect(link).toHaveAttribute('href', '/settings#two');
    expect(link).toHaveAttribute('aria-current', 'page');
  });

  it('renders URL-backed items without requiring a Router provider', () => {
    render(
      <FocusedSectionNav
        items={[{ id: 'guidance', label: 'Guidance', to: '/jobs/12?section=guidance' }]}
        activeId="guidance"
      />,
    );

    expect(screen.getByRole('link', { name: 'Guidance' }))
      .toHaveAttribute('href', '/jobs/12?section=guidance');
  });

  it('falls back to the first visible enabled item', () => {
    render(
      <FocusedSectionNav
        items={[
          { id: 'hidden', label: 'Hidden', hidden: true },
          { id: 'locked', label: 'Locked', disabled: true },
          { id: 'available', label: 'Available' },
        ]}
        activeId="missing"
      />,
    );

    expect(screen.queryByText('Hidden')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Available' }))
      .toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: 'Locked' }))
      .not.toHaveAttribute('aria-current');
  });

  it('groups a long rail and supports accessible tone badges', () => {
    const { container } = render(
      <FocusedSectionNav
        items={[
          { id: 'org', label: 'Organization', group: 'Workspace' },
          {
            id: 'security',
            label: 'Security',
            group: { id: 'governance', label: 'Governance' },
            tone: 'warning',
            badge: { label: '3', ariaLabel: '3 issues', tone: 'warning' },
          },
        ]}
        activeId="org"
      />,
    );

    expect(screen.getByRole('group', { name: 'Workspace' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Governance' })).toBeInTheDocument();
    expect(screen.getByLabelText('3 issues')).toHaveClass('focused-section-nav__badge--warning');
    expect(container.querySelector('[data-section-id="security"]'))
      .toHaveAttribute('data-tone', 'warning');
  });

  it('does not invent numbered markers for horizontal peer views', () => {
    const { container } = render(
      <FocusedSectionNav items={ITEMS} activeId="overview" variant="bar" />,
    );

    expect(container.querySelector('.focused-section-nav__marker')).not.toBeInTheDocument();
  });

  it('scrolls an off-screen active item into the selector viewport', () => {
    const { container, rerender } = render(
      <FocusedSectionNav items={ITEMS} activeId="overview" />,
    );
    const nav = container.querySelector('.focused-section-nav');
    const guidance = screen.getByRole('button', { name: /Guidance/i });
    nav.getBoundingClientRect = vi.fn(() => ({
      left: 0, right: 200, width: 200, top: 0, bottom: 40, height: 40, x: 0, y: 0,
      toJSON: () => ({}),
    }));
    guidance.getBoundingClientRect = vi.fn(() => ({
      left: 260, right: 360, width: 100, top: 0, bottom: 40, height: 40, x: 260, y: 0,
      toJSON: () => ({}),
    }));
    nav.scrollTo = vi.fn();

    rerender(<FocusedSectionNav items={ITEMS} activeId="guidance" />);

    expect(nav.scrollTo).toHaveBeenCalledWith({ left: 210, behavior: 'smooth' });
  });
});

describe('FocusedSectionLayout', () => {
  it('labels the active content region from the active navigation item', () => {
    render(
      <FocusedSectionLayout items={ITEMS} activeId="guidance" idPrefix="agent-settings">
        <h2>Guidance content</h2>
      </FocusedSectionLayout>,
    );

    expect(screen.getByRole('region', { name: /Guidance.*Criteria and feedback.*9/i }))
      .toHaveTextContent('Guidance content');
  });

  it('uses safe generated ids and keeps the content label connected', () => {
    render(
      <FocusedSectionLayout
        items={[{ id: 'Budget & Limits', label: 'Budget & limits' }]}
        activeId="Budget & Limits"
      >
        <h2>Budget content</h2>
      </FocusedSectionLayout>,
    );

    const item = screen.getByRole('button', { name: 'Budget & limits' });
    const region = screen.getByRole('region', { name: 'Budget & limits' });
    expect(item.id).not.toMatch(/[\s:&]/);
    expect(region).toHaveAttribute('aria-labelledby', item.id);
  });
});
