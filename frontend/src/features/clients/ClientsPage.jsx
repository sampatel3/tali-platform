// Clients — the consultancy's client directory.
//
// In a consultancy, requisitions belong to CLIENTS. This page lists every
// client with a prominent open-jobs count, a lightweight "new client" form
// (name + optional contact), and an expand-to-reveal panel showing each
// client's requisitions. It composes the global purple design tokens and the
// existing list conventions; it does NOT own any chat surface.
import React, { useCallback, useEffect, useState } from 'react';
import { Building2, ChevronRight, Mail, Plus, User } from 'lucide-react';

import { clientApi } from './api';
import './clients.css';

const statusLabel = (status) => String(status || 'active').replace(/_/g, ' ');
const reqStatusLabel = (status) => String(status || 'draft').replace(/_/g, ' ');
const isPublished = (status) => String(status || '').toLowerCase() === 'published';

// One client row + its (lazily fetched) requisitions panel.
function ClientCard({ client, expanded, onToggle, detail, detailLoading }) {
  const count = Number(client.open_job_count) || 0;
  const contactBits = [client.contact_name, client.contact_email].filter(Boolean);
  const requisitions = Array.isArray(detail?.requisitions) ? detail.requisitions : [];

  return (
    <li className={`cl-card${expanded ? ' is-open' : ''}`}>
      <button
        type="button"
        className="cl-card-row"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className="cl-avatar" aria-hidden="true"><Building2 size={18} /></span>
        <span className="cl-card-main">
          <span className="cl-card-name">{client.name || 'Unnamed client'}</span>
          {contactBits.length > 0 ? (
            <span className="cl-card-contact">
              {client.contact_name ? (
                <>
                  <User size={12} aria-hidden="true" />
                  {client.contact_name}
                </>
              ) : null}
              {client.contact_name && client.contact_email ? (
                <span className="cl-card-contact-sep">·</span>
              ) : null}
              {client.contact_email ? (
                <>
                  <Mail size={12} aria-hidden="true" />
                  {client.contact_email}
                </>
              ) : null}
            </span>
          ) : (
            <span className="cl-card-contact">No contact on file</span>
          )}
        </span>
        {client.status && String(client.status).toLowerCase() !== 'active' ? (
          <span className="cl-status">{statusLabel(client.status)}</span>
        ) : null}
        <span className={`cl-count${count === 0 ? ' is-zero' : ''}`}>
          <span className="cl-count-n">{count}</span>
          <span className="cl-count-label">{count === 1 ? 'open job' : 'open jobs'}</span>
        </span>
        <ChevronRight size={18} className={`cl-chevron${expanded ? ' is-open' : ''}`} aria-hidden="true" />
      </button>

      {expanded ? (
        <div className="cl-detail">
          <p className="cl-detail-label">Requisitions</p>
          {detailLoading ? (
            <div className="cl-detail-empty"><span className="cl-spinner" /> Loading…</div>
          ) : requisitions.length === 0 ? (
            <div className="cl-detail-empty">No requisitions assigned to this client yet.</div>
          ) : (
            <ul className="cl-req-list">
              {requisitions.map((r) => (
                <li key={r.id} className="cl-req">
                  <span className="cl-req-title">{r.title || 'Untitled requisition'}</span>
                  <span className="cl-req-meta">
                    {r.completeness != null ? <span>{r.completeness}%</span> : null}
                    <span className={`cl-dot${isPublished(r.status) ? ' is-published' : ''}`} />
                    {reqStatusLabel(r.status)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </li>
  );
}

export const ClientsPage = ({ onNavigate, NavComponent = null }) => {
  const [clients, setClients] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [contactName, setContactName] = useState('');
  const [contactEmail, setContactEmail] = useState('');

  // Expansion + per-client requisition detail (lazy, cached by id).
  const [expandedId, setExpandedId] = useState(null);
  const [details, setDetails] = useState({});
  const [detailLoadingId, setDetailLoadingId] = useState(null);

  const loadList = useCallback(async () => {
    try {
      const list = await clientApi.list();
      setClients(Array.isArray(list) ? list : []);
      setError('');
    } catch {
      setError('Could not load clients.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadList(); }, [loadList]);

  const createClient = useCallback(async (e) => {
    e?.preventDefault?.();
    const trimmed = name.trim();
    if (!trimmed || creating) return;
    setCreating(true);
    setError('');
    try {
      await clientApi.create({
        name: trimmed,
        contact_name: contactName.trim() || null,
        contact_email: contactEmail.trim() || null,
      });
      setName('');
      setContactName('');
      setContactEmail('');
      await loadList();
    } catch {
      setError('Could not create that client. Try again.');
    } finally {
      setCreating(false);
    }
  }, [name, contactName, contactEmail, creating, loadList]);

  // Expand a client → fetch its requisitions once (cached thereafter).
  const toggle = useCallback(async (id) => {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(id);
    if (details[id]) return; // already cached
    setDetailLoadingId(id);
    try {
      const full = await clientApi.get(id);
      setDetails((prev) => ({ ...prev, [id]: full }));
    } catch {
      // Non-fatal — the row stays expanded showing the empty/error state.
      setDetails((prev) => ({ ...prev, [id]: { requisitions: [] } }));
    } finally {
      setDetailLoadingId((cur) => (cur === id ? null : cur));
    }
  }, [expandedId, details]);

  return (
    <>
      {NavComponent ? <NavComponent currentPage="clients" onNavigate={onNavigate} /> : null}
      <div className="cl-root">
        <div className="cl-wrap">
          <header className="cl-head">
            <div className="cl-head-titles">
              <h1 className="cl-title">Clients</h1>
              <p className="cl-subtitle">
                The companies you recruit for. Assign a client to a requisition on its page —
                each client shows how many of its jobs are still open.
              </p>
            </div>
          </header>

          {error ? <div className="cl-error">{error}</div> : null}

          <form className="cl-form" onSubmit={createClient}>
            <div className="cl-field">
              <label className="cl-field-label" htmlFor="cl-name">Client name</label>
              <input
                id="cl-name"
                className="cl-input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Acme Corp"
              />
            </div>
            <div className="cl-field">
              <label className="cl-field-label" htmlFor="cl-contact-name">Contact name (optional)</label>
              <input
                id="cl-contact-name"
                className="cl-input"
                value={contactName}
                onChange={(e) => setContactName(e.target.value)}
                placeholder="e.g. Jane Doe"
              />
            </div>
            <div className="cl-field">
              <label className="cl-field-label" htmlFor="cl-contact-email">Contact email (optional)</label>
              <input
                id="cl-contact-email"
                className="cl-input"
                type="email"
                value={contactEmail}
                onChange={(e) => setContactEmail(e.target.value)}
                placeholder="jane@acme.com"
              />
            </div>
            <button type="submit" className="cl-new-btn" disabled={!name.trim() || creating}>
              {creating ? <span className="cl-spinner" /> : <Plus size={15} />} New client
            </button>
          </form>

          {loading ? (
            <div className="cl-loading"><span className="cl-spinner" /> Loading clients…</div>
          ) : clients.length === 0 ? (
            <div className="cl-empty">
              <div className="cl-empty-glyph"><Building2 size={22} /></div>
              <h2>No clients yet</h2>
              <p>Add your first client above, then assign it to a requisition from the requisition page.</p>
            </div>
          ) : (
            <ul className="cl-list">
              {clients.map((c) => (
                <ClientCard
                  key={c.id}
                  client={c}
                  expanded={expandedId === c.id}
                  onToggle={() => toggle(c.id)}
                  detail={details[c.id]}
                  detailLoading={detailLoadingId === c.id}
                />
              ))}
            </ul>
          )}
        </div>
      </div>
    </>
  );
};

export default ClientsPage;
