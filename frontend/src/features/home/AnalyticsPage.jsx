// AnalyticsPage — the dedicated /analytics route.
//
// Sam's call (candidate-UX consolidation): the analytics/reporting/teaching
// surface lives on its OWN page, off the home review loop — home keeps a compact
// pulse + an "Open full analytics →" link. This page reuses the existing
// HomeMonitoring console (Activity / Outcomes / Quality / A-B / History) in
// `standalone` mode (open by default, no collapse toggle) so there's a single
// analytics implementation rather than two. Reverses the earlier "reporting +
// analytics fold into the Hub bottom" routing (docs/HOME_HUB_DESIGN.md §4) — the
// route now renders a real page instead of redirecting to /home.
import React, { useCallback, useEffect, useState } from 'react';

import { AgentHeader } from '../../shared/layout/AgentHeader';
import { agent as agentApi } from '../../shared/api';
import { HomeMonitoring } from './HomeMonitoring';

export const AnalyticsPage = ({ onNavigate, NavComponent }) => {
  const [rolesBreakdown, setRolesBreakdown] = useState([]);
  const [feedback, setFeedback] = useState([]);
  const [outcomes, setOutcomes] = useState([]);
  const [loading, setLoading] = useState(true);

  // Same three feeds HomePage fed into HomeMonitoring; the console fetches its
  // own per-tab analytics (summary / breakdown / decision log) once open.
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [rb, fb, oc] = await Promise.all([
        agentApi.rolesBreakdown(),
        agentApi.listFeedback({ limit: 20 }),
        agentApi.realisedOutcomes({ limit: 20 }),
      ]);
      setRolesBreakdown(Array.isArray(rb?.data) ? rb.data : []);
      setFeedback(Array.isArray(fb?.data) ? fb.data : []);
      setOutcomes(Array.isArray(oc?.data) ? oc.data : []);
    } catch {
      // HomeMonitoring degrades gracefully on empty data.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="analytics" onNavigate={onNavigate} /> : null}
      <AgentHeader
        breadcrumbs={[{ label: 'Analytics' }]}
        kicker="ANALYTICS · AGENT REPORTING"
        title="Analytics"
        subtitle="Outcomes, the agent fleet, the teaching history, and the decision log — the reporting layer, off the home review loop."
      />
      <div className="page">
        <HomeMonitoring
          standalone
          rolesBreakdown={rolesBreakdown}
          feedback={feedback}
          outcomes={outcomes}
          loadingSignal={loading}
          reload={load}
          onNavigate={onNavigate}
          onSelect={(id) => onNavigate?.('candidate-report', { candidateApplicationId: id })}
        />
      </div>
    </div>
  );
};

export default AnalyticsPage;
