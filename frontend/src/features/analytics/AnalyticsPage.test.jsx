import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { agent as agentApi, analytics as analyticsApi } from '../../shared/api';
import { AnalyticsPage } from './AnalyticsPage';

// The pulse band lives directly in AnalyticsPage; the five tab bodies are stubbed
// so this test stays focused on the entrance-reveal classes and the KPI tickers.
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
    activityTimeseries: vi.fn(),
  },
}));
vi.mock('../../context/ToastContext', () => ({ useToast: () => ({ showToast: vi.fn() }) }));
vi.mock('./OutcomesTab', () => ({ OutcomesTab: () => null }));
vi.mock('./FleetTab', () => ({ FleetTab: () => null }));
vi.mock('./TeachingTab', () => ({ TeachingTab: () => null }));
vi.mock('./ExperimentsTab', () => ({ ExperimentsTab: () => null }));
vi.mock('./DecisionLogTab', () => ({ DecisionLogTab: () => null, outcomeOf: () => ({ text: '' }) }));

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
  analyticsApi.activityTimeseries.mockResolvedValue({ data: { series: [] } });
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
  it('renders the entrance-reveal + stagger classes on the pulse band', async () => {
    setReducedMotion(false);
    const { container } = render(<AnalyticsPage />);

    const band = container.querySelector('.an-pulse');
    expect(band).toBeTruthy();
    // Stagger wrapper drives both the band entrance and the per-cell offset.
    expect(band.classList.contains('reveal-stagger')).toBe(true);
    const cells = container.querySelectorAll('.an-pcell');
    expect(cells).toHaveLength(6);
    // Each cell carries its stagger index for the reveal offset.
    expect(cells[0].style.getPropertyValue('--i')).toBe('0');
    expect(cells[5].style.getPropertyValue('--i')).toBe('5');
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
});
