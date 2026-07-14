import React, { useCallback, useEffect, useState } from 'react';
import { ChevronDown, RefreshCw } from 'lucide-react';

import { outreach as outreachApi } from '../../shared/api/outreachClient';
import { Spinner } from '../../shared/ui/TaaliPrimitives';
import './campaignFlow.css';

// Compact, in-context campaign performance monitor for a single role. Lives on
// the role's Sourced candidate lens — the same surface where reach-out is run —
// NOT a global Campaigns tab. Reuses the existing list/get endpoints and the
// backend rollup counts (audience → drafted → approved → sent → delivered →
// opened → clicked → interested, + bounced/failed). No new backend.

// The funnel ladder, in send order. Each reads off serialize_campaign counts.
const FUNNEL_STEPS = [
  { key: 'audience', label: 'Audience' },
  { key: 'drafted', label: 'Drafted' },
  { key: 'approved', label: 'Approved' },
  { key: 'sent', label: 'Sent' },
  { key: 'delivered', label: 'Delivered' },
  { key: 'opened', label: 'Opened' },
  { key: 'clicked', label: 'Clicked' },
  { key: 'interested', label: 'Interested' },
];

function apiErrorMessage(err, fallback) {
  const detail = err?.response?.data?.detail;
  return typeof detail === 'string' && detail.trim() ? detail : fallback;
}

function CampaignFunnel({ counts = {} }) {
  const audience = Math.max(1, counts.audience || 0); // avoid /0 for the bar width
  return (
    <div className="cmp-funnel">
      {FUNNEL_STEPS.map((step) => {
        const value = counts[step.key] || 0;
        const pct = Math.round((value / audience) * 100);
        return (
          <div key={step.key} className="cmp-funnel-row">
            <span className="cmp-funnel-label">{step.label}</span>
            <span className="cmp-funnel-bar">
              <span className="cmp-funnel-fill" style={{ width: `${Math.min(100, pct)}%` }} />
            </span>
            <span className="cmp-funnel-value">{value}</span>
          </div>
        );
      })}
      {(counts.bounced || counts.failed) ? (
        <div className="cmp-funnel-foot">
          {counts.bounced ? <span>{counts.bounced} bounced</span> : null}
          {counts.failed ? <span>{counts.failed} failed</span> : null}
        </div>
      ) : null}
    </div>
  );
}

export function CampaignsMonitorPanel({ roleId, focusCampaignId = null, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [expandedId, setExpandedId] = useState(focusCampaignId);

  const load = useCallback(() => {
    if (!Number.isFinite(roleId)) return;
    setLoading(true);
    setError('');
    outreachApi
      .listCampaigns(roleId)
      .then((res) => setCampaigns(res.data?.campaigns || []))
      .catch((err) => setError(apiErrorMessage(err, 'Could not load campaigns.')))
      .finally(() => setLoading(false));
  }, [roleId]);

  // Open + focus a specific campaign (e.g. straight after a reach-out send).
  useEffect(() => {
    if (focusCampaignId != null) {
      setOpen(true);
      setExpandedId(focusCampaignId);
    }
  }, [focusCampaignId]);

  useEffect(() => {
    if (open) load();
  }, [open, load, focusCampaignId]);

  const activeCampaigns = campaigns.filter((c) => c.status !== 'archived');

  return (
    <div className="role-sec cmp-panel">
      <button
        type="button"
        className={`src-panel-toggle ${open ? 'open' : ''}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <div className="role-sec-title">
          <span className="marker">CO</span>
          Campaigns
          {activeCampaigns.length > 0 ? <span className="cmp-count">{activeCampaigns.length}</span> : null}
        </div>
        <ChevronDown className="caret" size={12} />
      </button>

      {open ? (
        <div className="cmp-panel-body">
          <div className="cmp-panel-head">
            <p className="cmp-help">
              Outreach campaigns you sent to sourced candidates for this role. Counts update as messages
              are delivered, opened and clicked.
            </p>
            <button type="button" className="btn btn-outline btn-sm" onClick={load} disabled={loading}>
              {loading ? <Spinner size={12} className="!text-current" /> : <RefreshCw size={12} />}
              Refresh
            </button>
          </div>

          {error ? <div className="rc-error">{error}</div> : null}

          {!loading && activeCampaigns.length === 0 ? (
            <div className="cmp-empty">
              No campaigns yet. Select sourced candidates in the Candidates tab and choose
              <strong> Reach out</strong> to start one.
            </div>
          ) : null}

          <ul className="cmp-list">
            {activeCampaigns.map((c) => {
              const counts = c.counts || {};
              const isOpen = expandedId === c.id;
              return (
                <li key={c.id} className="cmp-item">
                  <button
                    type="button"
                    className="cmp-item-head"
                    onClick={() => setExpandedId(isOpen ? null : c.id)}
                    aria-expanded={isOpen}
                  >
                    <span className="cmp-item-name">{c.name}</span>
                    <span className={`cmp-chip cmp-chip-${c.status}`}>{c.status}</span>
                    <span className="cmp-item-summary">
                      {counts.sent || 0} sent · {counts.opened || 0} opened · {counts.interested || 0} interested
                    </span>
                    <ChevronDown className={`caret ${isOpen ? 'open' : ''}`} size={12} />
                  </button>
                  {isOpen ? <CampaignFunnel counts={counts} /> : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export default CampaignsMonitorPanel;
