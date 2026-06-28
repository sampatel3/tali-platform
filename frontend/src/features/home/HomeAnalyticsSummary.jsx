// Compact analytics summary for the hub — a high-level pulse + a link to the
// full Analytics page. The detailed console (outcomes, fleet, teaching history,
// A/B, decision log) lives on /analytics, kept off the hub to keep the review
// loop focused. Every value reads the lightweight org-status poll the page
// already runs — no extra reporting queries fire on home.

import React from 'react';
import { LineChart, ArrowUpRight } from 'lucide-react';

import { formatCount } from '../../shared/metrics';

const pct = (v) => `${Math.round(Number(v) || 0)}%`;

export const HomeAnalyticsSummary = ({ kpis = {}, orgBudget = null, onNavigate }) => {
  const cells = [
    { k: 'Decisions today', v: formatCount(kpis.today || 0) },
    { k: 'Auto-advanced', v: formatCount(kpis.auto_applied_today || 0) },
    { k: 'Override rate', v: pct(kpis.override_rate_pct) },
    { k: 'Taught', v: pct(kpis.teach_rate_pct) },
    { k: 'Spend · MTD', v: orgBudget?.value || '—', unit: orgBudget?.unit || '' },
  ];

  return (
    <section className="home-section home-pulse">
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
            <div className="home-pulse-v">
              {c.v}
              {c.unit ? <span className="home-pulse-unit"> {c.unit}</span> : null}
            </div>
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
