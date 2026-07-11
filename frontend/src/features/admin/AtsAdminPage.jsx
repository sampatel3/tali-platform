import React, { useEffect, useState } from 'react';
import { Trash2 } from 'lucide-react';

import {
  compliance as complianceApi,
  offerTemplates as offerTemplatesApi,
} from '../../shared/api';
import {
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  Input,
  PageContainer,
  PageHeader,
  Select,
  Spinner,
  TabBar,
} from '../../shared/ui/TaaliPrimitives';

const ErrorNote = ({ children }) =>
  children ? (
    <Card className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] px-3 py-2 text-sm text-[var(--taali-danger)]">
      {children}
    </Card>
  ) : null;

// --- Offer templates ----------------------------------------------------
const OfferTemplatesSection = () => {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({ name: '', base_salary_amount: '', currency: 'AED', pay_frequency: 'year' });

  const reload = () => offerTemplatesApi.list().then(setRows);
  useEffect(() => {
    reload().catch(() => setError('Could not load templates.'));
  }, []);

  const create = async () => {
    setError(null);
    try {
      await offerTemplatesApi.create({
        name: form.name.trim(),
        base_salary_amount: form.base_salary_amount === '' ? null : Number(form.base_salary_amount),
        currency: form.currency || null,
        pay_frequency: form.pay_frequency || null,
      });
      setForm({ name: '', base_salary_amount: '', currency: 'AED', pay_frequency: 'year' });
      await reload();
    } catch {
      setError('Could not create the template — a name is required.');
    }
  };

  if (rows === null && !error) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <ErrorNote>{error}</ErrorNote>
      <Card className="px-4 py-4">
        <h3 className="text-sm font-semibold text-[var(--taali-text)]">New template</h3>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="block flex-1 min-w-[160px]">
            <span className="mb-1 block text-xs text-[var(--taali-muted)]">Name</span>
            <Input
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="e.g. Senior Eng — Band A"
            />
          </label>
          <label className="block w-32">
            <span className="mb-1 block text-xs text-[var(--taali-muted)]">Base salary</span>
            <Input
              type="number"
              value={form.base_salary_amount}
              onChange={(e) => setForm((f) => ({ ...f, base_salary_amount: e.target.value }))}
            />
          </label>
          <label className="block w-24">
            <span className="mb-1 block text-xs text-[var(--taali-muted)]">Currency</span>
            <Input value={form.currency} onChange={(e) => setForm((f) => ({ ...f, currency: e.target.value }))} />
          </label>
          <label className="block w-28">
            <span className="mb-1 block text-xs text-[var(--taali-muted)]">Frequency</span>
            <Select
              value={form.pay_frequency}
              onChange={(e) => setForm((f) => ({ ...f, pay_frequency: e.target.value }))}
            >
              <option value="year">Year</option>
              <option value="month">Month</option>
              <option value="hour">Hour</option>
            </Select>
          </label>
          <Button variant="primary" disabled={!form.name.trim()} onClick={create}>
            Add
          </Button>
        </div>
      </Card>

      {(rows || []).length === 0 ? (
        <EmptyState
          title="No templates"
          description="Reusable compensation templates prefill new offers."
          className="py-8"
        />
      ) : (
        rows.map((t) => (
          <Card key={t.id} className="flex items-center justify-between gap-3 px-4 py-3">
            <div>
              <div className="text-sm font-medium text-[var(--taali-text)]">{t.name}</div>
              <div className="mt-0.5 text-xs text-[var(--taali-muted)]">
                {t.currency || ''} {t.base_salary_amount != null ? t.base_salary_amount.toLocaleString() : '—'}
                {t.pay_frequency ? ` / ${t.pay_frequency}` : ''}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Badge variant={t.is_active ? 'success' : 'muted'}>{t.is_active ? 'active' : 'inactive'}</Badge>
              <button
                type="button"
                aria-label="Delete"
                className="text-[var(--taali-muted)] hover:text-[var(--taali-danger)]"
                onClick={() => offerTemplatesApi.remove(t.id).then(reload)}
              >
                <Trash2 size={16} />
              </button>
            </div>
          </Card>
        ))
      )}
    </div>
  );
};

// --- Compliance (GDPR data-subject requests + aggregate EEO) -------------
const EEO_CATEGORIES = ['gender', 'race_ethnicity', 'veteran_status', 'disability_status'];

const ComplianceSection = () => {
  const [requests, setRequests] = useState(null);
  const [eeo, setEeo] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({ request_type: 'access', subject_email: '' });
  const [exportView, setExportView] = useState(null);

  const reload = () =>
    Promise.all([complianceApi.listRequests(), complianceApi.eeoReport()]).then(([reqs, report]) => {
      setRequests(reqs);
      setEeo(report);
    });
  useEffect(() => {
    // Owner-gated server-side — a non-owner gets 403; show a friendly note.
    reload().catch(() => setError('You need to be a workspace owner to view compliance data.'));
  }, []);

  const create = async () => {
    setError(null);
    try {
      await complianceApi.createRequest({
        request_type: form.request_type,
        subject_email: form.subject_email.trim() || null,
      });
      setForm({ request_type: 'access', subject_email: '' });
      await reload();
    } catch {
      setError('Could not create the request — an email or candidate is required.');
    }
  };

  const fulfill = async (id) => {
    setError(null);
    try {
      const res = await complianceApi.fulfillRequest(id);
      if (res?.export) setExportView(res.export);
      await reload();
    } catch {
      setError('Could not fulfil the request.');
    }
  };

  const reject = async (id) => {
    setError(null);
    try {
      await complianceApi.rejectRequest(id, 'identity not verified');
      await reload();
    } catch {
      setError('Could not reject the request.');
    }
  };

  if (requests === null && !error) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <ErrorNote>{error}</ErrorNote>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-[var(--taali-text)]">Data-subject requests</h3>
        <Card className="px-4 py-4">
          <div className="flex flex-wrap items-end gap-3">
            <label className="block w-40">
              <span className="mb-1 block text-xs text-[var(--taali-muted)]">Type</span>
              <Select
                value={form.request_type}
                onChange={(e) => setForm((f) => ({ ...f, request_type: e.target.value }))}
              >
                <option value="access">Access (export)</option>
                <option value="erasure">Erasure</option>
              </Select>
            </label>
            <label className="block flex-1 min-w-[200px]">
              <span className="mb-1 block text-xs text-[var(--taali-muted)]">Subject email</span>
              <Input
                value={form.subject_email}
                onChange={(e) => setForm((f) => ({ ...f, subject_email: e.target.value }))}
                placeholder="person@example.com"
              />
            </label>
            <Button variant="primary" disabled={!form.subject_email.trim()} onClick={create}>
              Log request
            </Button>
          </div>
        </Card>

        <div className="mt-3 space-y-2">
          {(requests || []).length === 0 ? (
            <EmptyState
              title="No requests"
              description="Logged access/erasure requests appear here."
              className="py-6"
            />
          ) : (
            requests.map((r) => (
              <Card key={r.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <div className="truncate text-sm text-[var(--taali-text)]">
                    {r.subject_email || `candidate #${r.candidate_id}`}
                  </div>
                  <div className="mt-0.5 text-xs text-[var(--taali-muted)]">{r.request_type}</div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge
                    variant={
                      r.status === 'completed' ? 'success' : r.status === 'rejected' ? 'danger' : 'warning'
                    }
                  >
                    {r.status}
                  </Badge>
                  {r.status === 'pending' ? (
                    <>
                      <Button variant="ghost" size="xs" onClick={() => fulfill(r.id)}>
                        Fulfil
                      </Button>
                      <Button variant="ghost" size="xs" onClick={() => reject(r.id)}>
                        Reject
                      </Button>
                    </>
                  ) : null}
                </div>
              </Card>
            ))
          )}
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-[var(--taali-text)]">
          EEO self-ID <span className="text-[var(--taali-muted)]">(aggregate — never per-candidate)</span>
        </h3>
        {!eeo || eeo.total === 0 ? (
          <EmptyState
            title="No EEO responses"
            description="Voluntary self-ID counts appear here once collected."
            className="py-6"
          />
        ) : (
          <Card className="px-4 py-3 text-sm text-[var(--taali-text)]">
            <div className="mb-2">
              {eeo.total} responses · {eeo.declined_count} declined
            </div>
            {EEO_CATEGORIES.map((cat) => {
              // New suppressed shape: { values: {label: count}, suppressed_count }.
              // Below-threshold responses arrive as an anonymous bucket — the
              // backend never sends their value labels.
              const values = eeo[cat]?.values || {};
              const suppressed = eeo[cat]?.suppressed_count || 0;
              if (!Object.keys(values).length && !suppressed) return null;
              return (
                <div key={cat} className="mt-1">
                  <span className="text-xs uppercase text-[var(--taali-muted)]">{cat.replace(/_/g, ' ')}: </span>
                  {Object.entries(values).map(([k, v]) => (
                    <Badge key={k} variant="muted" className="ml-1">
                      {k}: {v}
                    </Badge>
                  ))}
                  {suppressed ? (
                    <Badge variant="muted" className="ml-1">
                      {suppressed} suppressed
                    </Badge>
                  ) : null}
                </div>
              );
            })}
            <div className="mt-2 text-xs text-[var(--taali-muted)]">
              Small groups are combined into a “suppressed” bucket to protect individuals.
            </div>
          </Card>
        )}
      </section>

      <Dialog open={exportView !== null} onClose={() => setExportView(null)} title="Data export">
        <pre className="max-h-[60vh] overflow-auto rounded-[var(--taali-radius-control)] bg-[var(--taali-surface-subtle)] p-3 text-xs text-[var(--taali-text)]">
          {exportView ? JSON.stringify(exportView, null, 2) : ''}
        </pre>
      </Dialog>
    </div>
  );
};

// Structured so more admin tabs can be added later.
const TABS = [
  { id: 'templates', label: 'Offer templates' },
  { id: 'compliance', label: 'Compliance' },
];

export const AtsAdminPage = ({ onNavigate, NavComponent }) => {
  const [tab, setTab] = useState('templates');
  return (
    <>
      {NavComponent ? <NavComponent currentPage="settings" onNavigate={onNavigate} /> : null}
      <PageContainer>
        <PageHeader title="ATS admin" subtitle="Offer templates and compliance for your workspace." />
        <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
        <div className="mt-4">
          {tab === 'templates' ? <OfferTemplatesSection /> : null}
          {tab === 'compliance' ? <ComplianceSection /> : null}
        </div>
      </PageContainer>
    </>
  );
};

export default AtsAdminPage;
