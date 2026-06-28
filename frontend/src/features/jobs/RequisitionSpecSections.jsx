import React from 'react';

// The Job spec tab's "From the requisition" panel: the linked hiring brief's
// STRUCTURED spec (responsibilities, must/preferred/dealbreakers, success
// profile, weighted priorities, client) — richer than the raw job_spec_text the
// rest of the tab renders. Shown only when the role originated from / was linked
// to a requisition (role.requisition is set by the role-detail serializer).

const asText = (it) => {
  if (typeof it === 'string') return it;
  if (it && typeof it === 'object') return it.text || it.label || it.factor || it.name || '';
  return it == null ? '' : String(it);
};

const cleanList = (items) =>
  (Array.isArray(items) ? items.map(asText).map((s) => s.trim()).filter(Boolean) : []);

function SpecList({ label, items }) {
  const clean = cleanList(items);
  if (!clean.length) return null;
  return (
    <div className="req-group">
      <div className="req-subhead">{label}</div>
      <ul>
        {clean.map((t, i) => (
          <li key={`${t}-${i}`}>{t}</li>
        ))}
      </ul>
    </div>
  );
}

export function RequisitionSpecSections({ requisition }) {
  if (!requisition) return null;
  const r = requisition;
  // Priorities are [{factor, weight}] — render "factor — weight".
  const priorities = cleanList(
    (r.priorities || []).map((p) =>
      p && typeof p === 'object'
        ? [p.factor || p.label || p.text, p.weight].filter(Boolean).join(' — ')
        : p,
    ),
  );
  const completeness = Number(r.completeness);

  return (
    <div className="role-sec req-spec">
      <div className="role-sec-title">
        <span className="marker">RQ</span>
        From the requisition
      </div>
      <div className="req-meta">
        {r.ref_code ? <code className="req-ref">{r.ref_code}</code> : null}
        {r.client_name ? (
          <span className="req-client">
            Client · <strong>{r.client_name}</strong>
          </span>
        ) : null}
        {Number.isFinite(completeness) && completeness > 0 ? (
          <span className="req-complete">{completeness}% captured</span>
        ) : null}
      </div>
      {r.summary ? <p>{r.summary}</p> : null}
      <SpecList label="What you'll do" items={r.responsibilities} />
      <SpecList label="Must have" items={r.must_haves} />
      <SpecList label="Nice to have" items={r.preferred} />
      <SpecList label="Dealbreakers" items={r.dealbreakers} />
      {r.success_profile ? (
        <div className="req-group">
          <div className="req-subhead">What great looks like</div>
          <p>{r.success_profile}</p>
        </div>
      ) : null}
      <SpecList label="What matters most" items={priorities} />
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Job status control — mark the requisition->Workable job's lifecycle. Shown on
// the role's Job Spec tab when a job_status is set (requisition-origin roles).
// --------------------------------------------------------------------------- //
const JOB_STATUS_LABEL = {
  draft: 'Draft',
  open: 'Open',
  filled: 'Filled',
  filled_external: 'Filled · external',
  cancelled: 'Cancelled',
};
const JOB_STATUS_TONE = {
  draft: 'draft',
  open: 'open',
  filled: 'filled',
  filled_external: 'ext',
  cancelled: 'cancelled',
};
const JOB_STATUS_CHOICES = [
  { key: 'open', label: 'Open' },
  { key: 'filled', label: 'Filled (by us)' },
  { key: 'filled_external', label: 'Filled (external)' },
  { key: 'cancelled', label: 'Cancelled' },
];

export function JobStatusControl({ status, onChange, busy }) {
  if (!status) return null;
  return (
    <div className="job-status-control">
      <div className="jsc-head">
        <span className="jsc-label">Job status</span>
        <span className={`job-status-badge is-${JOB_STATUS_TONE[status] || 'draft'}`}>
          {JOB_STATUS_LABEL[status] || status}
        </span>
      </div>
      <div className="jsc-actions">
        {JOB_STATUS_CHOICES.map((c) => (
          <button
            key={c.key}
            type="button"
            disabled={busy || status === c.key}
            className={`jsc-btn ${status === c.key ? 'is-current' : ''}`}
            onClick={() => onChange(c.key)}
          >
            {c.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default RequisitionSpecSections;
