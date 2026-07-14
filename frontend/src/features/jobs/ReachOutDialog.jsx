import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Send, Sparkles } from 'lucide-react';

import { outreach as outreachApi } from '../../shared/api/outreachClient';
import { Dialog, Button, Spinner } from '../../shared/ui/TaaliPrimitives';
import './campaignFlow.css';

// Reach out to sourced candidates — a lean, in-context campaign wizard.
//
// Runs entirely from the role's Candidates table (sourced filter): the recruiter
// ticks sourced leads and hits "Reach out". This dialog creates a campaign for
// the role, sets its audience to the selected application ids, runs the metered
// two-phase draft (cost estimate → confirm → the agent drafts), then collapses
// approve-all + send into the SINGLE campaign-level HITL
// ("Send N messages to N candidates?"). Nothing goes out without that confirm —
// the backend enforces the approval gate absolutely.

const POLL_INTERVAL_MS = 2000;
const MAX_POLL_ATTEMPTS = 45; // ~90s ceiling for drafting

const SKIP_REASON_LABEL = {
  suppressed: 'unsubscribed / suppressed',
  open_application: 'already in an active pipeline',
  duplicate: 'already in this campaign',
  missing_email: 'no email on file',
  wrong_role: 'belongs to a different role',
};

function apiErrorMessage(err, fallback) {
  const detail = err?.response?.data?.detail;
  return typeof detail === 'string' && detail.trim() ? detail : fallback;
}

function summariseSkipped(skipped) {
  const counts = {};
  (skipped || []).forEach((s) => {
    const key = s?.reason || 'skipped';
    counts[key] = (counts[key] || 0) + 1;
  });
  return Object.entries(counts).map(([reason, n]) => ({
    reason,
    n,
    label: SKIP_REASON_LABEL[reason] || reason,
  }));
}

// phases: review → cost → drafting → confirm → sending → done
export function ReachOutDialog({ open, roleId, roleTitle, applications = [], onClose, onCompleted, onSent }) {
  const [phase, setPhase] = useState('review');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [campaignId, setCampaignId] = useState(null);
  const [audience, setAudience] = useState(null); // { added, skipped }
  const [costEstimate, setCostEstimate] = useState(null); // { count, estimated_cost_usd }
  const [sendEstimate, setSendEstimate] = useState(null); // approve-and-send estimate
  const pollRef = useRef({ cancelled: false });

  const applicationIds = applications.map((a) => a.id).filter((id) => Number.isFinite(id));
  const selectedCount = applicationIds.length;

  // Reset whenever the dialog re-opens with a fresh selection.
  useEffect(() => {
    if (!open) return undefined;
    setPhase('review');
    setBusy(false);
    setError('');
    setCampaignId(null);
    setAudience(null);
    setCostEstimate(null);
    setSendEstimate(null);
    const token = { cancelled: false };
    pollRef.current = token;
    return () => { token.cancelled = true; };
  }, [open]);

  // Step 1 → 2: create the campaign, set its audience, fetch the draft cost.
  const prepare = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      const name = `Sourced outreach · ${roleTitle || 'role'} · ${new Date().toLocaleDateString()}`;
      const created = await outreachApi.createCampaign({ name, role_id: roleId ?? null });
      const cid = created.data?.id;
      setCampaignId(cid);
      const aud = await outreachApi.addAudience(cid, { application_ids: applicationIds });
      setAudience(aud.data || { added: 0, skipped: [] });
      if ((aud.data?.added || 0) === 0) {
        setPhase('cost'); // renders the "no reachable candidates" state
        return;
      }
      const est = await outreachApi.generate(cid, false);
      setCostEstimate(est.data || null);
      setPhase('cost');
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not prepare the campaign.'));
    } finally {
      setBusy(false);
    }
  }, [roleId, roleTitle, applicationIds]);

  // Step 2 → 3 → 4: confirm drafting, poll until the agent finishes, then read
  // the send estimate for the single HITL.
  const draft = useCallback(async () => {
    if (!campaignId) return;
    setBusy(true);
    setError('');
    try {
      await outreachApi.generate(campaignId, true);
      setPhase('drafting');
      const token = pollRef.current;
      let attempts = 0;
      // Poll the campaign until it leaves the 'generating' state.
      // eslint-disable-next-line no-constant-condition
      while (true) {
        if (token.cancelled) return;
        attempts += 1;
        // eslint-disable-next-line no-await-in-loop
        await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
        if (token.cancelled) return;
        // eslint-disable-next-line no-await-in-loop
        const detail = await outreachApi.getCampaign(campaignId);
        const status = detail.data?.status;
        if (status !== 'generating') break;
        if (attempts >= MAX_POLL_ATTEMPTS) {
          setError('Drafting is taking longer than expected. Check back shortly under Sourced → Campaigns.');
          setBusy(false);
          return;
        }
      }
      if (token.cancelled) return;
      const est = await outreachApi.approveAndSend(campaignId, false);
      setSendEstimate(est.data || null);
      setPhase('confirm');
    } catch (err) {
      setError(apiErrorMessage(err, 'Drafting failed.'));
      setPhase('cost');
    } finally {
      setBusy(false);
    }
  }, [campaignId]);

  // Step 4 → 5 → 6: the ONE HITL. Approve every draft and send the batch.
  const send = useCallback(async () => {
    if (!campaignId) return;
    setBusy(true);
    setError('');
    try {
      await outreachApi.approveAndSend(campaignId, true);
      setPhase('done');
      onCompleted?.(campaignId);
    } catch (err) {
      setError(apiErrorMessage(err, 'Send failed.'));
    } finally {
      setBusy(false);
    }
  }, [campaignId, onCompleted]);

  const skippedSummary = summariseSkipped(audience?.skipped);
  const willSend = sendEstimate?.will_send ?? 0;

  let title = 'Reach out to sourced candidates';
  let body = null;
  let footer = null;

  if (phase === 'review') {
    title = `Reach out to ${selectedCount} sourced candidate${selectedCount === 1 ? '' : 's'}`;
    body = (
      <div className="rc-body">
        <p className="rc-lead">
          Taali will draft a personalised first-touch email to each selected lead, grounded in this
          role. You review the count and cost, then send in one step. Nothing goes out until you confirm.
        </p>
        <ul className="rc-recipients">
          {applications.slice(0, 8).map((a) => (
            <li key={a.id}>
              <span className="rc-recipient-name">{a.candidate_name || a.candidate_email || `Candidate #${a.candidate_id || a.id}`}</span>
              {a.candidate_email ? <span className="rc-recipient-email">{a.candidate_email}</span> : null}
            </li>
          ))}
          {applications.length > 8 ? (
            <li className="rc-recipient-more">+{applications.length - 8} more</li>
          ) : null}
        </ul>
        {error ? <div className="rc-error">{error}</div> : null}
      </div>
    );
    footer = (
      <div className="rc-footer">
        <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button type="button" variant="primary" onClick={prepare} disabled={busy || selectedCount === 0}>
          {busy ? <Spinner size={13} className="!text-current" /> : <Sparkles size={14} />}
          Prepare campaign
        </Button>
      </div>
    );
  } else if (phase === 'cost') {
    const added = audience?.added || 0;
    title = 'Draft outreach';
    body = (
      <div className="rc-body">
        <div className="rc-audience">
          <span className="rc-audience-added"><strong>{added}</strong> reachable</span>
          {skippedSummary.map((s) => (
            <span key={s.reason} className="rc-audience-skip">{s.n} {s.label}</span>
          ))}
        </div>
        {added === 0 ? (
          <p className="rc-lead">
            None of the selected candidates can be reached — they were all excluded (see above). Sourced
            leads already in an active pipeline, unsubscribed, or without an email cannot receive outreach.
          </p>
        ) : (
          <p className="rc-lead">
            Taali will draft <strong>{costEstimate?.count ?? added}</strong> personalised message
            {(costEstimate?.count ?? added) === 1 ? '' : 's'}
            {costEstimate?.estimated_cost_usd != null ? (
              <> — estimated cost <strong>${Number(costEstimate.estimated_cost_usd).toFixed(2)}</strong></>
            ) : null}. You review the send before anything leaves.
          </p>
        )}
        {error ? <div className="rc-error">{error}</div> : null}
      </div>
    );
    footer = (
      <div className="rc-footer">
        <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>Close</Button>
        {added > 0 ? (
          <Button type="button" variant="primary" onClick={draft} disabled={busy}>
            {busy ? <Spinner size={13} className="!text-current" /> : <Sparkles size={14} />}
            Draft messages
          </Button>
        ) : null}
      </div>
    );
  } else if (phase === 'drafting') {
    title = 'Drafting messages';
    body = (
      <div className="rc-body rc-center">
        <Spinner size={22} />
        <p className="rc-lead">Taali is drafting personalised messages. This can take a moment…</p>
        {error ? <div className="rc-error">{error}</div> : null}
      </div>
    );
    footer = (
      <div className="rc-footer">
        <Button type="button" variant="ghost" onClick={onClose}>Close — keep drafting</Button>
      </div>
    );
  } else if (phase === 'confirm') {
    title = 'Send outreach';
    body = (
      <div className="rc-body">
        <p className="rc-send-headline">
          Send <strong>{willSend}</strong> message{willSend === 1 ? '' : 's'} to <strong>{willSend}</strong> sourced
          candidate{willSend === 1 ? '' : 's'}?
        </p>
        <p className="rc-lead">
          This sends the drafted emails on your organisation's behalf now. This is the only confirmation.
        </p>
        <ul className="rc-exclusions">
          {sendEstimate?.suppressed_excluded ? (
            <li>{sendEstimate.suppressed_excluded} suppressed / unsubscribed — excluded</li>
          ) : null}
          {sendEstimate?.rejected_excluded ? (
            <li>{sendEstimate.rejected_excluded} you rejected — excluded</li>
          ) : null}
          {sendEstimate?.failed_excluded ? (
            <li>{sendEstimate.failed_excluded} failed to draft — excluded</li>
          ) : null}
        </ul>
        {error ? <div className="rc-error">{error}</div> : null}
      </div>
    );
    footer = (
      <div className="rc-footer">
        <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button type="button" variant="primary" onClick={send} disabled={busy || willSend === 0}>
          {busy ? <Spinner size={13} className="!text-current" /> : <Send size={14} />}
          Send {willSend} message{willSend === 1 ? '' : 's'}
        </Button>
      </div>
    );
  } else if (phase === 'done') {
    title = 'Outreach on its way';
    body = (
      <div className="rc-body rc-center">
        <div className="rc-done-check"><Send size={20} /></div>
        <p className="rc-lead">
          <strong>{willSend}</strong> message{willSend === 1 ? '' : 's'} queued to send. Track opens, clicks and
          replies under <strong>Sourced → Campaigns</strong>.
        </p>
      </div>
    );
    footer = (
      <div className="rc-footer">
        <Button type="button" variant="ghost" onClick={onClose}>Close</Button>
        <Button
          type="button"
          variant="primary"
          onClick={() => { onSent?.(campaignId); }}
        >
          View campaign performance
        </Button>
      </div>
    );
  }

  return (
    <Dialog open={open} onClose={onClose} title={title} footer={footer} panelClassName="rc-dialog">
      {body}
    </Dialog>
  );
}

export default ReachOutDialog;
