import React from 'react';
import { Activity, GitPullRequestArrow, ShieldCheck } from 'lucide-react';
import { useLocation } from 'react-router-dom';
import { FocusedSectionNav } from '../../shared/ui/TaaliPrimitives';
import PolicyView from './PolicyView';
import PendingRetuneReview from './PendingRetuneReview';
import SignalsDashboard from './SignalsDashboard';

const TABS = [
  { key: 'policy', label: 'Active policy', Icon: ShieldCheck },
  { key: 'pending', label: 'Pending retunes', Icon: GitPullRequestArrow },
  { key: 'signals', label: 'Signals', Icon: Activity },
];

// Single-page hub for the three decision-policy views.
export default function DecisionPolicyPage() {
  const location = useLocation();
  const requestedTab = new URLSearchParams(location.search).get('tab') || 'policy';
  const tab = TABS.some((item) => item.key === requestedTab) ? requestedTab : 'policy';
  const items = TABS.map((item) => {
    const params = new URLSearchParams(location.search);
    if (item.key === 'policy') params.delete('tab');
    else params.set('tab', item.key);
    const query = params.toString();
    return {
      id: item.key,
      label: item.label,
      Icon: item.Icon,
      to: `${location.pathname}${query ? `?${query}` : ''}${location.hash || ''}`,
    };
  });
  return (
    <div className="dp-page">
      <FocusedSectionNav
        items={items}
        activeId={tab}
        ariaLabel="Decision policy views"
        idPrefix="decision-policy-view"
        variant="bar"
        sticky={false}
      />
      <div className="dp-tab-body">
        {tab === 'policy' && <PolicyView />}
        {tab === 'pending' && <PendingRetuneReview />}
        {tab === 'signals' && <SignalsDashboard />}
      </div>
    </div>
  );
}
