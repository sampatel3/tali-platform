// RequisitionEconomics — the INTERNAL economics strip on a requisition.
//
// In a consultancy a requisition belongs to a CLIENT and carries a client
// rate; the margin (client_rate − salary_max) is what the consultancy keeps.
// This is recruiter-internal — NOT candidate-facing — so it renders as a
// distinct purple-muted band, visually separate from the brief/job-spec.
//
// Everything routes through the parent's handlers, which use the EXISTING
// requisitionApi.update({ client_id }) / ({ client_rate }) and merge the
// serialized brief back (it now carries client_id/client_name/client_rate/
// margin/margin_pct), so the margin read-out re-derives after every save.
import React, { useEffect, useRef, useState } from 'react';
import { Building2, Check, Plus, TrendingUp, X } from 'lucide-react';

import { Select } from '../../shared/ui/TaaliPrimitives';

// Thousands separators, no decimals — figures are whole AED/yr amounts.
const fmtMoney = (n) => {
  const num = Number(n);
  if (!Number.isFinite(num)) return null;
  return num.toLocaleString('en-US', { maximumFractionDigits: 0 });
};

const fmtPct = (n) => {
  const num = Number(n);
  if (!Number.isFinite(num)) return null;
  return `${Math.round(num)}%`;
};

export function RequisitionEconomics({
  brief,
  clients = [],
  saving = false,
  onAssignClient,
  onSetClientRate,
  onCreateClient,
}) {
  const clientId = brief?.client_id ?? '';
  // Local rate draft so typing is smooth; committed on blur / Enter.
  const [rateDraft, setRateDraft] = useState(
    brief?.client_rate == null ? '' : String(brief.client_rate),
  );
  const [addingClient, setAddingClient] = useState(false);
  const [newClientName, setNewClientName] = useState('');
  const newClientRef = useRef(null);

  // Re-seed the rate field when the selected requisition changes.
  useEffect(() => {
    setRateDraft(brief?.client_rate == null ? '' : String(brief.client_rate));
  }, [brief?.id, brief?.client_rate]);

  useEffect(() => {
    if (addingClient) newClientRef.current?.focus();
  }, [addingClient]);

  const commitRate = () => {
    const trimmed = rateDraft.trim();
    const next = trimmed === '' ? null : Number(trimmed);
    if (next != null && Number.isNaN(next)) return; // ignore garbage
    const current = brief?.client_rate ?? null;
    if (next === current) return; // no-op — don't churn the backend
    onSetClientRate?.(next);
  };

  const submitNewClient = () => {
    const name = newClientName.trim();
    if (!name) return;
    onCreateClient?.(name);
    setNewClientName('');
    setAddingClient(false);
  };

  const hasMargin = brief?.margin != null && Number.isFinite(Number(brief.margin));
  const marginMoney = hasMargin ? fmtMoney(brief.margin) : null;
  const marginPct = brief?.margin_pct != null ? fmtPct(brief.margin_pct) : null;

  return (
    <div className="rq-econ" aria-label="Internal client economics">
      <span className="rq-econ-tag"><Building2 size={12} /> Internal</span>

      {/* Client selector + inline "+ New client" */}
      <div className="rq-econ-field">
        <label className="rq-econ-label" htmlFor="rq-econ-client">Client</label>
        {addingClient ? (
          <div className="rq-econ-newclient">
            <input
              ref={newClientRef}
              className="rq-econ-input"
              value={newClientName}
              placeholder="New client name"
              disabled={saving}
              onChange={(e) => setNewClientName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); submitNewClient(); }
                if (e.key === 'Escape') { setAddingClient(false); setNewClientName(''); }
              }}
            />
            <button
              type="button"
              className="rq-econ-iconbtn is-primary"
              aria-label="Create client"
              onClick={submitNewClient}
              disabled={saving || !newClientName.trim()}
            >
              <Check size={13} />
            </button>
            <button
              type="button"
              className="rq-econ-iconbtn"
              aria-label="Cancel"
              onClick={() => { setAddingClient(false); setNewClientName(''); }}
              disabled={saving}
            >
              <X size={13} />
            </button>
          </div>
        ) : (
          <div className="rq-econ-clientpick">
            <Select
              inline
              aria-label="Assign client"
              className="rq-econ-select"
              value={clientId === null || clientId === undefined ? '' : String(clientId)}
              disabled={saving}
              onChange={(e) => onAssignClient?.(e.target.value)}
            >
              <option value="">— Unassigned —</option>
              {clients.map((c) => (
                <option key={c.id} value={String(c.id)}>{c.name || 'Unnamed client'}</option>
              ))}
            </Select>
            <button
              type="button"
              className="rq-econ-newbtn"
              onClick={() => setAddingClient(true)}
              disabled={saving}
            >
              <Plus size={13} /> New client
            </button>
          </div>
        )}
      </div>

      {/* Client rate */}
      <div className="rq-econ-field">
        <label className="rq-econ-label" htmlFor="rq-econ-rate">Client rate (AED/yr)</label>
        <input
          id="rq-econ-rate"
          className="rq-econ-input rq-econ-rate"
          type="number"
          inputMode="numeric"
          min="0"
          step="1000"
          value={rateDraft}
          placeholder="—"
          disabled={saving}
          onChange={(e) => setRateDraft(e.target.value)}
          onBlur={commitRate}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.blur(); } }}
        />
      </div>

      {/* Margin read-out */}
      <div className="rq-econ-margin">
        <span className="rq-econ-label"><TrendingUp size={12} /> Est. margin</span>
        {hasMargin ? (
          <span className="rq-econ-margin-val">
            AED {marginMoney}
            {marginPct ? <span className="rq-econ-margin-pct"> ({marginPct})</span> : null}
          </span>
        ) : (
          <span className="rq-econ-margin-val is-empty" title="Set a client rate and the role's salary to see the margin">—</span>
        )}
      </div>

      {saving ? <span className="rq-spinner rq-econ-spin" aria-label="Saving" /> : null}
    </div>
  );
}

export default RequisitionEconomics;
