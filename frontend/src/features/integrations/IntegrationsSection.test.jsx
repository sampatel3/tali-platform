import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

// Stub the Bullhorn body so we exercise the section's gating/indicator logic
// without pulling in the JobStatus context the real component needs.
vi.mock('./BullhornConnection', () => ({
  BullhornConnection: () => <div>BULLHORN_BODY</div>,
}));

import { IntegrationsSection } from './IntegrationsSection';

const renderSection = (org, bodies = { workable: <div>WORKABLE_BODY</div> }) =>
  render(<IntegrationsSection org={org} bodies={bodies} />);

describe('IntegrationsSection', () => {
  it('shows the Active ATS indicator as Workable', () => {
    renderSection({ active_ats: 'workable', workable_connected: true });
    expect(screen.getByText('Active ATS')).toBeInTheDocument();
    expect(screen.getByText('Workable', { selector: '.settings-integration-chip' })).toBeInTheDocument();
  });

  it('shows the Active ATS indicator as Bullhorn', () => {
    renderSection({ active_ats: 'bullhorn', bullhorn_enabled: true, bullhorn_connected: true });
    expect(screen.getByText('Bullhorn', { selector: '.settings-integration-chip' })).toBeInTheDocument();
  });

  it('shows Standalone indicator + info line and no info line otherwise', () => {
    const { unmount } = renderSection({ active_ats: 'standalone' });
    expect(screen.getByText('Standalone', { selector: '.settings-integration-chip' })).toBeInTheDocument();
    expect(screen.getByText(/Taali runs standalone/i)).toBeInTheDocument();
    unmount();

    renderSection({ active_ats: 'workable', workable_connected: true });
    expect(screen.queryByText(/Taali runs standalone/i)).not.toBeInTheDocument();
  });

  it('defaults the indicator to Standalone when active_ats is missing', () => {
    renderSection({});
    expect(screen.getByText('Standalone', { selector: '.settings-integration-chip' })).toBeInTheDocument();
  });

  it('reflects a fresh Workable connect even before active_ats refetches', () => {
    // The token-connect path flips workable_connected in local state but the
    // serialized active_ats stays stale ('standalone') until a full refetch —
    // the indicator must derive from the live connection fields and show Workable.
    renderSection({ active_ats: 'standalone', workable_connected: true });
    expect(screen.getByText('Workable', { selector: '.settings-integration-chip' })).toBeInTheDocument();
    expect(screen.queryByText(/Taali runs standalone/i)).not.toBeInTheDocument();
  });

  it('always renders the Workable card and its body slot', () => {
    // Title lives in the toggle button now, so query by text (a button flattens
    // its descendant heading out of the accessibility tree).
    renderSection({ active_ats: 'standalone' });
    expect(screen.getByText('Workable integration')).toBeInTheDocument();
    expect(screen.getByText('WORKABLE_BODY')).toBeInTheDocument();
  });

  it('hides the Bullhorn card when bullhorn_enabled is falsy', () => {
    renderSection({ active_ats: 'standalone' });
    expect(screen.queryByText('Bullhorn integration')).not.toBeInTheDocument();
    expect(screen.queryByText('BULLHORN_BODY')).not.toBeInTheDocument();
  });

  it('shows the Bullhorn card (from the registry Component) when bullhorn_enabled is true', () => {
    renderSection({ active_ats: 'standalone', bullhorn_enabled: true });
    expect(screen.getByText('Bullhorn integration')).toBeInTheDocument();
    expect(screen.getByText('BULLHORN_BODY')).toBeInTheDocument();
  });

  it('shows a Connected chip on a connected provider card', () => {
    renderSection({ active_ats: 'workable', workable_connected: true });
    expect(screen.getAllByText('Connected').length).toBeGreaterThan(0);
  });

  it('expands a connected card and collapses an unconnected one by default', () => {
    // Workable connected → open; Bullhorn enabled-but-unconnected → collapsed.
    renderSection({ active_ats: 'workable', workable_connected: true, bullhorn_enabled: true });
    const workableBody = screen.getByText('WORKABLE_BODY').closest('.settings-integration-card-body');
    const bullhornBody = screen.getByText('BULLHORN_BODY').closest('.settings-integration-card-body');
    expect(workableBody).not.toHaveAttribute('hidden');
    expect(bullhornBody).toHaveAttribute('hidden');
  });

  it('expands a collapsed card when its header is clicked', () => {
    renderSection({ active_ats: 'standalone', bullhorn_enabled: true });
    const bullhornBody = screen.getByText('BULLHORN_BODY').closest('.settings-integration-card-body');
    expect(bullhornBody).toHaveAttribute('hidden');
    fireEvent.click(screen.getByRole('button', { name: /Bullhorn integration/i }));
    expect(bullhornBody).not.toHaveAttribute('hidden');
  });

  it('opens a connected card once org data loads (mounts with org=null first)', () => {
    // First mount: org still loading → Workable arrives unconnected → collapsed.
    const bodies = { workable: <div>WORKABLE_BODY</div> };
    const { rerender } = render(<IntegrationsSection org={null} bodies={bodies} />);
    expect(
      screen.getByText('WORKABLE_BODY').closest('.settings-integration-card-body'),
    ).toHaveAttribute('hidden');

    // Org data arrives connected → the card reveals itself.
    rerender(
      <IntegrationsSection org={{ active_ats: 'workable', workable_connected: true }} bodies={bodies} />,
    );
    expect(
      screen.getByText('WORKABLE_BODY').closest('.settings-integration-card-body'),
    ).not.toHaveAttribute('hidden');
  });
});
