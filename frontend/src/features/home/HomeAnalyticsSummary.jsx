// Compact analytics summary for the hub — a high-level pulse + a link to the
// full Analytics page. The detailed console (outcomes, fleet, teaching history,
// A/B, decision log) lives on /analytics, kept off the hub to keep the review
// loop focused. Every value reads the lightweight org-status poll the page
// already runs — no extra reporting queries fire on home.

import React from 'react';
import { LineChart, ArrowUpRight } from 'lucide-react';

import { formatCount, formatMoneyUsd } from '../../shared/metrics';
import { MotionNumber } from '../../shared/motion';

const pct = (v) => `${Math.round(Number(v) || 0)}%`;

// Values interpolate from their previous settled state when the lightweight
// org-status poll updates. Reduced motion renders the next value immediately.
const PulseValue = ({ to, format, unit }) => (
  <div className="home-pulse-v">
    <MotionNumber value={Number(to) || 0} format={format} />
    {unit ? <span className="home-pulse-unit"> {unit}</span> : null}
  </div>
);

export const HomeAnalyticsSummary = ({ kpis = {}, orgBudget = null, onNavigate }) => {
  const cells = [
    { k: 'Decisions today', to: kpis.today || 0, format: formatCount },
    { k: 'Auto-advanced', to: kpis.auto_applied_today || 0, format: formatCount },
    { k: 'Override rate', to: kpis.override_rate_pct || 0, format: pct },
    { k: 'Taught', to: kpis.teach_rate_pct || 0, format: pct },
    { k: 'Spend · MTD', to: kpis.org_budget_spent_cents || 0, format: formatMoneyUsd, unit: orgBudget?.unit || '' },
  ];

  return (
    <section className="home-section home-pulse reveal" style={{ '--reveal-delay': '0.16s' }}>
      <div className="home-section-head home-pulse-head">
        <span className="kicker">ANALYTICS · PLATFORM PULSE</span>
        <button
          type="button"
          className="home-pulse-link"
          onClick={() => onNavigate?.('analytics')}
        >
          <LineChart size={14} aria-hidden="true" /> Open full analytics
          <ArrowUpRight size={14} aria-hidden="true" />
        </button>
      </div>

      <div className="home-pulse-stats">
        {cells.map((c) => (
          <div className="home-pulse-stat" key={c.k}>
            <div className="home-pulse-k">{c.k}</div>
            <PulseValue to={c.to} format={c.format} unit={c.unit} />
          </div>
        ))}
      </div>

      <p className="home-pulse-note">
        Outcomes, quality, A/B, and the agent&apos;s teaching history live on the
        full <b>Analytics</b> page — kept off the hub to keep the review loop
        focused.
      </p>
    </section>
  );
};

export default HomeAnalyticsSummary;
