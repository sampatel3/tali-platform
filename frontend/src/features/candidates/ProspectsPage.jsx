import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  ChevronLeft,
  ChevronRight,
  Megaphone,
  Plus,
  Send,
  Upload,
} from 'lucide-react';

import { prospects as prospectsApi } from '../../shared/api/prospectsClient';
import { outreach as outreachApi } from '../../shared/api/outreachClient';
import { AgentHeader } from '../../shared/layout/AgentHeader';
import { useToast } from '../../context/ToastContext';
import CampaignsPanel from '../sourcing/CampaignsPanel';
import '../sourcing/sourcingPanels.css';

// Prospects — not-yet-applied contacts, org-level. Folded out of the retired
// Sourcing tab into the Candidates area: this is the pool of people a recruiter
// sourced (CSV import, manual add, the job page's Find-candidates helper) who
// have NOT applied to a role yet. They carry no CV parse, no score, and no
// application — a prospect only becomes a scored pipeline application when they
// engage (respond / apply). Outreach ("Reach out") is an action here; the
// audience rail server-side excludes anyone with an open application, so this
// never targets active-pipeline candidates.

const STATUSES = ['new', 'contacted', 'interested', 'converted', 'archived'];
const PAGE_SIZE = 25;

const EMPTY_FORM = {
  full_name: '',
  email: '',
  position: '',
  location: '',
  linkedin_url: '',
  phone: '',
  notes: '',
  status: 'new',
  source_name: 'manual',
};

function formatDate(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch (error) {
    return '';
  }
}

function humanizeSource(prospect) {
  const raw = String(prospect?.source_name || prospect?.source_strategy || '').trim();
  if (!raw) return 'Not recorded';
  if (raw.startsWith('csv:')) {
    const filename = raw.slice(4).trim();
    return filename ? `CSV · ${filename}` : 'CSV import';
  }
  if (raw.startsWith('sourcing-assist')) return 'Find candidates';
  if (raw === 'manual') return 'Added manually';
  if (raw === 'rediscovery') return 'Candidate rediscovery';
  return raw.replaceAll('_', ' ');
}

export default function ProspectsPage({ onNavigate, NavComponent = null }) {
  const { showToast } = useToast();

  // 'list' = prospects table · 'campaigns' = outreach campaign management.
  const [view, setView] = useState('list');
  const [campaignId, setCampaignId] = useState(null);

  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [statusFilter, setStatusFilter] = useState('active');
  const [page, setPage] = useState(0);
  const loadedProspects = useRef(false);
  const requestSequence = useRef(0);

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const [importResult, setImportResult] = useState(null);
  const [notice, setNotice] = useState('');
  const fileRef = useRef(null);

  // Multi-select for "Reach out". Keyed by prospect id.
  const [selected, setSelected] = useState({});
  const [reachingOut, setReachingOut] = useState(false);

  useEffect(() => {
    const nextQ = q.trim();
    if (nextQ === debouncedQ) return undefined;
    const timer = window.setTimeout(() => {
      setDebouncedQ(nextQ);
      setPage(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [debouncedQ, q]);

  const loadProspects = useCallback(async () => {
    const requestId = ++requestSequence.current;
    if (loadedProspects.current) setRefreshing(true);
    else setLoading(true);

    const params = { limit: PAGE_SIZE, offset: page * PAGE_SIZE };
    if (debouncedQ) params.q = debouncedQ;
    if (statusFilter) params.status = statusFilter;

    try {
      const res = await prospectsApi.list(params);
      if (requestId !== requestSequence.current) return;
      setRows(Array.isArray(res.data?.prospects) ? res.data.prospects : []);
      setTotal(Number(res.data?.total || 0));
      setError('');
      loadedProspects.current = true;
    } catch (loadError) {
      if (requestId !== requestSequence.current) return;
      setError('Could not load prospects. Check your connection and try again.');
    } finally {
      if (requestId === requestSequence.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [debouncedQ, page, statusFilter]);

  useEffect(() => {
    if (view === 'list') loadProspects();
  }, [loadProspects, view]);

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  useEffect(() => {
    if (page >= pageCount) setPage(pageCount - 1);
  }, [page, pageCount]);

  // Drop stale selections when the visible rows change.
  useEffect(() => {
    setSelected((current) => {
      const visible = new Set(rows.map((r) => r.id));
      const next = {};
      Object.keys(current).forEach((id) => {
        if (current[id] && visible.has(Number(id))) next[id] = true;
      });
      return next;
    });
  }, [rows]);

  const openNewProspect = useCallback((prefill = {}) => {
    setEditingId(null);
    setForm({ ...EMPTY_FORM, ...prefill });
    setFormError('');
    setShowForm(true);
  }, []);

  const openEditProspect = useCallback((prospect) => {
    setEditingId(prospect.id);
    setForm({
      ...EMPTY_FORM,
      full_name: prospect.full_name || '',
      email: prospect.email || '',
      position: prospect.position || '',
      location: prospect.location || '',
      linkedin_url: prospect.linkedin_url || '',
      phone: prospect.phone || '',
      notes: prospect.notes || '',
      status: prospect.status || 'new',
      source_name: prospect.source_name || 'manual',
    });
    setFormError('');
    setShowForm(true);
  }, []);

  const closeForm = () => {
    setShowForm(false);
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError('');
  };

  const handleSave = async (event) => {
    event.preventDefault();
    setFormError('');
    if (!form.full_name.trim() || !form.email.trim()) {
      setFormError('Name and email are required.');
      return;
    }

    const basePayload = {
      full_name: form.full_name.trim(),
      email: form.email.trim(),
      position: form.position.trim() || null,
      location: form.location.trim() || null,
      linkedin_url: form.linkedin_url.trim() || null,
      phone: form.phone.trim() || null,
      notes: form.notes.trim() || null,
    };

    setSaving(true);
    try {
      if (editingId) {
        await prospectsApi.update(editingId, { ...basePayload, status: form.status });
        setNotice(`${form.full_name.trim()} was updated.`);
        showToast('Prospect updated.', 'success');
      } else {
        await prospectsApi.create({ ...basePayload, source_name: form.source_name || 'manual' });
        setNotice(`${form.full_name.trim()} is in your prospects. Reach out when you are ready.`);
        showToast('Prospect saved.', 'success');
      }
      closeForm();
      setQ('');
      setDebouncedQ('');
      setStatusFilter('active');
      setPage(0);
      await loadProspects();
    } catch (saveError) {
      setFormError(
        saveError?.response?.status === 409
          ? 'A prospect with this email already exists.'
          : 'Could not save this prospect. Try again.',
      );
    } finally {
      setSaving(false);
    }
  };

  const handleImport = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setImportResult(null);
    setError('');
    try {
      const res = await prospectsApi.importCsv(file);
      setImportResult(res.data);
      setPage(0);
      await loadProspects();
      showToast(`Imported ${res.data?.created || 0} prospects.`, 'success');
    } catch (importError) {
      setError('CSV import failed. Check the file format and try again.');
    } finally {
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const handleArchive = async (prospect) => {
    try {
      await prospectsApi.archive(prospect.id);
      setNotice(`${prospect.full_name} was archived. You can restore them from the Archived filter.`);
      showToast('Prospect archived.', 'success');
      await loadProspects();
    } catch (archiveError) {
      setError('Could not archive this prospect. Try again.');
    }
  };

  const handleRestore = async (prospect) => {
    try {
      await prospectsApi.update(prospect.id, { status: 'new' });
      setNotice(`${prospect.full_name} is back in your active prospects.`);
      showToast('Prospect restored.', 'success');
      await loadProspects();
    } catch (restoreError) {
      setError('Could not restore this prospect. Try again.');
    }
  };

  const selectedIds = useMemo(
    () => Object.keys(selected).filter((id) => selected[id]).map(Number),
    [selected],
  );

  const openCampaigns = useCallback((id = null) => {
    setCampaignId(id);
    setView('campaigns');
  }, []);

  // Reach out: create a campaign seeded with the selected prospects, then hand
  // off to the campaign detail (audience → generate → approve-and-send HITL).
  const handleReachOut = async () => {
    if (!selectedIds.length || reachingOut) return;
    setReachingOut(true);
    setError('');
    try {
      const name = `Outreach · ${new Date().toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`;
      const created = await outreachApi.createCampaign({ name, role_id: null });
      const newId = created.data?.id;
      await outreachApi.addAudience(newId, { prospect_ids: selectedIds });
      setSelected({});
      showToast(`Started a campaign with ${selectedIds.length} prospect${selectedIds.length === 1 ? '' : 's'}.`, 'success');
      openCampaigns(newId);
    } catch (reachError) {
      setError('Could not start the campaign. Try again.');
    } finally {
      setReachingOut(false);
    }
  };

  const headerActions = view === 'list' ? (
    <>
      <button type="button" className="btn btn-purple" onClick={() => openNewProspect()}>
        <Plus size={14} aria-hidden="true" />
        Add prospect
      </button>
      <button type="button" className="btn btn-outline" onClick={() => fileRef.current?.click()}>
        <Upload size={14} aria-hidden="true" />
        Import CSV
      </button>
      <button type="button" className="btn btn-outline" onClick={() => openCampaigns(null)}>
        <Megaphone size={14} aria-hidden="true" />
        Campaigns
      </button>
      <input
        ref={fileRef}
        type="file"
        accept=".csv,text/csv"
        onChange={handleImport}
        className="sr-only"
        data-testid="csv-input"
        tabIndex={-1}
      />
    </>
  ) : (
    <button type="button" className="btn btn-outline" onClick={() => { setView('list'); setCampaignId(null); }}>
      <ChevronLeft size={14} aria-hidden="true" />
      Back to prospects
    </button>
  );

  const resultLabel = useMemo(() => {
    if (total === 0) return 'No prospects';
    return `${total.toLocaleString()} prospect${total === 1 ? '' : 's'}`;
  }, [total]);

  return (
    <div className="src-shell">
      {NavComponent ? <NavComponent currentPage="candidates" onNavigate={onNavigate} /> : null}
      <AgentHeader
        breadcrumbs={[{ label: 'Candidates' }, { label: 'Prospects' }]}
        kicker="PROSPECTS · NOT YET APPLIED"
        title={<>Your <em>prospects</em></>}
        period={false}
        subtitle="Contacts you have sourced but who have not applied yet. They are scored only once they engage. Reach out with a human-approved campaign."
        actions={headerActions}
      />

      <main className="src-root">
        {view === 'campaigns' ? (
          <section className="src-tab-panel">
            <CampaignsPanel
              initialCampaignId={campaignId}
              onCampaignChange={(id) => setCampaignId(id)}
            />
          </section>
        ) : (
          <section className="src-tab-panel">
            {notice ? (
              <div className="src-notice src-notice-success" role="status">
                <span>{notice}</span>
                <button type="button" className="src-link" onClick={() => setNotice('')}>Dismiss</button>
              </div>
            ) : null}

            {showForm ? (
              <form className="src-form" onSubmit={handleSave} aria-label={editingId ? 'Edit prospect' : 'Add prospect'}>
                <div className="src-form-header">
                  <div>
                    <h2 className="src-form-title">{editingId ? 'Edit prospect' : 'Add a prospect'}</h2>
                    <p className="src-form-copy">
                      Keep enough context to make future outreach specific and useful.
                    </p>
                  </div>
                </div>

                <div className="src-form-grid">
                  <label className="src-field">
                    <span className="src-field-label">Full name</span>
                    <input
                      className="src-input"
                      value={form.full_name}
                      onChange={(event) => setForm({ ...form, full_name: event.target.value })}
                      autoFocus
                      required
                    />
                  </label>
                  <label className="src-field">
                    <span className="src-field-label">Email</span>
                    <input
                      className="src-input"
                      type="email"
                      value={form.email}
                      onChange={(event) => setForm({ ...form, email: event.target.value })}
                      required
                    />
                  </label>
                  <label className="src-field">
                    <span className="src-field-label">Position</span>
                    <input
                      className="src-input"
                      value={form.position}
                      onChange={(event) => setForm({ ...form, position: event.target.value })}
                    />
                  </label>
                  <label className="src-field">
                    <span className="src-field-label">Location</span>
                    <input
                      className="src-input"
                      value={form.location}
                      onChange={(event) => setForm({ ...form, location: event.target.value })}
                    />
                  </label>
                  <label className="src-field">
                    <span className="src-field-label">LinkedIn URL</span>
                    <input
                      className="src-input"
                      type="url"
                      value={form.linkedin_url}
                      onChange={(event) => setForm({ ...form, linkedin_url: event.target.value })}
                    />
                  </label>
                  <label className="src-field">
                    <span className="src-field-label">Phone</span>
                    <input
                      className="src-input"
                      type="tel"
                      value={form.phone}
                      onChange={(event) => setForm({ ...form, phone: event.target.value })}
                    />
                  </label>
                  {editingId ? (
                    <label className="src-field">
                      <span className="src-field-label">Prospect status</span>
                      <select
                        className="src-input"
                        value={form.status}
                        onChange={(event) => setForm({ ...form, status: event.target.value })}
                      >
                        {STATUSES.map((status) => (
                          <option key={status} value={status}>{status}</option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                  <label className="src-field src-field-wide">
                    <span className="src-field-label">Notes or profile context</span>
                    <textarea
                      className="src-input src-textarea"
                      rows={5}
                      value={form.notes}
                      onChange={(event) => setForm({ ...form, notes: event.target.value })}
                      placeholder="What made this person relevant?"
                    />
                  </label>
                </div>

                {formError ? <div className="src-form-error" role="alert">{formError}</div> : null}
                <div className="src-form-actions">
                  <button type="submit" className="src-btn" disabled={saving}>
                    {saving ? 'Saving…' : editingId ? 'Save changes' : 'Save prospect'}
                  </button>
                  <button type="button" className="src-btn src-btn-ghost" onClick={closeForm}>
                    Cancel
                  </button>
                </div>
              </form>
            ) : null}

            {importResult ? (
              <div className="src-import" data-testid="import-summary" role="status">
                <strong>Imported {importResult.created}</strong>
                <span>Linked to {importResult.linked_to_existing_candidate} existing candidates</span>
                <span>{importResult.duplicates_in_file} duplicates skipped</span>
                <span>{importResult.already_prospects} already in prospects</span>
                {Array.isArray(importResult.invalid_rows) && importResult.invalid_rows.length > 0 ? (
                  <ul className="src-invalid">
                    {importResult.invalid_rows.map((row) => (
                      <li key={row.row}>Row {row.row}: {row.reason}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}

            {selectedIds.length ? (
              <div className="src-notice" role="status">
                <span>{selectedIds.length} selected</span>
                <button
                  type="button"
                  className="src-btn"
                  onClick={handleReachOut}
                  disabled={reachingOut}
                >
                  <Send size={13} aria-hidden="true" />
                  {reachingOut ? 'Starting…' : 'Reach out'}
                </button>
                <button type="button" className="src-link" onClick={() => setSelected({})}>Clear</button>
              </div>
            ) : null}

            <div className="src-toolbar">
              <div className="src-filters">
                <label className="src-field">
                  <span className="src-field-label">Search prospects</span>
                  <input
                    className="src-input"
                    placeholder="Name, email, or position"
                    value={q}
                    onChange={(event) => setQ(event.target.value)}
                  />
                </label>
                <label className="src-field">
                  <span className="src-field-label">Status filter</span>
                  <select
                    className="src-input"
                    value={statusFilter}
                    onChange={(event) => {
                      setStatusFilter(event.target.value);
                      setPage(0);
                    }}
                  >
                    <option value="active">Active prospects</option>
                    <option value="">All statuses</option>
                    {STATUSES.map((status) => (
                      <option key={status} value={status}>{status}</option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="src-result-count" aria-live="polite">
                {resultLabel}
                {refreshing ? <span className="src-refreshing">Updating…</span> : null}
              </div>
            </div>

            {error ? (
              <div className="src-form-error src-error-row" role="alert">
                <span>{error}</span>
                <button type="button" className="src-link" onClick={loadProspects}>Retry</button>
              </div>
            ) : null}

            {loading ? (
              <div className="src-muted" role="status">Loading prospects…</div>
            ) : rows.length === 0 ? (
              <div className="src-empty">
                <p className="src-empty-title">
                  {debouncedQ || statusFilter !== 'active' ? 'No prospects match these filters' : 'No prospects yet'}
                </p>
                <p className="src-empty-body">
                  {debouncedQ || statusFilter !== 'active'
                    ? 'Try a broader search or change the status filter.'
                    : 'Add a prospect, import a CSV, or use Find candidates on a job to build this list.'}
                </p>
              </div>
            ) : (
              <div className="src-table-wrap">
                <table className="src-table">
                  <thead>
                    <tr>
                      <th aria-label="Select" />
                      <th>Prospect</th>
                      <th>Contact</th>
                      <th>Source</th>
                      <th>Status</th>
                      <th>Added</th>
                      <th aria-label="Actions" />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((prospect) => (
                      <tr key={prospect.id}>
                        <td data-label="Select">
                          <input
                            type="checkbox"
                            checked={!!selected[prospect.id]}
                            disabled={!!prospect.suppressed}
                            onChange={(event) => setSelected((current) => ({
                              ...current,
                              [prospect.id]: event.target.checked,
                            }))}
                            aria-label={`Select ${prospect.full_name}`}
                          />
                        </td>
                        <td data-label="Prospect">
                          <div className="src-person-name">
                            {prospect.full_name}
                            {prospect.candidate_id ? (
                              // Non-navigating indicator: a prospect links to a
                              // candidate (candidate id), but the candidate
                              // report route is application-scoped and a prospect
                              // has 0..n applications — there is no unambiguous
                              // application id to open, so this is a label, not a
                              // link.
                              <span
                                className="src-badge"
                                title="This prospect matches an existing candidate in your pipeline"
                              >
                                In your candidates
                              </span>
                            ) : null}
                          </div>
                          <div className="src-person-meta">
                            {[prospect.position, prospect.location].filter(Boolean).join(' · ') || 'No role details'}
                          </div>
                        </td>
                        <td data-label="Contact">
                          <a className="src-contact" href={`mailto:${prospect.email}`}>{prospect.email}</a>
                          {prospect.phone ? <div className="src-person-meta">{prospect.phone}</div> : null}
                          {prospect.suppressed ? (
                            <span className="src-badge" title={`Suppressed: ${prospect.suppressed}`}>
                              {prospect.suppressed}
                            </span>
                          ) : null}
                        </td>
                        <td data-label="Source">{humanizeSource(prospect)}</td>
                        <td data-label="Status">
                          <span className={`src-status src-status-${prospect.status}`}>{prospect.status}</span>
                        </td>
                        <td data-label="Added">{formatDate(prospect.created_at)}</td>
                        <td data-label="Actions">
                          <div className="src-row-actions">
                            <button type="button" className="src-link" onClick={() => openEditProspect(prospect)}>
                              Edit
                            </button>
                            {prospect.status === 'archived' ? (
                              <button type="button" className="src-link" onClick={() => handleRestore(prospect)}>
                                Restore
                              </button>
                            ) : (
                              <button type="button" className="src-link" onClick={() => handleArchive(prospect)}>
                                Archive
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {total > PAGE_SIZE ? (
              <nav className="src-pagination" aria-label="Prospect pages">
                <button
                  type="button"
                  className="src-page-btn"
                  onClick={() => setPage((value) => Math.max(0, value - 1))}
                  disabled={page === 0}
                >
                  <ChevronLeft size={14} aria-hidden="true" />
                  Previous
                </button>
                <span className="src-page-info">Page {page + 1} of {pageCount}</span>
                <button
                  type="button"
                  className="src-page-btn"
                  onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}
                  disabled={page + 1 >= pageCount}
                >
                  Next
                  <ChevronRight size={14} aria-hidden="true" />
                </button>
              </nav>
            ) : null}
          </section>
        )}
      </main>
    </div>
  );
}
