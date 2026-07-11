import React, { useCallback, useEffect, useState } from 'react';

import { outreach as outreachApi } from '../../shared/api/outreachClient';
import { prospects as prospectsApi } from '../../shared/api/prospectsClient';
import { roles as rolesApi } from '../../shared/api/rolesClient';
import ConfirmDialog from '../chat/ConfirmDialog';

// Per-message draft cost (USD) — mirrors the backend COST_PER_DRAFT_USD so the
// cost-confirm dialog shows the same estimate before the recruiter confirms.
const COST_PER_DRAFT_USD = 0.006;

function StatusChip({ status }) {
  return <span className={`cmp-chip cmp-chip-${status}`}>{status}</span>;
}

// Outreach campaigns tab. List → drill into a campaign → build audience,
// generate drafts (cost-confirm), review + approve/reject, send (confirm),
// watch results. Nothing sends without explicit per-message approval + a send
// confirm; the backend enforces the approval gate absolutely.
export default function CampaignsPanel({ initialCampaignId = null }) {
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedId, setSelectedId] = useState(initialCampaignId);

  const loadList = useCallback(() => {
    setLoading(true);
    outreachApi
      .listCampaigns()
      .then((res) => setCampaigns(res.data?.campaigns || []))
      .catch(() => setError('Could not load campaigns.'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadList();
  }, [loadList]);

  if (selectedId) {
    return (
      <CampaignDetail
        campaignId={selectedId}
        onBack={() => {
          setSelectedId(null);
          loadList();
        }}
      />
    );
  }

  return (
    <div>
      <NewCampaign onCreated={(id) => setSelectedId(id)} />
      {error ? <div className="src-form-error">{error}</div> : null}
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
                  <td>{c.name}</td>
                  <td><StatusChip status={c.status} /></td>
                  <td>{k.sent || 0}</td>
                  <td>{k.opened || 0}</td>
                  <td>{k.clicked || 0}</td>
                  <td>{k.interested || 0}</td>
                  <td>
                    <button type="button" className="src-link" onClick={() => setSelectedId(c.id)}>
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
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (!open) return;
    rolesApi
      .list()
      .then((res) => {
        const items = Array.isArray(res.data) ? res.data : res.data?.roles || [];
        setRoleOptions(items);
      })
      .catch(() => setRoleOptions([]));
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
      .catch(() => setErr('Could not create the campaign.'))
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
        <input
          className="src-input"
          placeholder="Campaign name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label="Campaign name"
        />
        <select
          className="src-input"
          value={roleId}
          onChange={(e) => setRoleId(e.target.value)}
          aria-label="Role"
        >
          <option value="">No role (general)</option>
          {roleOptions.map((r) => (
            <option key={r.id} value={r.id}>{r.name}</option>
          ))}
        </select>
      </div>
      {err ? <div className="src-form-error">{err}</div> : null}
      <div className="src-form-actions">
        <button type="button" className="src-btn" onClick={create} disabled={saving}>
          {saving ? 'Creating…' : 'Create campaign'}
        </button>
        <button type="button" className="src-btn src-btn-ghost" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function CampaignDetail({ campaignId, onBack }) {
  const [campaign, setCampaign] = useState(null);
  const [error, setError] = useState('');
  const [brief, setBrief] = useState('');
  const [genConfirm, setGenConfirm] = useState(false);
  const [genEst, setGenEst] = useState(null);
  const [sendConfirm, setSendConfirm] = useState(false);
  const [sendMeta, setSendMeta] = useState(null);
  const [skipped, setSkipped] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    outreachApi
      .getCampaign(campaignId)
      .then((res) => {
        setCampaign(res.data);
        setBrief(res.data.brief || '');
      })
      .catch(() => setError('Could not load the campaign.'));
  }, [campaignId]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) return <div className="src-form-error">{error}</div>;
  if (!campaign) return <div className="src-muted">Loading…</div>;

  const messages = campaign.messages || [];
  const drafts = messages.filter((m) => m.status === 'draft');
  const approved = messages.filter((m) => m.status === 'approved');
  const pending = messages.filter((m) => m.status === 'pending');

  const saveBrief = () => {
    outreachApi.patchCampaign(campaignId, { brief }).then(load).catch(() => {});
  };

  const openGenerate = () => {
    outreachApi
      .generate(campaignId, false)
      .then((res) => {
        setGenEst(res.data);
        setGenConfirm(true);
      })
      .catch(() => setError('Could not estimate draft cost.'));
  };

  const runGenerate = () => {
    setGenConfirm(false);
    setBusy(true);
    outreachApi
      .generate(campaignId, true)
      .then(load)
      .catch(() => setError('Could not start draft generation.'))
      .finally(() => setBusy(false));
  };

  const openSend = () => {
    outreachApi
      .send(campaignId, false)
      .then((res) => {
        setSendMeta(res.data);
        setSendConfirm(true);
      })
      .catch(() => setError('Could not prepare the send.'));
  };

  const runSend = () => {
    setSendConfirm(false);
    setBusy(true);
    outreachApi
      .send(campaignId, true)
      .then(load)
      .catch(() => setError('Could not start the send.'))
      .finally(() => setBusy(false));
  };

  const approveAll = () => {
    outreachApi.approve(campaignId, { all_drafts: true }).then(load).catch(() => {});
  };

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
      </div>

      <div className="src-form">
        <label className="src-sub" htmlFor="cmp-brief">Brief (pitch context for the drafter)</label>
        <textarea
          id="cmp-brief"
          className="src-input"
          rows={4}
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
        />
        <div className="src-form-actions">
          <button type="button" className="src-btn src-btn-ghost" onClick={saveBrief}>Save brief</button>
        </div>
      </div>

      <AudienceAdder
        campaignId={campaignId}
        onAdded={(res) => { setSkipped(res.skipped); load(); }}
      />
      {skipped && skipped.length ? (
        <div className="src-import" data-testid="skipped-summary">
          <strong>Skipped {skipped.length}</strong>{' — '}
          {skipped.map((s, i) => `${s.email || s.id} (${s.reason})`).join(', ')}
        </div>
      ) : null}

      <div className="src-actions" style={{ margin: '16px 0' }}>
        <button type="button" className="src-btn" onClick={openGenerate} disabled={busy || pending.length === 0}>
          Generate drafts ({pending.length})
        </button>
        {drafts.length > 0 ? (
          <button type="button" className="src-btn src-btn-ghost" onClick={approveAll}>
            Approve all ({drafts.length})
          </button>
        ) : null}
        <button type="button" className="src-btn" onClick={openSend} disabled={busy || approved.length === 0}>
          Send ({approved.length} approved)
        </button>
      </div>

      <MessageList campaignId={campaignId} messages={messages} onChange={load} />

      <ConfirmDialog
        open={genConfirm}
        title={`Generate ${genEst?.count ?? 0} draft${genEst?.count === 1 ? '' : 's'}?`}
        detail={`Claude writes one message per recipient. Estimated cost: ~$${(genEst?.estimated_cost_usd ?? 0).toFixed(2)}.`}
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
    </div>
  );
}

function AudienceAdder({ campaignId, onAdded }) {
  const [rows, setRows] = useState([]);
  const [selected, setSelected] = useState({});
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    prospectsApi
      .list({ status: 'new' })
      .then((res) => setRows(res.data?.prospects || []))
      .catch(() => setRows([]));
  }, [open]);

  const add = () => {
    const ids = Object.keys(selected).filter((k) => selected[k]).map(Number);
    if (!ids.length) return;
    outreachApi
      .addAudience(campaignId, { prospect_ids: ids })
      .then((res) => {
        onAdded(res.data);
        setSelected({});
        setOpen(false);
      })
      .catch(() => {});
  };

  if (!open) {
    return (
      <button type="button" className="src-btn src-btn-ghost" onClick={() => setOpen(true)}>
        Add from prospects
      </button>
    );
  }

  return (
    <div className="src-form" data-testid="audience-adder">
      {rows.length === 0 ? (
        <div className="src-muted">No prospects to add.</div>
      ) : (
        <table className="src-table">
          <tbody>
            {rows.map((p) => (
              <tr key={p.id}>
                <td>
                  <input
                    type="checkbox"
                    disabled={!!p.suppressed}
                    checked={!!selected[p.id]}
                    onChange={(e) => setSelected({ ...selected, [p.id]: e.target.checked })}
                    aria-label={`Select ${p.full_name}`}
                  />
                </td>
                <td>{p.full_name}</td>
                <td>
                  {p.email}
                  {p.suppressed ? <span className="src-badge">{p.suppressed}</span> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="src-form-actions">
        <button type="button" className="src-btn" onClick={add}>Add selected</button>
        <button type="button" className="src-btn src-btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </div>
  );
}

function MessageList({ campaignId, messages, onChange }) {
  if (!messages.length) return null;
  return (
    <div data-testid="message-list">
      {messages.map((m) => (
        <MessageRow key={m.id} campaignId={campaignId} message={m} onChange={onChange} />
      ))}
    </div>
  );
}

function MessageRow({ campaignId, message, onChange }) {
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState(message.subject || '');
  const [body, setBody] = useState(message.body || '');

  const canEdit = message.status === 'draft' || message.status === 'approved';

  const save = () => {
    outreachApi.editMessage(campaignId, message.id, { subject, body }).then(() => {
      setEditing(false);
      onChange();
    });
  };
  const approve = () => outreachApi.approve(campaignId, { message_ids: [message.id] }).then(onChange);
  const reject = () => outreachApi.reject(campaignId, message.id).then(onChange);

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
          <input
            className="src-input"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="Subject"
            aria-label="Subject"
          />
          <textarea
            className="src-input"
            rows={5}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            aria-label="Body"
            style={{ marginTop: 8 }}
          />
          <div className="src-form-actions">
            <button type="button" className="src-btn" onClick={save}>Save</button>
            <button type="button" className="src-btn src-btn-ghost" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </>
      ) : (
        <>
          {message.subject ? <div><strong>{message.subject}</strong></div> : null}
          {message.body ? <div className="src-sub" style={{ whiteSpace: 'pre-wrap' }}>{message.body}</div> : null}
          {message.error ? <div className="src-form-error">{message.error}</div> : null}
          {canEdit ? (
            <div className="src-form-actions">
              <button type="button" className="src-link" onClick={() => setEditing(true)}>Edit</button>
              {message.status === 'draft' ? (
                <button type="button" className="src-link" onClick={approve}>Approve</button>
              ) : null}
              <button type="button" className="src-link" onClick={reject}>Reject</button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
