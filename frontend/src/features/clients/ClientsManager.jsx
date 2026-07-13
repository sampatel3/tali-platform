// Hiring-department management — embedded directly in Settings → Hiring
// departments (no separate page). A "hiring department" is whoever a requisition
// is for: an external client (e.g. ADCB) or an internal team (e.g. Engineering).
// Lists them with an inline open/filled rollup + a lightweight "new department"
// form. The per-department pipeline lives on the Jobs page (filter by
// department); margins live on each requisition. NB the backend entity is still
// `Client` (model/routes/fields unchanged) — only the user-facing label changed.
// Composes the global purple tokens + the existing clients.css conventions.
import React, { useCallback, useEffect, useState } from 'react';
import { Building2, Mail, Plus, User } from 'lucide-react';

import { MotionSpinner } from '../../shared/motion';
import { clientApi } from './api';
import './clients.css';

const statusLabel = (status) => String(status || 'active').replace(/_/g, ' ');

function ClientRow({ client }) {
  const rollup = client.job_rollup || {};
  const active = Number(rollup.active || 0);
  const filled = Number(rollup.filled || 0);
  const external = Number(rollup.filled_external || 0);
  const hasContact = client.contact_name || client.contact_email;

  return (
    <li className="cl-card">
      <div className="cl-card-row is-static">
        <span className="cl-avatar" aria-hidden="true"><Building2 size={18} /></span>
        <span className="cl-card-main">
          <span className="cl-card-name">{client.name || 'Unnamed department'}</span>
          {hasContact ? (
            <span className="cl-card-contact">
              {client.contact_name ? (<><User size={12} aria-hidden="true" />{client.contact_name}</>) : null}
              {client.contact_name && client.contact_email ? <span className="cl-card-contact-sep">·</span> : null}
              {client.contact_email ? (<><Mail size={12} aria-hidden="true" />{client.contact_email}</>) : null}
            </span>
          ) : (
            <span className="cl-card-contact">No contact on file</span>
          )}
        </span>
        {client.status && String(client.status).toLowerCase() !== 'active' ? (
          <span className="cl-status">{statusLabel(client.status)}</span>
        ) : null}
        <span className="cl-rollup-mini" title="Open / waiting · filled by us · filled externally">
          <b>{active}</b> open · <b>{filled}</b> filled{external ? <> · <b>{external}</b> ext</> : null}
        </span>
      </div>
    </li>
  );
}

export function ClientsManager() {
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
      setError('Could not load hiring departments.');
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
      setError('Could not create that hiring department. Try again.');
    } finally {
      setCreating(false);
    }
  }, [name, contactName, contactEmail, creating, loadList]);

  return (
    <div className="cl-embed">
      {error ? <div className="cl-error">{error}</div> : null}

      <form className="cl-form" onSubmit={createClient}>
        <div className="cl-field">
          <label className="cl-field-label" htmlFor="cl-name">Hiring department name</label>
          <input id="cl-name" className="cl-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Engineering, or a client like ADCB" />
        </div>
        <div className="cl-field">
          <label className="cl-field-label" htmlFor="cl-contact-name">Contact name (optional)</label>
          <input id="cl-contact-name" className="cl-input" value={contactName} onChange={(e) => setContactName(e.target.value)} placeholder="e.g. Jane Doe" />
        </div>
        <div className="cl-field">
          <label className="cl-field-label" htmlFor="cl-contact-email">Contact email (optional)</label>
          <input id="cl-contact-email" className="cl-input" type="email" value={contactEmail} onChange={(e) => setContactEmail(e.target.value)} placeholder="jane@acme.com" />
        </div>
        <button type="submit" className="cl-new-btn" disabled={!name.trim() || creating}>
          {creating ? <MotionSpinner className="cl-motion-spinner" size={15} /> : <Plus size={15} />} New department
        </button>
      </form>

      {loading ? (
        <div className="cl-loading"><MotionSpinner className="cl-motion-spinner" size={15} /> Loading hiring departments…</div>
      ) : clients.length === 0 ? (
        <div className="cl-empty">
          <div className="cl-empty-glyph"><Building2 size={22} /></div>
          <h2>No hiring departments yet</h2>
          <p>Add your first one above — an external client like ADCB or an internal team like Engineering — then assign it to a requisition. Filter the Jobs page by department to see its pipeline.</p>
        </div>
      ) : (
        <ul className="cl-list">
          {clients.map((c) => <ClientRow key={c.id} client={c} />)}
        </ul>
      )}
    </div>
  );
}

export default ClientsManager;
