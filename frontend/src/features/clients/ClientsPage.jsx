// Clients — the consultancy's client directory.
//
// In a consultancy, requisitions belong to CLIENTS. This page lists every
// client with a prominent open-jobs count and a lightweight "new client" form
// (name + optional contact). Clicking a client opens its detail page
// (/clients/:id) — the economics roll-up + assigned requisitions. It composes
// the global purple design tokens and the existing list conventions; it does
// NOT own any chat surface.
import React, { useCallback, useEffect, useState } from 'react';
import { Building2, ChevronRight, Mail, Plus, User } from 'lucide-react';

import { clientApi } from './api';
import './clients.css';

const statusLabel = (status) => String(status || 'active').replace(/_/g, ' ');

// One client row — leads to the client's detail page.
function ClientCard({ client, onOpen }) {
  const count = Number(client.open_job_count) || 0;
  const contactBits = [client.contact_name, client.contact_email].filter(Boolean);

  return (
    <li className="cl-card">
      <button
        type="button"
        className="cl-card-row"
        onClick={onOpen}
        aria-label={`Open ${client.name || 'client'}`}
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
        <ChevronRight size={18} className="cl-chevron" aria-hidden="true" />
      </button>
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

  const openClient = useCallback(
    (id) => onNavigate?.('client-detail', { clientId: id }),
    [onNavigate],
  );

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
                  onOpen={() => openClient(c.id)}
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
