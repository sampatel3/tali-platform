// Client detail — one consultancy client, its economics roll-up, and the
// requisitions assigned to it.
//
// Reached from the Clients directory (ClientsPage → onNavigate('client-detail',
// { clientId })) and mounted at /clients/:id. Shows a header (name + status +
// contact), an aggregates strip (open jobs, total requisitions, total margin,
// avg margin %), and the requisition list (title, status, completeness bar,
// margin, job-page indicator). Composes the GLOBAL purple design tokens and the
// existing `cl-` vocabulary; it does NOT own any chat surface.
import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { ArrowLeft, Building2, ExternalLink, Mail, User } from 'lucide-react';

import { clientApi } from './api';
import './clients.css';

const statusLabel = (status) => String(status || 'active').replace(/_/g, ' ');
const reqStatusLabel = (status) => String(status || 'draft').replace(/_/g, ' ');
const isPublished = (status) => String(status || '').toLowerCase() === 'published';

// Margins are AED ints (or null). Format with thousands separators, "—" when
// there's nothing to compute (no rate / no salary on the requisition).
const fmtMoney = (value) => {
  if (value == null || Number.isNaN(Number(value))) return '—';
  return `AED ${Math.round(Number(value)).toLocaleString('en-US')}`;
};
const fmtPct = (value) => {
  if (value == null || Number.isNaN(Number(value))) return '—';
  return `${Math.round(Number(value))}%`;
};
const clampPct = (value) => Math.max(0, Math.min(100, Number(value) || 0));

// One requisition row: title, status chip, completeness mini-bar, margin, and
// a job-page indicator. The whole row leads back to the requisitions workspace.
function RequisitionRow({ requisition, onOpen }) {
  const completeness = requisition.completeness != null ? clampPct(requisition.completeness) : null;
  const published = isPublished(requisition.job_page);

  return (
    <li className="cl-detail-req">
      <button type="button" className="cl-detail-req-row" onClick={onOpen}>
        <span className="cl-detail-req-main">
          <span className="cl-detail-req-title">{requisition.title || 'Untitled requisition'}</span>
          <span className={`cl-detail-req-status${isPublished(requisition.status) ? ' is-published' : ''}`}>
            {reqStatusLabel(requisition.status)}
          </span>
        </span>

        {completeness != null ? (
          <span className="cl-detail-bar" title={`${completeness}% complete`} aria-label={`${completeness}% complete`}>
            <span className="cl-detail-bar-track">
              <span className="cl-detail-bar-fill" style={{ width: `${completeness}%` }} />
            </span>
            <span className="cl-detail-bar-val">{completeness}%</span>
          </span>
        ) : (
          <span className="cl-detail-bar is-empty">—</span>
        )}

        <span className="cl-detail-req-margin">
          <span className="cl-detail-req-margin-amt">{fmtMoney(requisition.margin)}</span>
          <span className="cl-detail-req-margin-pct">{fmtPct(requisition.margin_pct)}</span>
        </span>

        <span className={`cl-detail-jobpage${published ? ' is-published' : ''}`}>
          {published ? (
            <>
              <ExternalLink size={12} aria-hidden="true" />
              Published
            </>
          ) : requisition.job_page ? (
            statusLabel(requisition.job_page)
          ) : (
            'Draft'
          )}
        </span>
      </button>
    </li>
  );
}

export const ClientDetailPage = ({ onNavigate, NavComponent = null }) => {
  const { id: routeId } = useParams();
  const [client, setClient] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    if (!routeId) {
      setError('missing');
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const data = await clientApi.get(routeId);
      setClient(data || null);
      setError('');
    } catch (err) {
      // 404 → the client was deleted or the id is bogus; anything else is a
      // generic load failure. Both render a recoverable empty/error state.
      setError(err?.response?.status === 404 ? 'missing' : 'failed');
      setClient(null);
    } finally {
      setLoading(false);
    }
  }, [routeId]);

  useEffect(() => { void load(); }, [load]);

  const goToClients = useCallback(() => onNavigate?.('clients'), [onNavigate]);
  // Deep-linking to a specific brief is optional — the requisitions workspace
  // is the canonical place to open one, so a click lands there.
  const openRequisition = useCallback(() => onNavigate?.('requisitions'), [onNavigate]);

  const nav = NavComponent ? <NavComponent currentPage="clients" onNavigate={onNavigate} /> : null;

  if (loading) {
    return (
      <>
        {nav}
        <div className="cl-root">
          <div className="cl-wrap">
            <div className="cl-loading"><span className="cl-spinner" /> Loading client…</div>
          </div>
        </div>
      </>
    );
  }

  if (error === 'missing' || (!client && error !== 'failed')) {
    return (
      <>
        {nav}
        <div className="cl-root">
          <div className="cl-wrap">
            <button type="button" className="cl-detail-back" onClick={goToClients}>
              <ArrowLeft size={15} aria-hidden="true" /> Clients
            </button>
            <div className="cl-empty">
              <div className="cl-empty-glyph"><Building2 size={22} /></div>
              <h2>Client not found</h2>
              <p>This client may have been removed, or the link is out of date.</p>
            </div>
          </div>
        </div>
      </>
    );
  }

  if (error === 'failed') {
    return (
      <>
        {nav}
        <div className="cl-root">
          <div className="cl-wrap">
            <button type="button" className="cl-detail-back" onClick={goToClients}>
              <ArrowLeft size={15} aria-hidden="true" /> Clients
            </button>
            <div className="cl-error">Could not load this client. Please try again.</div>
          </div>
        </div>
      </>
    );
  }

  const summary = client.summary || {};
  const requisitions = Array.isArray(client.requisitions) ? client.requisitions : [];
  // Job-lifecycle rollup (open/waiting · filled · external) of the client's
  // linked roles — the same shape the Jobs page client filter shows.
  const rollup = client.job_rollup || {};
  const activeJobs = Number(rollup.active || 0);
  const filledJobs = Number(rollup.filled || 0);
  const externalJobs = Number(rollup.filled_external || 0);
  const totalRequisitions = summary.total_requisitions != null ? summary.total_requisitions : requisitions.length;
  const showStatus = client.status && String(client.status).toLowerCase() !== 'active';

  return (
    <>
      {nav}
      <div className="cl-root">
        <div className="cl-wrap">
          <button type="button" className="cl-detail-back" onClick={goToClients}>
            <ArrowLeft size={15} aria-hidden="true" /> Clients
          </button>

          {/* ===== Header ===== */}
          <header className="cl-detail-head">
            <span className="cl-detail-avatar" aria-hidden="true"><Building2 size={22} /></span>
            <div className="cl-detail-head-main">
              <div className="cl-detail-head-title">
                <h1 className="cl-title">{client.name || 'Unnamed client'}</h1>
                {showStatus ? <span className="cl-status">{statusLabel(client.status)}</span> : null}
              </div>
              {client.contact_name || client.contact_email ? (
                <p className="cl-detail-contact">
                  {client.contact_name ? (
                    <span className="cl-detail-contact-bit">
                      <User size={13} aria-hidden="true" />
                      {client.contact_name}
                    </span>
                  ) : null}
                  {client.contact_email ? (
                    <a className="cl-detail-contact-bit cl-detail-contact-link" href={`mailto:${client.contact_email}`}>
                      <Mail size={13} aria-hidden="true" />
                      {client.contact_email}
                    </a>
                  ) : null}
                </p>
              ) : (
                <p className="cl-detail-contact cl-detail-contact-none">No contact on file</p>
              )}
            </div>
          </header>

          {/* ===== Aggregates strip ===== */}
          <div className="cl-detail-stats">
            <div className="cl-detail-stat">
              <span className="cl-detail-stat-n">{activeJobs}</span>
              <span className="cl-detail-stat-label">Open / waiting</span>
            </div>
            <div className="cl-detail-stat">
              <span className="cl-detail-stat-n">{filledJobs}</span>
              <span className="cl-detail-stat-label">Filled</span>
            </div>
            {externalJobs > 0 ? (
              <div className="cl-detail-stat">
                <span className="cl-detail-stat-n">{externalJobs}</span>
                <span className="cl-detail-stat-label">Filled externally</span>
              </div>
            ) : null}
            <div className="cl-detail-stat">
              <span className="cl-detail-stat-n">{totalRequisitions}</span>
              <span className="cl-detail-stat-label">Total requisitions</span>
            </div>
            <div className="cl-detail-stat">
              <span className="cl-detail-stat-n">{fmtMoney(summary.total_margin)}</span>
              <span className="cl-detail-stat-label">Total margin</span>
            </div>
            <div className="cl-detail-stat">
              <span className="cl-detail-stat-n">{fmtPct(summary.avg_margin_pct)}</span>
              <span className="cl-detail-stat-label">Avg margin</span>
            </div>
          </div>

          {/* ===== Requisitions ===== */}
          <section className="cl-detail-section">
            <p className="cl-detail-label">Requisitions</p>
            {requisitions.length === 0 ? (
              <div className="cl-empty cl-detail-empty-block">
                <div className="cl-empty-glyph"><Building2 size={22} /></div>
                <h2>No requisitions yet</h2>
                <p>Assign this client to a requisition from the requisition page to see it here.</p>
              </div>
            ) : (
              <ul className="cl-detail-req-list">
                {requisitions.map((r) => (
                  <RequisitionRow key={r.id} requisition={r} onOpen={openRequisition} />
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </>
  );
};

export default ClientDetailPage;
