import React from 'react';

import { Select } from '../../shared/ui/TaaliPrimitives';
import {
  atsProviderLabel,
  effectiveNativeJobStatus,
  roleAtsProvider,
  roleAtsType,
  roleExternalJobLive,
  roleExternalJobState,
} from './atsType';

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
        From the job brief
      </div>
      <div className="req-meta">
        {r.ref_code ? <code className="req-ref">{r.ref_code}</code> : null}
        {r.client_name ? (
          <span className="req-client">
            Department · <strong>{r.client_name}</strong>
          </span>
        ) : null}
        {Number.isFinite(completeness) && completeness > 0 ? (
          <span className="req-complete">{completeness}% captured</span>
        ) : null}
      </div>
      {r.summary ? <p>{r.summary}</p> : null}
      <SpecList label="What you'll do" items={r.responsibilities} />
      {r.success_profile ? (
        <div className="req-group">
          <div className="req-subhead">What great looks like</div>
          <p>{r.success_profile}</p>
        </div>
      ) : null}
      <SpecList label="What matters most" items={priorities} />
      {/* The scoring requirements — must-haves, nice-to-haves, dealbreakers —
          live on the Agent settings tab now (they drive candidate scoring and
          are editable there), so they're not duplicated here. */}
      <p className="req-reqs-note">
        Must-haves, nice-to-haves &amp; dealbreakers live on the
        {' '}<strong>Agent settings</strong> tab — they drive scoring and can be edited there.
      </p>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Role lifecycle — Taali owns lifecycle writes only for Full ATS roles. Jobs
// synced from Workable or Bullhorn show the provider's authoritative state and
// tell the recruiter where to change it; related scoring roles defer lifecycle
// to their original role.
// --------------------------------------------------------------------------- //
const JOB_STATUS_LABEL = {
  draft: 'Draft',
  open: 'Open',
  filled: 'Filled',
  filled_external: 'Filled · external',
  cancelled: 'Archived',
};
const JOB_STATUS_TONE = {
  draft: 'draft',
  open: 'open',
  filled: 'filled',
  filled_external: 'ext',
  cancelled: 'cancelled',
};
const INACTIVE_JOB_STATUSES = new Set(['filled', 'filled_external', 'cancelled']);

export const roleLifecycleConfirmation = (nextStatus, currentStatus) => {
  const normalizedCurrent = String(currentStatus || 'open').trim().toLowerCase();
  if (nextStatus === 'cancelled') {
    return {
      title: 'Archive this role?',
      description: 'This role will move to Archived & inactive, and Taali will stop accepting and processing new applications. Candidate history will stay available. You can reopen the role later.',
      confirmLabel: 'Archive role',
      loadingLabel: 'Archiving…',
      variant: 'danger',
    };
  }
  if (nextStatus === 'filled') {
    return {
      title: 'Mark this role as filled?',
      description: 'This role will move to Archived & inactive, and Taali will stop accepting and processing new applications. Candidate history will stay available.',
      confirmLabel: 'Mark filled by us',
      loadingLabel: 'Updating…',
      variant: 'primary',
    };
  }
  if (nextStatus === 'filled_external') {
    return {
      title: 'Mark this role as filled externally?',
      description: 'This records that the role was filled outside your process. It will move to Archived & inactive, while candidate history stays available.',
      confirmLabel: 'Mark filled externally',
      loadingLabel: 'Updating…',
      variant: 'primary',
    };
  }
  const reopening = nextStatus === 'open' && INACTIVE_JOB_STATUSES.has(normalizedCurrent);
  return {
    title: reopening ? 'Reopen this role?' : 'Open this role?',
    description: reopening
      ? 'This role will return to the active Jobs list. Its current agent and native job-page settings will still apply.'
      : 'This role will move to the active Jobs list. Its current agent and native job-page settings will still apply.',
    confirmLabel: reopening ? 'Reopen role' : 'Open role',
    loadingLabel: reopening ? 'Reopening…' : 'Opening…',
    variant: 'primary',
  };
};

const formatExternalState = (state) => {
  const normalized = String(state || '').trim();
  if (!normalized) return 'Status pending sync';
  return normalized
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
};

const externalStateTone = (role, state) => {
  if (roleExternalJobLive(role) === true) return 'open';
  const normalized = String(state || '').trim().toLowerCase();
  if (normalized === 'draft') return 'draft';
  if (normalized === 'filled') return 'filled';
  return 'cancelled';
};

const nativeLifecycleActions = (status) => {
  if (INACTIVE_JOB_STATUSES.has(status)) {
    return [{ key: 'open', label: 'Reopen role' }];
  }
  return [
    ...(status === 'draft' ? [{ key: 'open', label: 'Open role' }] : []),
    { key: 'filled', label: 'Mark filled by us' },
    { key: 'filled_external', label: 'Mark filled externally' },
    { key: 'cancelled', label: 'Archive role' },
  ];
};

export function RoleLifecycleControl({
  role,
  onChange,
  busy,
  disabled = false,
  disabledReason = null,
}) {
  const atsType = roleAtsType(role);

  if (atsType === 'sister') {
    const ownerName = String(role?.ats_owner_role_name || '').trim();
    const ownerId = Number(role?.ats_owner_role_id || 0);
    const ownerLabel = ownerName
      ? `${ownerName}${ownerId ? ` #${ownerId}` : ''}`
      : (ownerId ? `role #${ownerId}` : 'the original role');
    return (
      <div className="job-status-control role-lifecycle-control is-read-only" role="group" aria-label="Role lifecycle">
        <div className="jsc-main">
          <div className="jsc-head">
            <span className="jsc-label">Role lifecycle</span>
            <span className="job-status-badge is-draft">Shared</span>
          </div>
          <p className="jsc-copy">
            <strong>Managed on the original role</strong>
            {' '}Archive or reopen {ownerLabel} to manage this shared candidate pool.
          </p>
        </div>
      </div>
    );
  }

  if (atsType === 'workable' || atsType === 'bullhorn') {
    const provider = roleAtsProvider(role);
    const providerLabel = atsProviderLabel(provider);
    const rawProviderState = roleExternalJobState(role);
    const providerState = formatExternalState(rawProviderState);
    const providerTone = externalStateTone(role, rawProviderState);
    return (
      <div className="job-status-control role-lifecycle-control is-read-only" role="group" aria-label="Role lifecycle">
        <div className="jsc-main">
          <div className="jsc-head">
            <span className="jsc-label">Role lifecycle</span>
            <span className={`job-status-badge is-${providerTone}`}>{providerState}</span>
          </div>
          <p className="jsc-copy">
            <strong>{`Managed in ${providerLabel}`}</strong>
            {` Archive or reopen this role in ${providerLabel}. Taali will reflect the change after the next sync.`}
          </p>
        </div>
      </div>
    );
  }

  const status = effectiveNativeJobStatus(role);
  const actions = nativeLifecycleActions(status);
  return (
    <div className="job-status-control role-lifecycle-control" role="group" aria-label="Role lifecycle">
      <div className="jsc-main">
        <div className="jsc-head">
          <span className="jsc-label">Role lifecycle</span>
          <span className={`job-status-badge is-${JOB_STATUS_TONE[status] || 'draft'}`}>
            {JOB_STATUS_LABEL[status]}
          </span>
        </div>
        <p className="jsc-copy">Managed in Taali for this Full ATS role.</p>
      </div>
      <div className="jsc-actions">
        {actions.map((action) => (
          <button
            key={action.key}
            type="button"
            disabled={disabled || busy}
            title={disabled ? disabledReason : undefined}
            className="jsc-btn"
            onClick={() => onChange(action.key)}
          >
            {action.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Hiring-department control — assign (or clear) the hiring department a role
// belongs to (an external client or an internal team). Unlike the lifecycle
// this shows for ANY role (not just requisition-origin ones): its whole point is
// letting recruiters tag legacy / Workable-imported jobs that never carried a
// department. The assignment rides on the role's brief (the backend stands up a
// stub when none exists) so the Jobs Department column / filter + per-department
// rollups pick the role up. NB the backend entity is still `client`.
// --------------------------------------------------------------------------- //
export function ClientControl({
  clientId,
  clientName,
  clients,
  onChange,
  busy,
  disabled = false,
  disabledReason = null,
}) {
  const options = Array.isArray(clients) ? clients : [];
  return (
    <div className="job-status-control client-control">
      <div className="jsc-head">
        <span className="jsc-label">Hiring department</span>
        <span className={`req-client-badge${clientName ? '' : ' is-empty'}`}>
          {clientName || 'No department'}
        </span>
      </div>
      <div className="jsc-actions">
        <Select
          inline
          value={clientId == null ? '' : String(clientId)}
          onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
          disabled={disabled || busy}
          title={disabled ? disabledReason : undefined}
          aria-label="Assign hiring department"
          placeholder="Assign a department…"
        >
          <option value="">— No department —</option>
          {options.map((c) => (
            <option key={c.id} value={String(c.id)}>{c.name}</option>
          ))}
        </Select>
      </div>
    </div>
  );
}

export default RequisitionSpecSections;
