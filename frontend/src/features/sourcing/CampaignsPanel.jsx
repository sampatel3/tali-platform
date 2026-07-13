import React, { useCallback, useEffect, useRef, useState } from 'react';

import { outreach as outreachApi } from '../../shared/api/outreachClient';
import { prospects as prospectsApi } from '../../shared/api/prospectsClient';
import { roles as rolesApi } from '../../shared/api/rolesClient';
import ConfirmDialog from '../chat/ConfirmDialog';
import './sourcingPanels.css';

// Per-message draft cost (USD) — mirrors the backend COST_PER_DRAFT_USD so the
// cost-confirm dialog shows the same estimate before the recruiter confirms.
const COST_PER_DRAFT_USD = 0.006;
const POLL_INTERVAL_MS = 2000;
const MAX_POLL_ATTEMPTS = 30;
const AUDIENCE_PAGE_SIZE = 50;
const ACTIVE_CAMPAIGN_STATUSES = new Set(['generating', 'sending']);
const EDITABLE_CAMPAIGN_STATUSES = new Set(['draft', 'ready']);
const TERMINAL_JOB_STATUSES = new Set(['filled', 'filled_external', 'cancelled']);
const TERMINAL_WORKABLE_STATES = new Set(['closed', 'archived']);

function isSourceableRole(role) {
  const jobStatus = String(role?.job_status || '').toLowerCase();
  const workableState = String(role?.workable_job_state || '').toLowerCase();
  return !TERMINAL_JOB_STATUSES.has(jobStatus) && !TERMINAL_WORKABLE_STATES.has(workableState);
}

function apiErrorMessage(err, fallback) {
  const detail = err?.response?.data?.detail;
  return typeof detail === 'string' && detail.trim() ? detail : fallback;
}

function StatusChip({ status }) {
  return <span className={`cmp-chip cmp-chip-${status}`}>{status}</span>;
}

// Outreach campaigns tab. List → drill into a campaign → build audience,
// generate drafts (cost-confirm), review + approve/reject, send (confirm),
// watch results. Nothing sends without explicit per-message approval + a send
// confirm; the backend enforces the approval gate absolutely.
export default function CampaignsPanel({ initialCampaignId = null, onCampaignChange = null }) {
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedId, setSelectedId] = useState(initialCampaignId);

  const loadList = useCallback(() => {
    setLoading(true);
    setError('');
    outreachApi
      .listCampaigns()
      .then((res) => setCampaigns(res.data?.campaigns || []))
      .catch((err) => setError(apiErrorMessage(err, 'Could not load campaigns.')))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    setSelectedId(initialCampaignId);
  }, [initialCampaignId]);

  useEffect(() => {
    if (selectedId == null) loadList();
  }, [loadList, selectedId]);

  const selectCampaign = (id) => {
    setSelectedId(id);
    onCampaignChange?.(id);
  };

  if (selectedId) {
    return (
      <CampaignDetail
        campaignId={selectedId}
        onBack={() => {
          selectCampaign(null);
        }}
      />
    );
  }

  return (
    <div>
      <NewCampaign onCreated={selectCampaign} />
      {error ? (
        <div className="src-form-error" role="alert">
          {error}{' '}
          <button type="button" className="src-link" onClick={loadList}>Try again</button>
        </div>
      ) : null}
      {loading ? (
        <div className="src-muted">Loading campaigns…</div>
      ) : campaigns.length === 0 ? (
        <div className="src-muted">No campaigns yet. Create one above.</div>
      ) : (
        <table className="src-table" data-testid="campaigns-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>Sent</th>
              <th>Opened</th>
              <th>Clicked</th>
              <th>Interested</th>
              <th aria-label="Open" />
            </tr>
          </thead>
          <tbody>
            {campaigns.map((c) => {
              const k = c.counts || {};
              return (
                <tr key={c.id}>
                  <td data-label="Name">{c.name}</td>
                  <td data-label="Status"><StatusChip status={c.status} /></td>
                  <td data-label="Sent">{k.sent || 0}</td>
                  <td data-label="Opened">{k.opened || 0}</td>
                  <td data-label="Clicked">{k.clicked || 0}</td>
                  <td data-label="Interested">{k.interested || 0}</td>
                  <td data-label="Action">
                    <button type="button" className="src-link" onClick={() => selectCampaign(c.id)}>
                      Open
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function NewCampaign({ onCreated }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [roleId, setRoleId] = useState('');
  const [roleOptions, setRoleOptions] = useState([]);
  const [rolesLoading, setRolesLoading] = useState(false);
  const [rolesError, setRolesError] = useState('');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (!open) return;
    let active = true;
    setRolesLoading(true);
    setRolesError('');
    rolesApi
      .list()
      .then((res) => {
        if (!active) return;
        const items = Array.isArray(res.data) ? res.data : res.data?.roles || [];
        setRoleOptions(items.filter(isSourceableRole));
      })
      .catch((loadErr) => {
        if (!active) return;
        setRoleOptions([]);
        setRolesError(apiErrorMessage(loadErr, 'Could not load open roles. You can still create a general campaign.'));
      })
      .finally(() => active && setRolesLoading(false));
    return () => {
      active = false;
    };
  }, [open]);

  const create = () => {
    if (!name.trim()) {
      setErr('Name is required.');
      return;
    }
    setSaving(true);
    setErr('');
    outreachApi
      .createCampaign({ name: name.trim(), role_id: roleId ? Number(roleId) : null })
      .then((res) => onCreated(res.data.id))
      .catch((createErr) => setErr(apiErrorMessage(createErr, 'Could not create the campaign.')))
      .finally(() => setSaving(false));
  };

  if (!open) {
    return (
      <div style={{ marginBottom: 16 }}>
        <button type="button" className="src-btn" onClick={() => setOpen(true)}>
          New campaign
        </button>
      </div>
    );
  }

  return (
    <div className="src-form">
      <div className="src-form-grid">
        <label className="src-field">
          <span className="src-field-label">Campaign name</span>
          <input
            className="src-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </label>
        <label className="src-field">
          <span className="src-field-label">Open role</span>
          <select
            className="src-input"
            value={roleId}
            onChange={(e) => setRoleId(e.target.value)}
            disabled={rolesLoading}
          >
            <option value="">{rolesLoading ? 'Loading open roles…' : 'No role (general)'}</option>
            {roleOptions.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
        </label>
      </div>
      {rolesError ? <div className="src-form-error" role="alert">{rolesError}</div> : null}
      {err ? <div className="src-form-error" role="alert">{err}</div> : null}
      <div className="src-form-actions">
        <button type="button" className="src-btn" onClick={create} disabled={saving}>
          {saving ? 'Creating…' : 'Create campaign'}
        </button>
        <button type="button" className="src-btn src-btn-ghost" onClick={() => setOpen(false)} disabled={saving}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function CampaignDetail({ campaignId, onBack }) {
  const campaignIdRef = useRef(campaignId);
  campaignIdRef.current = campaignId;
  const [campaign, setCampaign] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');
  const [pollMessage, setPollMessage] = useState('');
  const [brief, setBrief] = useState('');
  const [genConfirm, setGenConfirm] = useState(false);
  const [genEst, setGenEst] = useState(null);
  const [sendConfirm, setSendConfirm] = useState(false);
  const [sendMeta, setSendMeta] = useState(null);
  const [batchConfirm, setBatchConfirm] = useState(false);
  const [batchMeta, setBatchMeta] = useState(null);
  const [archiveConfirm, setArchiveConfirm] = useState(false);
  const [skipped, setSkipped] = useState(null);
  const [busyAction, setBusyAction] = useState('');

  const refreshCampaign = useCallback(async ({ syncBrief = false } = {}) => {
    const res = await outreachApi.getCampaign(campaignId);
    if (campaignIdRef.current !== campaignId) return null;
    setCampaign(res.data);
    if (syncBrief) setBrief(res.data.brief || '');
    return res.data;
  }, [campaignId]);

  useEffect(() => {
    let active = true;
    setCampaign(null);
    setLoadError('');
    setActionError('');
    setNotice('');
    refreshCampaign({ syncBrief: true }).catch((err) => {
      if (active) setLoadError(apiErrorMessage(err, 'Could not load the campaign.'));
    });
    return () => {
      active = false;
    };
  }, [campaignId, refreshCampaign]);

  // Generation and sending run in Celery. Poll only while one of those jobs is
  // active, and stop after one minute so a stuck worker cannot create an
  // unbounded request loop in an idle browser tab.
  useEffect(() => {
    if (!ACTIVE_CAMPAIGN_STATUSES.has(campaign?.status)) {
      setPollMessage('');
      return undefined;
    }

    let cancelled = false;
    let timeoutId;
    let attempts = 0;
    const activeLabel = campaign.status === 'generating' ? 'Draft generation' : 'Sending';

    const schedule = () => {
      timeoutId = window.setTimeout(async () => {
        attempts += 1;
        try {
          const next = await refreshCampaign();
          if (cancelled || !ACTIVE_CAMPAIGN_STATUSES.has(next?.status)) return;
          if (attempts >= MAX_POLL_ATTEMPTS) {
            setPollMessage(`${activeLabel} is taking longer than expected. Use Refresh status to check again.`);
            return;
          }
          setPollMessage(`${activeLabel} is in progress. This page will update automatically.`);
          schedule();
        } catch (err) {
          if (cancelled) return;
          if (attempts >= MAX_POLL_ATTEMPTS) {
            setPollMessage(`Could not confirm the latest campaign status. Use Refresh status to try again.`);
            return;
          }
          setPollMessage('Could not refresh campaign status. Retrying automatically…');
          schedule();
        }
      }, POLL_INTERVAL_MS);
    };

    setPollMessage(`${activeLabel} is in progress. This page will update automatically.`);
    schedule();
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [campaign?.status, refreshCampaign]);

  const clearFeedback = () => {
    setActionError('');
    setNotice('');
  };

  const manualRefresh = async () => {
    clearFeedback();
    setBusyAction('refresh');
    try {
      await refreshCampaign();
      setNotice('Campaign status refreshed.');
    } catch (err) {
      setActionError(apiErrorMessage(err, 'Could not refresh the campaign.'));
    } finally {
      setBusyAction('');
    }
  };

  if (loadError && !campaign) {
    return (
      <div>
        <button type="button" className="src-link" onClick={onBack}>← Back to campaigns</button>
        <div className="src-form-error" role="alert" style={{ marginTop: 12 }}>
          {loadError}{' '}
          <button
            type="button"
            className="src-link"
            onClick={() => {
              setLoadError('');
              refreshCampaign({ syncBrief: true }).catch((err) => {
                setLoadError(apiErrorMessage(err, 'Could not load the campaign.'));
              });
            }}
          >
            Try again
          </button>
        </div>
      </div>
    );
  }
  if (!campaign) return <div className="src-muted">Loading campaign…</div>;

  const messages = campaign.messages || [];
  const drafts = messages.filter((m) => m.status === 'draft');
  const approved = messages.filter((m) => m.status === 'approved');
  const pending = messages.filter((m) => m.status === 'pending');
  const isProcessing = ACTIVE_CAMPAIGN_STATUSES.has(campaign.status);
  const canEditCampaign = EDITABLE_CAMPAIGN_STATUSES.has(campaign.status);
  const actionsDisabled = Boolean(busyAction) || !canEditCampaign;

  const saveBrief = async () => {
    if (actionsDisabled) return;
    clearFeedback();
    setBusyAction('brief');
    try {
      await outreachApi.patchCampaign(campaignId, { brief });
      await refreshCampaign();
      setNotice('Brief saved.');
    } catch (err) {
      setActionError(apiErrorMessage(err, 'Could not save the campaign brief.'));
    } finally {
      setBusyAction('');
    }
  };

  const openGenerate = async () => {
    if (actionsDisabled || pending.length === 0) return;
    clearFeedback();
    setBusyAction('estimate');
    try {
      const res = await outreachApi.generate(campaignId, false);
      setGenEst(res.data);
      setGenConfirm(true);
    } catch (err) {
      setActionError(apiErrorMessage(err, 'Could not estimate draft cost.'));
    } finally {
      setBusyAction('');
    }
  };

  const runGenerate = async () => {
    setGenConfirm(false);
    if (!canEditCampaign || busyAction) return;
    clearFeedback();
    setBusyAction('generate');
    try {
      const res = await outreachApi.generate(campaignId, true);
      setCampaign((current) => ({ ...current, status: res.data?.status || 'generating' }));
      setNotice('Draft generation started.');
    } catch (err) {
      setActionError(apiErrorMessage(err, 'Could not start draft generation.'));
    } finally {
      setBusyAction('');
    }
  };

  const openSend = async () => {
    if (actionsDisabled || approved.length === 0) return;
    clearFeedback();
    setBusyAction('prepare-send');
    try {
      const res = await outreachApi.send(campaignId, false);
      setSendMeta(res.data);
      setSendConfirm(true);
    } catch (err) {
      setActionError(apiErrorMessage(err, 'Could not prepare the send.'));
    } finally {
      setBusyAction('');
    }
  };

  const runSend = async () => {
    setSendConfirm(false);
    if (!canEditCampaign || busyAction) return;
    clearFeedback();
    setBusyAction('send');
    try {
      const res = await outreachApi.send(campaignId, true);
      setCampaign((current) => ({ ...current, status: res.data?.status || 'sending' }));
      setNotice('Send started.');
    } catch (err) {
      setActionError(apiErrorMessage(err, 'Could not start the send.'));
    } finally {
      setBusyAction('');
    }
  };

  // One campaign-level HITL: approve every remaining draft and send the batch.
  // The recruiter's per-message edit/reject still runs first (rejected drafts
  // are excluded); this replaces the separate approve-all + send confirm steps.
  const openApproveAndSend = async () => {
    if (actionsDisabled || drafts.length + approved.length === 0) return;
    clearFeedback();
    setBusyAction('prepare-batch');
    try {
      const res = await outreachApi.approveAndSend(campaignId, false);
      setBatchMeta(res.data);
      setBatchConfirm(true);
    } catch (err) {
      setActionError(apiErrorMessage(err, 'Could not prepare the send.'));
    } finally {
      setBusyAction('');
    }
  };

  const runApproveAndSend = async () => {
    setBatchConfirm(false);
    if (!canEditCampaign || busyAction) return;
    clearFeedback();
    setBusyAction('batch-send');
    try {
      const res = await outreachApi.approveAndSend(campaignId, true);
      setCampaign((current) => ({ ...current, status: res.data?.status || 'sending' }));
      setNotice('Approved and sending the campaign.');
    } catch (err) {
      setActionError(apiErrorMessage(err, 'Could not approve and send the campaign.'));
    } finally {
      setBusyAction('');
    }
  };

  const approveAll = async () => {
    if (actionsDisabled || drafts.length === 0) return;
    clearFeedback();
    setBusyAction('approve');
    try {
      const res = await outreachApi.approve(campaignId, { all_drafts: true });
      await refreshCampaign();
      setNotice(`Approved ${res.data?.approved ?? drafts.length} message${drafts.length === 1 ? '' : 's'}.`);
    } catch (err) {
      setActionError(apiErrorMessage(err, 'Could not approve the drafts.'));
    } finally {
      setBusyAction('');
    }
  };

  const runArchive = async () => {
    setArchiveConfirm(false);
    if (campaign.status === 'archived' || isProcessing || busyAction) return;
    clearFeedback();
    setBusyAction('archive');
    try {
      await outreachApi.archiveCampaign(campaignId);
      await refreshCampaign();
      setNotice('Campaign archived.');
    } catch (err) {
      setActionError(apiErrorMessage(err, 'Could not archive the campaign.'));
    } finally {
      setBusyAction('');
    }
  };

  const refreshAfterMessageChange = () => refreshCampaign().catch((err) => {
    setActionError(apiErrorMessage(err, 'The message changed, but the campaign could not be refreshed.'));
  });

  return (
    <div>
      <button type="button" className="src-link" onClick={onBack}>← Back to campaigns</button>
      <div className="src-head" style={{ marginTop: 12 }}>
        <div>
          <h2 className="src-title">{campaign.name}</h2>
          <p className="src-sub">
            <StatusChip status={campaign.status} />
          </p>
        </div>
        <div className="src-actions">
          <button type="button" className="src-btn src-btn-ghost" onClick={manualRefresh} disabled={Boolean(busyAction)}>
            {busyAction === 'refresh' ? 'Refreshing…' : 'Refresh status'}
          </button>
          {campaign.status !== 'archived' ? (
            <button
              type="button"
              className="src-btn src-btn-ghost"
              onClick={() => setArchiveConfirm(true)}
              disabled={Boolean(busyAction) || isProcessing}
            >
              {busyAction === 'archive' ? 'Archiving…' : 'Archive'}
            </button>
          ) : null}
        </div>
      </div>

      {actionError ? <div className="src-form-error" role="alert">{actionError}</div> : null}
      {notice ? <div className="src-notice src-notice-success" role="status">{notice}</div> : null}
      {pollMessage ? <div className="src-notice" role="status">{pollMessage}</div> : null}

      <div className="src-form">
        <label className="src-sub" htmlFor="cmp-brief">Brief (pitch context for the drafter)</label>
        <textarea
          id="cmp-brief"
          className="src-input"
          rows={4}
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
          disabled={!canEditCampaign}
        />
        <div className="src-form-actions">
          <button type="button" className="src-btn src-btn-ghost" onClick={saveBrief} disabled={actionsDisabled}>
            {busyAction === 'brief' ? 'Saving…' : 'Save brief'}
          </button>
        </div>
      </div>

      <AudienceAdder
        campaignId={campaignId}
        disabled={!canEditCampaign || Boolean(busyAction)}
        onAdded={(res) => {
          setSkipped(res.skipped || []);
          refreshCampaign().catch((err) => {
            setActionError(apiErrorMessage(err, 'Audience added, but the campaign could not be refreshed.'));
          });
        }}
      />
      {skipped && skipped.length ? (
        <div className="src-import" data-testid="skipped-summary">
          <strong>Skipped {skipped.length}</strong>{' — '}
          {skipped.map((s, i) => `${s.email || s.id} (${s.reason})`).join(', ')}
        </div>
      ) : null}

      <div className="src-actions" style={{ margin: '16px 0' }}>
        <button type="button" className="src-btn" onClick={openGenerate} disabled={actionsDisabled || pending.length === 0}>
          {busyAction === 'estimate' ? 'Estimating…' : `Generate drafts (${pending.length})`}
        </button>
        {drafts.length + approved.length > 0 ? (
          <button
            type="button"
            className="src-btn"
            onClick={openApproveAndSend}
            disabled={actionsDisabled}
            data-testid="approve-send-all"
          >
            {busyAction === 'prepare-batch'
              ? 'Preparing…'
              : `Approve & send all (${drafts.length + approved.length})`}
          </button>
        ) : null}
        {drafts.length > 0 ? (
          <button type="button" className="src-btn src-btn-ghost" onClick={approveAll} disabled={actionsDisabled}>
            {busyAction === 'approve' ? 'Approving…' : `Approve all (${drafts.length})`}
          </button>
        ) : null}
        <button type="button" className="src-btn src-btn-ghost" onClick={openSend} disabled={actionsDisabled || approved.length === 0}>
          {busyAction === 'prepare-send' ? 'Preparing…' : `Send approved (${approved.length})`}
        </button>
      </div>

      <MessageList
        campaignId={campaignId}
        messages={messages}
        onChange={refreshAfterMessageChange}
        disabled={!canEditCampaign || Boolean(busyAction)}
      />

      <ConfirmDialog
        open={genConfirm}
        title={`Generate ${genEst?.count ?? 0} draft${genEst?.count === 1 ? '' : 's'}?`}
        detail={`Claude writes one message per recipient. Estimated cost: ~$${(
          genEst?.estimated_cost_usd ?? ((genEst?.count ?? 0) * COST_PER_DRAFT_USD)
        ).toFixed(2)}.`}
        confirmLabel="Generate"
        onConfirm={runGenerate}
        onCancel={() => setGenConfirm(false)}
      />
      <ConfirmDialog
        open={sendConfirm}
        title={`Send ${sendMeta?.approved_count ?? 0} approved message${sendMeta?.approved_count === 1 ? '' : 's'}?`}
        detail={`${sendMeta?.approved_count ?? 0} approved messages will be sent, each with your email as reply-to and a one-click unsubscribe.`}
        confirmLabel="Send"
        onConfirm={runSend}
        onCancel={() => setSendConfirm(false)}
      />
      <ConfirmDialog
        open={batchConfirm}
        title={`Send ${batchMeta?.will_send ?? 0} message${batchMeta?.will_send === 1 ? '' : 's'} to ${batchMeta?.will_send ?? 0} prospect${batchMeta?.will_send === 1 ? '' : 's'}?`}
        detail={[
          `Each goes out with your email as reply-to and a one-click unsubscribe.`,
          batchMeta?.suppressed_excluded
            ? `${batchMeta.suppressed_excluded} suppressed excluded.`
            : '',
          batchMeta?.rejected_excluded
            ? `${batchMeta.rejected_excluded} rejected excluded.`
            : '',
        ].filter(Boolean).join(' ')}
        confirmLabel="Approve & send all"
        onConfirm={runApproveAndSend}
        onCancel={() => setBatchConfirm(false)}
      />
      <ConfirmDialog
        open={archiveConfirm}
        title="Archive this campaign?"
        detail="The campaign and its results will remain available for reporting, but its audience and messages can no longer be changed."
        confirmLabel="Archive"
        destructive
        onConfirm={runArchive}
        onCancel={() => setArchiveConfirm(false)}
      />
    </div>
  );
}

function AudienceAdder({ campaignId, onAdded, disabled = false }) {
  const [rows, setRows] = useState([]);
  const [selected, setSelected] = useState({});
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setDebouncedQuery(query.trim());
      setPage(0);
    }, 250);
    return () => window.clearTimeout(timeoutId);
  }, [query]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true);
    setError('');
    const params = {
      status: 'new',
      limit: AUDIENCE_PAGE_SIZE,
      offset: page * AUDIENCE_PAGE_SIZE,
    };
    if (debouncedQuery) params.q = debouncedQuery;
    prospectsApi
      .list(params)
      .then((res) => {
        if (!active) return;
        setRows(res.data?.prospects || []);
        setTotal(Number(res.data?.total || 0));
      })
      .catch((loadErr) => {
        if (!active) return;
        setRows([]);
        setError(apiErrorMessage(loadErr, 'Could not load prospects.'));
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [debouncedQuery, open, page]);

  const add = async () => {
    if (disabled || adding) return;
    const ids = Object.keys(selected).filter((k) => selected[k]).map(Number);
    if (!ids.length) return;
    setAdding(true);
    setError('');
    try {
      const res = await outreachApi.addAudience(campaignId, { prospect_ids: ids });
      onAdded(res.data);
      setSelected({});
      setQuery('');
      setDebouncedQuery('');
      setPage(0);
      setOpen(false);
    } catch (addErr) {
      setError(apiErrorMessage(addErr, 'Could not add the selected prospects.'));
    } finally {
      setAdding(false);
    }
  };

  const selectedCount = Object.values(selected).filter(Boolean).length;
  const pageCount = Math.max(1, Math.ceil(total / AUDIENCE_PAGE_SIZE));

  if (!open) {
    return (
      <button type="button" className="src-btn src-btn-ghost" onClick={() => setOpen(true)} disabled={disabled}>
        Add from prospects
      </button>
    );
  }

  return (
    <div className="src-form" data-testid="audience-adder">
      <label className="src-field">
        <span className="src-field-label">Search available prospects</span>
        <input
          className="src-input"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Name, email, or role"
          disabled={loading || adding}
        />
      </label>
      {error ? <div className="src-form-error" role="alert">{error}</div> : null}
      {loading ? (
        <div className="src-muted">Loading prospects…</div>
      ) : rows.length === 0 && !error && !debouncedQuery ? (
        <div className="src-muted">No prospects to add.</div>
      ) : rows.length === 0 ? (
        <div className="src-muted">No prospects match that search.</div>
      ) : (
        <table className="src-table">
          <tbody>
            {rows.map((p) => (
              <tr key={p.id}>
                <td data-label="Selection">
                  <input
                    type="checkbox"
                    disabled={!!p.suppressed || adding || disabled}
                    checked={!!selected[p.id]}
                    onChange={(e) => setSelected((current) => ({ ...current, [p.id]: e.target.checked }))}
                    aria-label={`Select ${p.full_name}`}
                  />
                </td>
                <td data-label="Name">{p.full_name}</td>
                <td data-label="Email">
                  {p.email}
                  {p.suppressed ? <span className="src-badge">{p.suppressed}</span> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {total > AUDIENCE_PAGE_SIZE ? (
        <nav className="src-pagination" aria-label="Audience prospect pages">
          <button
            type="button"
            className="src-page-btn"
            disabled={page === 0 || loading || adding}
            onClick={() => setPage((value) => Math.max(0, value - 1))}
          >
            Previous
          </button>
          <span className="src-page-info">Page {page + 1} of {pageCount}</span>
          <button
            type="button"
            className="src-page-btn"
            disabled={page + 1 >= pageCount || loading || adding}
            onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}
          >
            Next
          </button>
        </nav>
      ) : null}
      <div className="src-form-actions">
        <button type="button" className="src-btn" onClick={add} disabled={!selectedCount || adding || disabled}>
          {adding ? 'Adding…' : `Add selected${selectedCount ? ` (${selectedCount})` : ''}`}
        </button>
        <button
          type="button"
          className="src-btn src-btn-ghost"
          onClick={() => {
            setOpen(false);
            setError('');
          }}
          disabled={adding}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function MessageList({ campaignId, messages, onChange, disabled = false }) {
  const [query, setQuery] = useState('');
  if (!messages.length) return null;

  const normalizedQuery = query.trim().toLowerCase();
  const visibleMessages = normalizedQuery
    ? messages.filter((message) => [
      message.recipient_name,
      message.email,
      message.subject,
      message.status,
    ].some((value) => String(value || '').toLowerCase().includes(normalizedQuery)))
    : messages;

  return (
    <div data-testid="message-list">
      {messages.length > 8 ? (
        <label className="src-field" style={{ marginBottom: 12 }}>
          <span className="src-field-label">Search campaign messages</span>
          <input
            className="src-input"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Recipient, subject, or status"
          />
        </label>
      ) : null}
      {visibleMessages.length === 0 ? <div className="src-muted">No messages match that search.</div> : null}
      {visibleMessages.map((m) => (
        <MessageRow
          key={m.id}
          campaignId={campaignId}
          message={m}
          onChange={onChange}
          disabled={disabled}
        />
      ))}
    </div>
  );
}

function MessageRow({ campaignId, message, onChange, disabled = false }) {
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState(message.subject || '');
  const [body, setBody] = useState(message.body || '');
  const [busyAction, setBusyAction] = useState('');
  const [error, setError] = useState('');

  const canEdit = message.status === 'draft' || message.status === 'approved';

  useEffect(() => {
    if (editing) return;
    setSubject(message.subject || '');
    setBody(message.body || '');
  }, [editing, message.body, message.subject]);

  const save = async () => {
    if (disabled || busyAction) return;
    setBusyAction('save');
    setError('');
    try {
      await outreachApi.editMessage(campaignId, message.id, { subject, body });
      setEditing(false);
      await onChange?.();
    } catch (saveErr) {
      setError(apiErrorMessage(saveErr, 'Could not save this message.'));
    } finally {
      setBusyAction('');
    }
  };
  const approve = async () => {
    if (disabled || busyAction) return;
    setBusyAction('approve');
    setError('');
    try {
      await outreachApi.approve(campaignId, { message_ids: [message.id] });
      await onChange?.();
    } catch (approveErr) {
      setError(apiErrorMessage(approveErr, 'Could not approve this message.'));
    } finally {
      setBusyAction('');
    }
  };
  const reject = async () => {
    if (disabled || busyAction) return;
    setBusyAction('reject');
    setError('');
    try {
      await outreachApi.reject(campaignId, message.id);
      await onChange?.();
    } catch (rejectErr) {
      setError(apiErrorMessage(rejectErr, 'Could not reject this message.'));
    } finally {
      setBusyAction('');
    }
  };

  return (
    <div className="src-form" data-testid={`message-${message.id}`}>
      <div className="src-head">
        <div>
          <strong>{message.recipient_name || message.email}</strong>
          <span className="src-sub"> · {message.email}</span>
        </div>
        <StatusChip status={message.status} />
      </div>
      {editing ? (
        <>
          <div className="src-form-grid">
            <label className="src-field src-field-wide">
              <span className="src-field-label">Subject</span>
              <input
                className="src-input"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
              />
            </label>
            <label className="src-field src-field-wide">
              <span className="src-field-label">Message body</span>
              <textarea
                className="src-input"
                rows={5}
                value={body}
                onChange={(e) => setBody(e.target.value)}
              />
            </label>
          </div>
          <div className="src-form-actions">
            <button type="button" className="src-btn" onClick={save} disabled={disabled || Boolean(busyAction)}>
              {busyAction === 'save' ? 'Saving…' : 'Save'}
            </button>
            <button
              type="button"
              className="src-btn src-btn-ghost"
              onClick={() => {
                setSubject(message.subject || '');
                setBody(message.body || '');
                setError('');
                setEditing(false);
              }}
              disabled={Boolean(busyAction)}
            >
              Cancel
            </button>
          </div>
        </>
      ) : (
        <>
          {message.subject ? <div><strong>{message.subject}</strong></div> : null}
          {message.body ? <div className="src-sub" style={{ whiteSpace: 'pre-wrap' }}>{message.body}</div> : null}
          {message.error ? <div className="src-form-error">{message.error}</div> : null}
          {canEdit ? (
            <div className="src-form-actions">
              <button type="button" className="src-link" onClick={() => setEditing(true)} disabled={disabled || Boolean(busyAction)}>Edit</button>
              {message.status === 'draft' ? (
                <button type="button" className="src-link" onClick={approve} disabled={disabled || Boolean(busyAction)}>
                  {busyAction === 'approve' ? 'Approving…' : 'Approve'}
                </button>
              ) : null}
              <button type="button" className="src-link" onClick={reject} disabled={disabled || Boolean(busyAction)}>
                {busyAction === 'reject' ? 'Rejecting…' : 'Reject'}
              </button>
            </div>
          ) : null}
        </>
      )}
      {error ? <div className="src-form-error" role="alert">{error}</div> : null}
    </div>
  );
}
