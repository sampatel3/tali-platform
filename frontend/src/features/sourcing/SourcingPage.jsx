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
  Search,
  Upload,
  UsersRound,
} from 'lucide-react';

import { prospects as prospectsApi } from '../../shared/api/prospectsClient';
import { roles as rolesApi } from '../../shared/api';
import { AgentHeader } from '../../shared/layout/AgentHeader';
import { useToast } from '../../context/ToastContext';
import { SourceCandidatesPanel } from './SourceCandidatesPanel';
import CampaignsPanel from './CampaignsPanel';
import './SourcingPage.css';

const WORKFLOW_TABS = [
  {
    id: 'find',
    label: 'Find candidates',
    copy: 'Search from a live role',
    Icon: Search,
  },
  {
    id: 'prospects',
    label: 'Prospects',
    copy: 'Keep your shortlist',
    Icon: UsersRound,
  },
  {
    id: 'campaigns',
    label: 'Campaigns',
    copy: 'Review and reach out',
    Icon: Megaphone,
  },
];

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

function readRouteState() {
  if (typeof window === 'undefined') return { tab: 'find', campaignId: null };
  const params = new URLSearchParams(window.location.search || '');
  const campaignValue = params.get('campaign');
  const campaignId = campaignValue && Number.isFinite(Number(campaignValue))
    ? Number(campaignValue)
    : null;
  const requestedTab = params.get('tab');
  const tab = campaignId
    ? 'campaigns'
    : WORKFLOW_TABS.some((item) => item.id === requestedTab)
      ? requestedTab
      : 'find';
  return { tab, campaignId };
}

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

const TERMINAL_JOB_STATUS = new Set(['filled', 'filled_external', 'cancelled']);
const TERMINAL_WORKABLE_STATE = new Set(['closed', 'archived']);

export function isSourceableRole(role) {
  const jobStatus = String(role?.job_status || '').toLowerCase();
  const workableState = String(role?.workable_job_state || '').toLowerCase();
  if (TERMINAL_JOB_STATUS.has(jobStatus)) return false;
  if (TERMINAL_WORKABLE_STATE.has(workableState)) return false;
  return true;
}

function FindCandidatesTab({ onPrepareProspect }) {
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
        const all = Array.isArray(res.data) ? res.data : (res.data?.roles || []);
        setRoles(all.filter(isSourceableRole));
        setError('');
      })
      .catch(() => active && setError('Could not load your open roles.'))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="src-find">
      <div className="src-find-intro">
        <strong>Start with the role, not a blank search box.</strong>
        <span>
          Taali turns the role criteria into LinkedIn and Google searches. Paste a
          promising profile back here and carry its context into your shortlist.
        </span>
      </div>

      {loading ? (
        <div className="src-muted" role="status">Loading your open roles…</div>
      ) : error ? (
        <div className="src-form-error" role="alert">{error}</div>
      ) : roles.length === 0 ? (
        <div className="src-empty">
          <p className="src-empty-title">No open roles yet</p>
          <p className="src-empty-body">
            Create a job first, then return here to build role-specific searches.
          </p>
        </div>
      ) : (
        <>
          <label className="src-field src-find-picker">
            <span className="src-field-label">Open role</span>
            <select
              className="src-input"
              value={selectedRoleId}
              onChange={(event) => setSelectedRoleId(event.target.value)}
              aria-label="Pick a role"
            >
              <option value="">Choose a role…</option>
              {roles.map((role) => (
                <option key={role.id} value={String(role.id)}>
                  {role.name}
                </option>
              ))}
            </select>
          </label>

          {selectedRoleId ? (
            <SourceCandidatesPanel
              key={selectedRoleId}
              roleId={Number(selectedRoleId)}
              defaultOpen
              onPrepareProspect={onPrepareProspect}
            />
          ) : (
            <div className="src-empty src-empty-compact">
              <p className="src-empty-title">Choose a role to begin</p>
              <p className="src-empty-body">
                You will get copy-ready searches and a grounded first-message helper.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function SourcingPage({ onNavigate, NavComponent = null }) {
  const { showToast } = useToast();
  const initialRoute = useRef(null);
  if (initialRoute.current === null) initialRoute.current = readRouteState();

  const [tab, setTab] = useState(initialRoute.current.tab);
  const [campaignId, setCampaignId] = useState(initialRoute.current.campaignId);
  const tabRefs = useRef({});

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

  const navigateWorkflow = useCallback((nextTab, nextCampaignId = null, options = {}) => {
    const normalizedCampaign = nextTab === 'campaigns' && nextCampaignId
      ? Number(nextCampaignId)
      : null;
    setTab(nextTab);
    setCampaignId(normalizedCampaign);

    if (typeof window === 'undefined') return;
    const url = new URL(window.location.href);
    url.searchParams.set('tab', nextTab);
    if (normalizedCampaign) url.searchParams.set('campaign', String(normalizedCampaign));
    else url.searchParams.delete('campaign');
    const method = options.replace ? 'replaceState' : 'pushState';
    window.history[method]({}, '', `${url.pathname}${url.search}${url.hash}`);
  }, []);

  useEffect(() => {
    const onPopState = () => {
      const next = readRouteState();
      setTab(next.tab);
      setCampaignId(next.campaignId);
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

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

    const params = {
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    };
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
    if (tab === 'prospects') loadProspects();
  }, [loadProspects, tab]);

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  useEffect(() => {
    if (page >= pageCount) setPage(pageCount - 1);
  }, [page, pageCount]);

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
        setNotice(`${form.full_name.trim()} is in your shortlist. Add them to a campaign when you are ready.`);
        showToast('Prospect saved to your shortlist.', 'success');
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
      setNotice(`${prospect.full_name} is back in your active shortlist.`);
      showToast('Prospect restored.', 'success');
      await loadProspects();
    } catch (restoreError) {
      setError('Could not restore this prospect. Try again.');
    }
  };

  const handlePrepareProspect = useCallback(({ profileText, roleId }) => {
    openNewProspect({
      notes: profileText,
      source_name: `sourcing-assist:role-${roleId}`,
    });
    setNotice('Profile context is ready. Add a name and email to keep this person in your shortlist.');
    navigateWorkflow('prospects');
  }, [navigateWorkflow, openNewProspect]);

  const activeTabIndex = WORKFLOW_TABS.findIndex((item) => item.id === tab);
  const handleTabKeyDown = (event, index) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    let nextIndex = index;
    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + WORKFLOW_TABS.length) % WORKFLOW_TABS.length;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % WORKFLOW_TABS.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = WORKFLOW_TABS.length - 1;
    const next = WORKFLOW_TABS[nextIndex];
    navigateWorkflow(next.id);
    tabRefs.current[next.id]?.focus();
  };

  const headerActions = tab === 'prospects' ? (
    <>
      <button type="button" className="btn btn-purple" onClick={() => openNewProspect()}>
        <Plus size={14} aria-hidden="true" />
        Add prospect
      </button>
      <button type="button" className="btn btn-outline" onClick={() => fileRef.current?.click()}>
        <Upload size={14} aria-hidden="true" />
        Import CSV
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
  ) : null;

  const resultLabel = useMemo(() => {
    if (total === 0) return 'No prospects';
    return `${total.toLocaleString()} prospect${total === 1 ? '' : 's'}`;
  }, [total]);

  return (
    <div className="src-shell">
      {NavComponent ? <NavComponent currentPage="sourcing" onNavigate={onNavigate} /> : null}
      <AgentHeader
        breadcrumbs={[{ label: 'Sourcing' }]}
        kicker="SOURCING · HUMAN-APPROVED OUTREACH"
        title={<>Build a better <em>shortlist</em></>}
        period={false}
        subtitle="Find people against a live role, keep their context, then approve every message before it sends."
        actions={headerActions}
      />

      <main className="src-root">
        <div className="src-progress" role="tablist" aria-label="Sourcing workflow">
          {WORKFLOW_TABS.map(({ id, label, copy, Icon }, index) => {
            const active = tab === id;
            const complete = index < activeTabIndex;
            return (
              <button
                key={id}
                ref={(node) => { tabRefs.current[id] = node; }}
                type="button"
                role="tab"
                id={`src-tab-${id}`}
                aria-controls={`src-panel-${id}`}
                aria-selected={active}
                tabIndex={active ? 0 : -1}
                className={`src-step ${active ? 'is-active' : ''} ${complete ? 'is-complete' : ''}`}
                onClick={() => navigateWorkflow(id)}
                onKeyDown={(event) => handleTabKeyDown(event, index)}
              >
                <span className="src-step-number" aria-hidden="true">
                  {complete ? <Icon size={14} /> : index + 1}
                </span>
                <span className="src-step-body">
                  <span className="src-step-title">{label}</span>
                  <span className="src-step-copy">{copy}</span>
                </span>
              </button>
            );
          })}
        </div>

        <section
          className="src-tab-panel"
          id={`src-panel-${tab}`}
          role="tabpanel"
          aria-labelledby={`src-tab-${tab}`}
          tabIndex={0}
        >
          {tab === 'find' ? (
            <FindCandidatesTab onPrepareProspect={handlePrepareProspect} />
          ) : tab === 'campaigns' ? (
            <CampaignsPanel
              initialCampaignId={campaignId}
              onCampaignChange={(id) => navigateWorkflow('campaigns', id)}
            />
          ) : (
            <>
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
                      <h2 className="src-form-title">{editingId ? 'Edit prospect' : 'Add to your shortlist'}</h2>
                      <p className="src-form-copy">
                        {form.source_name.startsWith('sourcing-assist')
                          ? 'The pasted profile is already in Notes. Complete the contact details to keep it.'
                          : 'Keep enough context to make future outreach specific and useful.'}
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
                    {debouncedQ || statusFilter !== 'active' ? 'No prospects match these filters' : 'Your shortlist is empty'}
                  </p>
                  <p className="src-empty-body">
                    {debouncedQ || statusFilter !== 'active'
                      ? 'Try a broader search or change the status filter.'
                      : 'Start with Find candidates, then carry a promising profile into this shortlist.'}
                  </p>
                  {!debouncedQ && statusFilter === 'active' ? (
                    <button type="button" className="src-btn" onClick={() => navigateWorkflow('find')}>
                      Find candidates
                      <ChevronRight size={14} aria-hidden="true" />
                    </button>
                  ) : null}
                </div>
              ) : (
                <div className="src-table-wrap">
                  <table className="src-table">
                    <thead>
                      <tr>
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
                          <td data-label="Prospect">
                            <div className="src-person-name">
                              {prospect.full_name}
                              {prospect.candidate_id ? <span className="src-badge">Candidate linked</span> : null}
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
            </>
          )}
        </section>
      </main>
    </div>
  );
}
