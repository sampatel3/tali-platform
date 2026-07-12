import React from 'react';
import { render, screen } from '@testing-library/react';
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

  it('always renders the Workable card and its body slot', () => {
    renderSection({ active_ats: 'standalone' });
    expect(screen.getByRole('heading', { name: /Workable integration/i })).toBeInTheDocument();
    expect(screen.getByText('WORKABLE_BODY')).toBeInTheDocument();
  });

  it('hides the Bullhorn card when bullhorn_enabled is falsy', () => {
    renderSection({ active_ats: 'standalone' });
    expect(screen.queryByRole('heading', { name: /Bullhorn integration/i })).not.toBeInTheDocument();
    expect(screen.queryByText('BULLHORN_BODY')).not.toBeInTheDocument();
  });

  it('shows the Bullhorn card (from the registry Component) when bullhorn_enabled is true', () => {
    renderSection({ active_ats: 'standalone', bullhorn_enabled: true });
    expect(screen.getByRole('heading', { name: /Bullhorn integration/i })).toBeInTheDocument();
    expect(screen.getByText('BULLHORN_BODY')).toBeInTheDocument();
  });

  it('shows a Connected chip on a connected provider card', () => {
    renderSection({ active_ats: 'workable', workable_connected: true });
    expect(screen.getAllByText('Connected').length).toBeGreaterThan(0);
  });
});
