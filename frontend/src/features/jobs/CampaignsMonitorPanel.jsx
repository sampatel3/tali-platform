import React, { useCallback, useEffect, useState } from 'react';
import { ChevronDown, RefreshCw, Send, Sparkles } from 'lucide-react';

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
  const [sendEstimates, setSendEstimates] = useState({});
  const [campaignDetails, setCampaignDetails] = useState({});
  const [approvingId, setApprovingId] = useState(null);

  const load = useCallback(() => {
    if (!Number.isFinite(roleId)) return;
    setLoading(true);
    setError('');
    outreachApi
      .listCampaigns(roleId)
      .then(async (res) => {
        const next = res.data?.campaigns || [];
        setCampaigns(next);
        const awaitingAgentApproval = next.find(
          (campaign) => campaign.origin === 'agent' && campaign.status === 'ready',
        );
        if (awaitingAgentApproval) {
          setOpen(true);
          setExpandedId((current) => current ?? awaitingAgentApproval.id);
        }
        const ready = next.filter((campaign) => campaign.status === 'ready');
        if (ready.length === 0) {
          setSendEstimates({});
          setCampaignDetails({});
          return;
        }
        const reviewData = await Promise.all(
          ready.map(async (campaign) => {
            const [estimate, detail] = await Promise.all([
              outreachApi.approveAndSend(campaign.id, false)
                .then((response) => response.data || null)
                .catch(() => null),
              outreachApi.getCampaign(campaign.id)
                .then((response) => response.data || null)
                .catch(() => null),
            ]);
            return { id: campaign.id, estimate, detail };
          }),
        );
        setSendEstimates(Object.fromEntries(
          reviewData.map((item) => [item.id, item.estimate]),
        ));
        setCampaignDetails(Object.fromEntries(
          reviewData.map((item) => [item.id, item.detail]),
        ));
      })
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

  // Load even while collapsed so a campaign prepared by the role agent can
  // surface its one required outbound approval without a manual refresh.
  useEffect(() => { load(); }, [load, focusCampaignId]);

  useEffect(() => {
    const inFlight = campaigns.some((campaign) =>
      campaign.status === 'generating' || campaign.status === 'sending');
    if (!inFlight) return undefined;
    const timer = window.setInterval(load, 3000);
    return () => window.clearInterval(timer);
  }, [campaigns, load]);

  const approveAndSend = useCallback(async (campaign) => {
    const estimate = sendEstimates[campaign.id];
    if (!estimate || !estimate.will_send) return;
    setApprovingId(campaign.id);
    setError('');
    try {
      await outreachApi.approveAndSend(
        campaign.id,
        true,
        estimate.will_send,
        estimate.review_token,
      );
      await load();
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not approve and send this campaign.'));
      await load();
    } finally {
      setApprovingId(null);
    }
  }, [load, sendEstimates]);

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
              Taali prepares sourced outreach automatically. You only approve the outbound send;
              delivery, opens, clicks and interest update here.
            </p>
            <button type="button" className="btn btn-outline btn-sm" onClick={load} disabled={loading}>
              {loading ? <Spinner size={12} className="!text-current" /> : <RefreshCw size={12} />}
              Refresh
            </button>
          </div>

          <div className="cmp-source-status" aria-label="Automated sourcing providers">
            <span><strong>Internal talent pool</strong> · agent ready</span>
            <span><strong>LinkedIn RSC</strong> · partner access required; one-click export only</span>
          </div>

          {error ? <div className="rc-error">{error}</div> : null}

          {!loading && activeCampaigns.length === 0 ? (
            <div className="cmp-empty">
              No campaigns yet. The role agent will search the internal talent pool and prepare
              outreach when the funnel needs more candidates.
            </div>
          ) : null}

          <ul className="cmp-list">
            {activeCampaigns.map((c) => {
              const counts = c.counts || {};
              const isOpen = expandedId === c.id;
              const estimate = sendEstimates[c.id];
              const reviewMessages = (campaignDetails[c.id]?.messages || []).filter(
                (message) => message.status === 'draft' || message.status === 'approved',
              );
              const isAgentPrepared = c.origin === 'agent';
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
                  {isOpen ? (
                    <>
                      {isAgentPrepared ? (
                        <div className="cmp-agent-note">
                          <Sparkles size={13} /> Prepared by Taali · no candidate selection required
                        </div>
                      ) : null}
                      <CampaignFunnel counts={counts} />
                      {c.status === 'ready' ? (
                        <div className="cmp-review-block">
                          <div className="cmp-draft-preview" aria-label="Recipients and outreach drafts">
                            <strong>Review recipients and drafts</strong>
                            {reviewMessages.length > 0 ? (
                              <ul>
                                {reviewMessages.map((message) => (
                                  <li key={message.id}>
                                    <div className="cmp-draft-recipient">
                                      <span>{message.recipient_name || message.email}</span>
                                      <span>{message.email}</span>
                                    </div>
                                    <div className="cmp-draft-subject">
                                      {message.subject || '(No subject)'}
                                    </div>
                                    <p>{message.body || '(Draft body unavailable)'}</p>
                                  </li>
                                ))}
                              </ul>
                            ) : (
                              <span>Loading the exact outbound content…</span>
                            )}
                          </div>
                          <div className="cmp-send-hitl">
                            <div>
                              <strong>Outbound approval required</strong>
                              <span>
                                {estimate
                                  ? `${estimate.will_send || 0} message${estimate.will_send === 1 ? '' : 's'} ready`
                                  : 'Checking the final send count…'}
                              </span>
                            </div>
                            <button
                              type="button"
                              className="btn btn-primary btn-sm"
                              disabled={
                                !estimate?.will_send
                                || !estimate?.review_token
                                || reviewMessages.length !== estimate?.sendable_count
                                || approvingId === c.id
                              }
                              onClick={() => approveAndSend(c)}
                            >
                              {approvingId === c.id
                                ? <Spinner size={12} className="!text-current" />
                                : <Send size={12} />}
                              Approve &amp; send {estimate?.will_send || ''}
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </>
                  ) : null}
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
