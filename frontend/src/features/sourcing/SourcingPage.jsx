import React, { useCallback, useEffect, useRef, useState } from 'react';
import { prospects as prospectsApi } from '../../shared/api/prospectsClient';
import { roles as rolesApi } from '../../shared/api';
import { SourceCandidatesPanel } from '../jobs/SourceCandidatesPanel';
import CampaignsPanel from './CampaignsPanel';
import './SourcingPage.css';

// Read ?tab= and ?campaign= from the URL so the "Start outreach" CTA can
// deep-link straight into a campaign's detail view.
function readTabFromUrl() {
  if (typeof window === 'undefined') return { tab: 'prospects', campaignId: null };
  const params = new URLSearchParams(window.location.search || '');
  const campaignId = params.get('campaign');
  const tab = params.get('tab') === 'campaigns' || campaignId ? 'campaigns' : 'prospects';
  return { tab, campaignId: campaignId ? Number(campaignId) : null };
}

const STATUSES = ['new', 'contacted', 'interested', 'converted', 'archived'];

const EMPTY_FORM = {
  full_name: '',
  email: '',
  position: '',
  location: '',
  linkedin_url: '',
  phone: '',
  notes: '',
};

function formatDate(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch (e) {
    return '';
  }
}

// "Find candidates" tab — pick an open role, then reuse the role-scoped
// SourceCandidatesPanel (the same LinkedIn search-string generator + paste-a-
// profile outreach drafter that lives on the job page). Everything it produces
// is copy-paste text the recruiter runs by hand; nothing is sent or automated.
function FindCandidatesTab() {
  const [roles, setRoles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedRoleId, setSelectedRoleId] = useState('');

  useEffect(() => {
    let active = true;
    setLoading(true);
    rolesApi
      .list()
      .then((res) => {
        if (!active) return;
        setRoles(Array.isArray(res.data) ? res.data : []);
        setError('');
      })
      .catch(() => active && setError('Could not load your roles.'))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="src-find">
      <p className="src-find-intro">
        Pick an open role, then generate ready-to-paste search strings for
        LinkedIn and Google. These are just search text you run yourself —
        nothing is sent or automated. You can also paste a profile to draft a
        first outreach message.
      </p>

      {loading ? (
        <div className="src-muted">Loading your roles…</div>
      ) : error ? (
        <div className="src-form-error">{error}</div>
      ) : roles.length === 0 ? (
        <div className="src-muted">
          No open roles yet. Create a job first, then come back to generate
          search strings for it.
        </div>
      ) : (
        <>
          <label className="src-find-picker">
            <span className="src-find-label">Role</span>
            <select
              className="src-input"
              value={selectedRoleId}
              onChange={(e) => setSelectedRoleId(e.target.value)}
              aria-label="Pick a role"
            >
              <option value="">Choose a role…</option>
              {roles.map((r) => (
                <option key={r.id} value={String(r.id)}>
                  {r.name} (#{r.id})
                </option>
              ))}
            </select>
          </label>

          {selectedRoleId ? (
            <SourceCandidatesPanel
              key={selectedRoleId}
              roleId={Number(selectedRoleId)}
              defaultOpen
            />
          ) : (
            <div className="src-muted">Pick a role to generate search strings.</div>
          )}
        </>
      )}
    </div>
  );
}

// Outreach foundations — the sourced-prospect list. Recruiters add prospects
// (inline form or CSV import) and see each row's suppression state (a muted
// purple badge, never red) so they never queue an un-mailable address.
export default function SourcingPage({ onNavigate, NavComponent = null }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const [importResult, setImportResult] = useState(null);
  const fileRef = useRef(null);
  const initial = readTabFromUrl();
  const [tab, setTab] = useState(initial.tab);

  const load = useCallback(() => {
    setLoading(true);
    const params = {};
    if (q.trim()) params.q = q.trim();
    if (statusFilter) params.status = statusFilter;
    prospectsApi
      .list(params)
      .then((res) => {
        setRows(Array.isArray(res.data?.prospects) ? res.data.prospects : []);
        setError('');
      })
      .catch(() => setError('Could not load prospects.'))
      .finally(() => setLoading(false));
  }, [q, statusFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const handleAdd = (e) => {
    e.preventDefault();
    setFormError('');
    if (!form.full_name.trim() || !form.email.trim()) {
      setFormError('Name and email are required.');
      return;
    }
    setSaving(true);
    prospectsApi
      .create({
        full_name: form.full_name.trim(),
        email: form.email.trim(),
        position: form.position.trim() || null,
        location: form.location.trim() || null,
        linkedin_url: form.linkedin_url.trim() || null,
        phone: form.phone.trim() || null,
        notes: form.notes.trim() || null,
      })
      .then(() => {
        setForm(EMPTY_FORM);
        setShowForm(false);
        load();
      })
      .catch((err) => {
        setFormError(
          err?.response?.status === 409
            ? 'A prospect with this email already exists.'
            : 'Could not add prospect.',
        );
      })
      .finally(() => setSaving(false));
  };

  const handleImport = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportResult(null);
    prospectsApi
      .importCsv(file)
      .then((res) => {
        setImportResult(res.data);
        load();
      })
      .catch(() => setError('CSV import failed.'))
      .finally(() => {
        if (fileRef.current) fileRef.current.value = '';
      });
  };

  const handleArchive = (id) => {
    prospectsApi.archive(id).then(load).catch(() => setError('Could not archive prospect.'));
  };

  return (
    <div className="src-shell">
      {NavComponent ? <NavComponent currentPage="sourcing" onNavigate={onNavigate} /> : null}
      <div className="src-root">
        <header className="src-head">
          <div>
            <h1 className="src-title">Sourcing</h1>
            <p className="src-sub">
              Build your own shortlist — candidates you go out and find, rather
              than people who apply to you. Generate ready-to-paste LinkedIn
              searches from any open role, keep the people you find as prospects,
              and send AI-drafted outreach emails that you approve before
              anything goes out.
            </p>
          </div>
          {tab === 'prospects' ? (
            <div className="src-actions">
              <button type="button" className="src-btn" onClick={() => setShowForm((v) => !v)}>
                Add prospect
              </button>
              <button type="button" className="src-btn src-btn-ghost" onClick={() => fileRef.current?.click()}>
                Import CSV
              </button>
              <input
                ref={fileRef}
                type="file"
                accept=".csv,text/csv"
                onChange={handleImport}
                style={{ display: 'none' }}
                data-testid="csv-input"
              />
            </div>
          ) : null}
        </header>

        <div className="src-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'prospects'}
            className={`src-tab ${tab === 'prospects' ? 'src-tab-active' : ''}`}
            onClick={() => setTab('prospects')}
          >
            Prospects
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'find'}
            className={`src-tab ${tab === 'find' ? 'src-tab-active' : ''}`}
            onClick={() => setTab('find')}
          >
            Find candidates
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'campaigns'}
            className={`src-tab ${tab === 'campaigns' ? 'src-tab-active' : ''}`}
            onClick={() => setTab('campaigns')}
          >
            Campaigns
          </button>
        </div>

        {tab === 'campaigns' ? (
          <CampaignsPanel initialCampaignId={initial.campaignId} />
        ) : tab === 'find' ? (
          <FindCandidatesTab />
        ) : (
        <>

        {showForm ? (
          <form className="src-form" onSubmit={handleAdd}>
            <div className="src-form-grid">
              <input
                className="src-input"
                placeholder="Full name"
                value={form.full_name}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                aria-label="Full name"
              />
              <input
                className="src-input"
                placeholder="Email"
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                aria-label="Email"
              />
              <input
                className="src-input"
                placeholder="Position"
                value={form.position}
                onChange={(e) => setForm({ ...form, position: e.target.value })}
                aria-label="Position"
              />
              <input
                className="src-input"
                placeholder="Location"
                value={form.location}
                onChange={(e) => setForm({ ...form, location: e.target.value })}
                aria-label="Location"
              />
            </div>
            {formError ? <div className="src-form-error">{formError}</div> : null}
            <div className="src-form-actions">
              <button type="submit" className="src-btn" disabled={saving}>
                {saving ? 'Saving…' : 'Save prospect'}
              </button>
              <button type="button" className="src-btn src-btn-ghost" onClick={() => { setShowForm(false); setFormError(''); }}>
                Cancel
              </button>
            </div>
          </form>
        ) : null}

        {importResult ? (
          <div className="src-import" data-testid="import-summary">
            <strong>Imported {importResult.created}</strong>
            {' · '}linked {importResult.linked_to_existing_candidate}
            {' · '}dupes in file {importResult.duplicates_in_file}
            {' · '}already prospects {importResult.already_prospects}
            {Array.isArray(importResult.invalid_rows) && importResult.invalid_rows.length > 0 ? (
              <ul className="src-invalid">
                {importResult.invalid_rows.map((r) => (
                  <li key={r.row}>Row {r.row}: {r.reason}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        <div className="src-filters">
          <input
            className="src-input"
            placeholder="Search name, email, position"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Search prospects"
          />
          <select
            className="src-input"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            aria-label="Filter by status"
          >
            <option value="">All statuses</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>

        {error ? <div className="src-form-error">{error}</div> : null}

        {loading ? (
          <div className="src-muted">Loading prospects…</div>
        ) : rows.length === 0 ? (
          <div className="src-empty">
            <p className="src-empty-title">No prospects yet.</p>
            <p className="src-empty-body">
              Prospects are the people you find yourself and want to reach out
              to. Add one by hand or import a CSV to start your list — or open{' '}
              <button
                type="button"
                className="src-link"
                onClick={() => setTab('find')}
              >
                Find candidates
              </button>{' '}
              to generate ready-to-paste LinkedIn searches for one of your roles.
            </p>
          </div>
        ) : (
          <table className="src-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Position</th>
                <th>Source</th>
                <th>Status</th>
                <th>Added</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id}>
                  <td>{p.full_name}</td>
                  <td>
                    {p.email}
                    {p.suppressed ? (
                      <span className="src-badge" title={`Suppressed: ${p.suppressed}`}>
                        {p.suppressed}
                      </span>
                    ) : null}
                  </td>
                  <td>{p.position || '—'}</td>
                  <td>{p.source_name || p.source_strategy || '—'}</td>
                  <td><span className="src-status">{p.status}</span></td>
                  <td>{formatDate(p.created_at)}</td>
                  <td>
                    {p.status !== 'archived' ? (
                      <button type="button" className="src-link" onClick={() => handleArchive(p.id)}>
                        Archive
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        </>
        )}
      </div>
    </div>
  );
}
