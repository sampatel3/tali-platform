import React, { useState } from 'react';
import PolicyView from './PolicyView';
import PendingRetuneReview from './PendingRetuneReview';
import SignalsDashboard from './SignalsDashboard';

const TABS = [
  { key: 'policy', label: 'Active policy' },
  { key: 'pending', label: 'Pending retunes' },
  { key: 'signals', label: 'Signals' },
];

// Single-page Hub for the decision policy. The four-tab layout maps
// 1:1 to the four views in §6 of CLAUDE.md (DecisionExplainer is
// embedded into AgentDecision panels rather than living here).
export default function DecisionPolicyPage() {
  const [tab, setTab] = useState('policy');
  return (
    <div className="dp-page">
      <nav className="dp-tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            className={tab === t.key ? 'dp-tab dp-tab-active' : 'dp-tab'}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <div className="dp-tab-body">
        {tab === 'policy' && <PolicyView />}
        {tab === 'pending' && <PendingRetuneReview />}
        {tab === 'signals' && <SignalsDashboard />}
      </div>
    </div>
  );
}
