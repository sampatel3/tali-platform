import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { agent as agentApi, analytics as analyticsApi } from '../../shared/api';
import { AnalyticsPage } from './AnalyticsPage';

// The pulse band lives directly in AnalyticsPage; the five tab bodies are stubbed
// so this test stays focused on the shared Motion stagger and the KPI tickers.
vi.mock('../../shared/api', () => ({
  agent: {
    rolesBreakdown: vi.fn(),
    listFeedback: vi.fn(),
    listDecisions: vi.fn(),
  },
  analytics: {
    reportingSummary: vi.fn(),
    decisionsBreakdown: vi.fn(),
    decisionTrend: vi.fn(),
    costPerOutcome: vi.fn(),
  },
}));
vi.mock('../../context/ToastContext', () => ({ useToast: () => ({ showToast: vi.fn() }) }));
vi.mock('./OutcomesTab', () => ({ OutcomesTab: () => null }));
vi.mock('./FleetTab', () => ({
  FleetTab: ({ onOpenDecisionLog }) => (
    <button type="button" onClick={onOpenDecisionLog}>Open decision log</button>
  ),
}));
vi.mock('./TeachingTab', () => ({ TeachingTab: () => null }));
vi.mock('./ExperimentsTab', () => ({ ExperimentsTab: () => null }));
vi.mock('./DecisionLogTab', () => ({
  DecisionLogTab: () => <div>Decision log panel</div>,
  outcomeOf: () => ({ text: '' }),
}));

const setReducedMotion = (reduced) => {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: reduced && String(query).includes('prefers-reduced-motion'),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
};

const seedApi = () => {
  agentApi.rolesBreakdown.mockResolvedValue({ data: [] });
  agentApi.listFeedback.mockResolvedValue({ data: [] });
  agentApi.listDecisions.mockResolvedValue({ data: [] });
  analyticsApi.decisionTrend.mockResolvedValue({ data: {} });
  analyticsApi.costPerOutcome.mockResolvedValue({ data: null });
  analyticsApi.decisionsBreakdown.mockResolvedValue({
    data: { totals: { advance_conversion: { advanced_total: 200, hired: 40 } } },
  });
  analyticsApi.reportingSummary.mockResolvedValue({
    data: {
      kpis: {
        decisions_made: { current: 1240 },
        auto_advanced: { current: 900 },
        auto_rejected: { current: 120 },
        human_review: { approved: 800, override_rate_pct: 12, overridden: 30, teach_rate_pct: 8, taught: 15 },
        org_spend: { spent_cents: 190000, budget_cents: 500000 },
      },
    },
  });
};

beforeEach(() => {
  vi.clearAllMocks();
  seedApi();
});
afterEach(() => vi.restoreAllMocks());

describe('AnalyticsPage pulse band', () => {
  it('uses the shared Motion stagger for the pulse band', async () => {
    setReducedMotion(false);
    const { container } = render(<AnalyticsPage />);
    await screen.findByText('1,240');

    const band = container.querySelector('.an-pulse');
    expect(band).toBeTruthy();
    // MotionStagger owns the band entrance; there is no legacy CSS animation
    // class or per-cell CSS delay index left behind.
    expect(band).toHaveAttribute('data-motion-stagger', 'analytics-pulse');
    expect(band.classList.contains('reveal-stagger')).toBe(false);
    const cells = container.querySelectorAll('.an-pcell');
    expect(cells).toHaveLength(6);
    expect(cells[0].style.getPropertyValue('--i')).toBe('');
    expect(cells[5].style.getPropertyValue('--i')).toBe('');
  });

  it('lands on the final formatted KPI values under prefers-reduced-motion', async () => {
    setReducedMotion(true);
    render(<AnalyticsPage />);

    // Integer ticker → locale-grouped.
    expect(await screen.findByText('1,240')).toBeInTheDocument();
    // Percent tickers → "N%" (override rate 12%, advance→hire 20%).
    expect(await screen.findByText('12%')).toBeInTheDocument();
    expect(await screen.findByText('20%')).toBeInTheDocument();
    // Money ticker → fmtUsd on cents ("$1,900").
    expect(await screen.findByText('$1,900')).toBeInTheDocument();
  });

  it('switches Fleet to live workspace context and opens the Decision log', async () => {
    setReducedMotion(true);
    const { container } = render(<AnalyticsPage />);

    fireEvent.click(screen.getByRole('tab', { name: 'Agent fleet' }));

    expect(screen.getByRole('tab', { name: 'Agent fleet' })).toHaveAttribute('aria-selected', 'true');
    expect(container.querySelector('.an-pulse')).not.toBeInTheDocument();
    expect(screen.getByText('ANALYTICS · LIVE WORKSPACE')).toBeInTheDocument();
    expect(screen.getByText('Analytics · agent fleet')).toBeInTheDocument();
    expect(screen.queryByLabelText('Role filter')).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Time window' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Export' })).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole('button', { name: 'Open decision log' }));

    expect(screen.getByRole('tab', { name: 'Decision log' })).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(screen.getByText('Decision log panel')).toBeInTheDocument());
  });
});
