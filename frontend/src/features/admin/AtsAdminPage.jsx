import React, { useEffect, useState } from 'react';
import { Trash2 } from 'lucide-react';

import {
  webhooks as webhooksApi,
  offerTemplates as offerTemplatesApi,
  compliance as complianceApi,
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

const WEBHOOK_EVENTS = ['application.created', 'application.stage_changed', 'offer.accepted', 'offer.sent'];

const ErrorNote = ({ children }) => (children ? (
  <Card className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] px-3 py-2 text-sm text-[var(--taali-danger)]">{children}</Card>
) : null);

// --- Webhooks -----------------------------------------------------------
const WebhooksSection = () => {
  const [subs, setSubs] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({ url: '', secret: '', event_types: [] });
  const [deliveries, setDeliveries] = useState(null);

  const reload = () => webhooksApi.list().then(setSubs);
  useEffect(() => { reload().catch(() => setError('Failed to load webhooks.')); }, []);

  const toggleEvent = (evt) => setForm((f) => ({
    ...f,
    event_types: f.event_types.includes(evt) ? f.event_types.filter((e) => e !== evt) : [...f.event_types, evt],
  }));

  const create = async () => {
    setError(null);
    try {
      await webhooksApi.create({ url: form.url.trim(), secret: form.secret.trim(), event_types: form.event_types });
      setForm({ url: '', secret: '', event_types: [] });
      await reload();
    } catch { setError('Could not create the subscription (url + secret required).'); }
  };

  if (subs === null && !error) return <div className="flex justify-center py-10"><Spinner /></div>;

  return (
    <div className="space-y-4">
      <ErrorNote>{error}</ErrorNote>
      <Card className="px-4 py-4">
        <h3 className="text-sm font-semibold text-[var(--taali-text)]">Add endpoint</h3>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="block"><span className="mb-1 block text-xs text-[var(--taali-muted)]">URL</span>
            <Input value={form.url} onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))} placeholder="https://…/hook" /></label>
          <label className="block"><span className="mb-1 block text-xs text-[var(--taali-muted)]">Signing secret</span>
            <Input value={form.secret} onChange={(e) => setForm((f) => ({ ...f, secret: e.target.value }))} placeholder="shared secret" /></label>
        </div>
        <div className="mt-3">
          <span className="mb-1 block text-xs text-[var(--taali-muted)]">Events (none = all)</span>
          <div className="flex flex-wrap gap-3">
            {WEBHOOK_EVENTS.map((evt) => (
              <label key={evt} className="flex items-center gap-1.5 text-sm text-[var(--taali-text)]">
                <input type="checkbox" checked={form.event_types.includes(evt)} onChange={() => toggleEvent(evt)} className="h-4 w-4 accent-[var(--taali-purple)]" />
                {evt}
              </label>
            ))}
          </div>
        </div>
        <div className="mt-3"><Button variant="primary" disabled={!form.url.trim() || !form.secret.trim()} onClick={create}>Add</Button></div>
      </Card>

      {(subs || []).length === 0 ? (
        <EmptyState title="No webhook endpoints" description="Add an endpoint to receive signed events." className="py-8" />
      ) : subs.map((s) => (
        <Card key={s.id} className="flex items-center justify-between gap-3 px-4 py-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-[var(--taali-text)]">{s.url}</div>
            <div className="mt-0.5 text-xs text-[var(--taali-muted)]">{(s.event_types || []).length ? s.event_types.join(', ') : 'all events'}</div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Badge variant={s.is_active ? 'success' : 'muted'}>{s.is_active ? 'active' : 'paused'}</Badge>
            <Button variant="ghost" size="xs" onClick={() => webhooksApi.update(s.id, { is_active: !s.is_active }).then(reload)}>{s.is_active ? 'Pause' : 'Resume'}</Button>
            <Button variant="ghost" size="xs" onClick={() => webhooksApi.deliveries(s.id).then(setDeliveries)}>Deliveries</Button>
            <button type="button" aria-label="Delete" className="text-[var(--taali-muted)] hover:text-[var(--taali-danger)]" onClick={() => webhooksApi.remove(s.id).then(reload)}><Trash2 size={16} /></button>
          </div>
        </Card>
      ))}

      <Dialog open={deliveries !== null} onClose={() => setDeliveries(null)} title="Recent deliveries">
        {(deliveries || []).length === 0 ? (
          <p className="text-sm text-[var(--taali-muted)]">No deliveries yet.</p>
        ) : (
          <div className="space-y-2">
            {deliveries.map((d) => (
              <div key={d.id} className="flex items-center justify-between gap-3 text-sm">
                <span className="text-[var(--taali-text)]">{d.event_type}</span>
                <Badge variant={d.status === 'delivered' ? 'success' : d.status === 'failed' ? 'danger' : 'muted'}>
                  {d.status}{d.response_status ? ` · ${d.response_status}` : ''}
                </Badge>
              </div>
            ))}
          </div>
        )}
      </Dialog>
    </div>
  );
};

// --- Offer templates ----------------------------------------------------
const OfferTemplatesSection = () => {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({ name: '', base_salary_amount: '', currency: 'AED', pay_frequency: 'year' });

  const reload = () => offerTemplatesApi.list().then(setRows);
  useEffect(() => { reload().catch(() => setError('Failed to load templates.')); }, []);

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
    } catch { setError('Could not create the template (name required).'); }
  };

  if (rows === null && !error) return <div className="flex justify-center py-10"><Spinner /></div>;

  return (
    <div className="space-y-4">
      <ErrorNote>{error}</ErrorNote>
      <Card className="px-4 py-4">
        <h3 className="text-sm font-semibold text-[var(--taali-text)]">New template</h3>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="block flex-1 min-w-[160px]"><span className="mb-1 block text-xs text-[var(--taali-muted)]">Name</span>
            <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="e.g. Senior Eng — Band A" /></label>
          <label className="block w-32"><span className="mb-1 block text-xs text-[var(--taali-muted)]">Base salary</span>
            <Input type="number" value={form.base_salary_amount} onChange={(e) => setForm((f) => ({ ...f, base_salary_amount: e.target.value }))} /></label>
          <label className="block w-24"><span className="mb-1 block text-xs text-[var(--taali-muted)]">Currency</span>
            <Input value={form.currency} onChange={(e) => setForm((f) => ({ ...f, currency: e.target.value }))} /></label>
          <Button variant="primary" disabled={!form.name.trim()} onClick={create}>Add</Button>
        </div>
      </Card>

      {(rows || []).length === 0 ? (
        <EmptyState title="No templates" description="Reusable comp templates prefill new offers." className="py-8" />
      ) : rows.map((t) => (
        <Card key={t.id} className="flex items-center justify-between gap-3 px-4 py-3">
          <div>
            <div className="text-sm font-medium text-[var(--taali-text)]">{t.name}</div>
            <div className="mt-0.5 text-xs text-[var(--taali-muted)]">{t.currency || ''} {t.base_salary_amount != null ? t.base_salary_amount.toLocaleString() : '—'}{t.pay_frequency ? ` / ${t.pay_frequency}` : ''}</div>
          </div>
          <button type="button" aria-label="Delete" className="text-[var(--taali-muted)] hover:text-[var(--taali-danger)]" onClick={() => offerTemplatesApi.remove(t.id).then(reload)}><Trash2 size={16} /></button>
        </Card>
      ))}
    </div>
  );
};

// --- Compliance ---------------------------------------------------------
const ComplianceSection = () => {
  const [requests, setRequests] = useState(null);
  const [eeo, setEeo] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({ request_type: 'access', subject_email: '' });
  const [exportView, setExportView] = useState(null);

  const reload = () => Promise.all([complianceApi.listRequests(), complianceApi.eeoReport()])
    .then(([reqs, report]) => { setRequests(reqs); setEeo(report); });
  useEffect(() => { reload().catch(() => setError('Failed to load compliance data.')); }, []);

  const create = async () => {
    setError(null);
    try {
      await complianceApi.createRequest({ request_type: form.request_type, subject_email: form.subject_email.trim() || null });
      setForm({ request_type: 'access', subject_email: '' });
      await reload();
    } catch { setError('Could not create the request (email or candidate required).'); }
  };

  const fulfill = async (id) => {
    setError(null);
    try {
      const res = await complianceApi.fulfillRequest(id);
      if (res?.export) setExportView(res.export);
      await reload();
    } catch { setError('Could not fulfil the request.'); }
  };

  if (requests === null && !error) return <div className="flex justify-center py-10"><Spinner /></div>;

  return (
    <div className="space-y-5">
      <ErrorNote>{error}</ErrorNote>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-[var(--taali-text)]">Data-subject requests</h3>
        <Card className="px-4 py-4">
          <div className="flex flex-wrap items-end gap-3">
            <label className="block w-40"><span className="mb-1 block text-xs text-[var(--taali-muted)]">Type</span>
              <Select value={form.request_type} onChange={(e) => setForm((f) => ({ ...f, request_type: e.target.value }))}>
                <option value="access">Access (export)</option>
                <option value="erasure">Erasure</option>
              </Select></label>
            <label className="block flex-1 min-w-[200px]"><span className="mb-1 block text-xs text-[var(--taali-muted)]">Subject email</span>
              <Input value={form.subject_email} onChange={(e) => setForm((f) => ({ ...f, subject_email: e.target.value }))} placeholder="person@example.com" /></label>
            <Button variant="primary" disabled={!form.subject_email.trim()} onClick={create}>Log request</Button>
          </div>
        </Card>

        <div className="mt-3 space-y-2">
          {(requests || []).length === 0 ? (
            <EmptyState title="No requests" description="Logged access/erasure requests appear here." className="py-6" />
          ) : requests.map((r) => (
            <Card key={r.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div className="min-w-0">
                <div className="truncate text-sm text-[var(--taali-text)]">{r.subject_email || `candidate #${r.candidate_id}`}</div>
                <div className="mt-0.5 text-xs text-[var(--taali-muted)]">{r.request_type}</div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Badge variant={r.status === 'completed' ? 'success' : r.status === 'rejected' ? 'danger' : 'warning'}>{r.status}</Badge>
                {r.status === 'pending' ? (
                  <>
                    <Button variant="ghost" size="xs" onClick={() => fulfill(r.id)}>Fulfil</Button>
                    <Button variant="ghost" size="xs" onClick={() => complianceApi.rejectRequest(r.id, 'identity not verified').then(reload)}>Reject</Button>
                  </>
                ) : null}
              </div>
            </Card>
          ))}
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-[var(--taali-text)]">
          EEO self-ID <span className="text-[var(--taali-muted)]">(aggregate — never per-candidate)</span>
        </h3>
        {!eeo || eeo.total === 0 ? (
          <EmptyState title="No EEO responses" description="Voluntary self-ID counts appear here once collected." className="py-6" />
        ) : (
          <Card className="px-4 py-3 text-sm text-[var(--taali-text)]">
            <div className="mb-2">{eeo.total} responses · {eeo.declined_count} declined</div>
            {['gender', 'race_ethnicity', 'veteran_status', 'disability_status'].map((cat) => (
              Object.keys(eeo[cat] || {}).length ? (
                <div key={cat} className="mt-1">
                  <span className="text-xs uppercase text-[var(--taali-muted)]">{cat.replace(/_/g, ' ')}: </span>
                  {Object.entries(eeo[cat]).map(([k, v]) => <Badge key={k} variant="muted" className="ml-1">{k}: {v}</Badge>)}
                </div>
              ) : null
            ))}
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

const TABS = [
  { id: 'webhooks', label: 'Webhooks' },
  { id: 'templates', label: 'Offer templates' },
  { id: 'compliance', label: 'Compliance' },
];

export const AtsAdminPage = ({ onNavigate, NavComponent }) => {
  const [tab, setTab] = useState('webhooks');
  return (
    <>
      {NavComponent ? <NavComponent currentPage="settings" onNavigate={onNavigate} /> : null}
      <PageContainer>
        <PageHeader title="ATS admin" subtitle="Webhooks, offer templates, and compliance." />
        <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
        <div className="mt-4">
          {tab === 'webhooks' ? <WebhooksSection /> : null}
          {tab === 'templates' ? <OfferTemplatesSection /> : null}
          {tab === 'compliance' ? <ComplianceSection /> : null}
        </div>
      </PageContainer>
    </>
  );
};

export default AtsAdminPage;
