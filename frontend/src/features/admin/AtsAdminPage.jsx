import React, { useEffect, useState } from 'react';
import { Trash2 } from 'lucide-react';

import { offerTemplates as offerTemplatesApi } from '../../shared/api';
import {
  Badge,
  Button,
  Card,
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

// Structured so more admin tabs (webhooks, compliance) can be added later.
const TABS = [{ id: 'templates', label: 'Offer templates' }];

export const AtsAdminPage = ({ onNavigate, NavComponent }) => {
  const [tab, setTab] = useState('templates');
  return (
    <>
      {NavComponent ? <NavComponent currentPage="settings" onNavigate={onNavigate} /> : null}
      <PageContainer>
        <PageHeader title="ATS admin" subtitle="Reusable offer templates for your workspace." />
        <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
        <div className="mt-4">{tab === 'templates' ? <OfferTemplatesSection /> : null}</div>
      </PageContainer>
    </>
  );
};

export default AtsAdminPage;
