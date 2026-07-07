import React, { useEffect, useState } from 'react';

import { offers as offersApi } from '../../shared/api';
import {
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  Input,
  Select,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';

// Allowed next states per current status (mirrors the backend state machine).
const NEXT = {
  draft: ['pending_approval', 'sent', 'deprecated'],
  pending_approval: ['approved', 'draft', 'deprecated'],
  approved: ['sent', 'deprecated'],
  sent: ['accepted', 'declined', 'expired', 'deprecated'],
  expired: ['sent', 'deprecated'],
};
const statusVariant = (s) => (s === 'accepted' ? 'success' : s === 'declined' || s === 'expired' ? 'danger' : s === 'sent' ? 'info' : 'muted');

export const OfferPanel = ({ applicationId }) => {
  const [list, setList] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ base_salary_amount: '', currency: 'AED', pay_frequency: 'year' });
  const [payloadView, setPayloadView] = useState(null); // { title, data }

  const reload = () => offersApi.listForApplication(applicationId).then(setList);

  useEffect(() => {
    let cancelled = false;
    reload().catch(() => { if (!cancelled) setError('Failed to load offers.'); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applicationId]);

  const createOffer = async () => {
    setBusy(true);
    setError(null);
    try {
      await offersApi.create(applicationId, {
        base_salary_amount: form.base_salary_amount === '' ? null : Number(form.base_salary_amount),
        currency: form.currency || null,
        pay_frequency: form.pay_frequency || null,
      });
      await reload();
    } catch {
      setError('Could not create the offer.');
    } finally {
      setBusy(false);
    }
  };

  const transition = async (offerId, status) => {
    setError(null);
    try {
      await offersApi.transition(offerId, status);
      await reload();
    } catch (err) {
      setError(err?.response?.status === 409 ? 'That transition is not allowed.' : 'Could not update the offer.');
    }
  };

  const showPayload = async (offerId, kind) => {
    try {
      const data = kind === 'hris' ? await offersApi.hrisExport(offerId) : await offersApi.esignRequest(offerId);
      setPayloadView({ title: kind === 'hris' ? 'HRIS export' : 'E-sign request', data });
    } catch {
      setError('Could not load that payload.');
    }
  };

  if (list === null && !error) return <div className="flex justify-center py-10"><Spinner /></div>;

  return (
    <div className="space-y-4 py-2">
      {error ? (
        <Card className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] px-3 py-2 text-sm text-[var(--taali-danger)]">{error}</Card>
      ) : null}

      <Card className="px-4 py-4">
        <h3 className="text-sm font-semibold text-[var(--taali-text)]">New offer</h3>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="mb-1 block text-xs text-[var(--taali-muted)]">Base salary</span>
            <Input type="number" value={form.base_salary_amount} onChange={(e) => setForm((f) => ({ ...f, base_salary_amount: e.target.value }))} placeholder="0" />
          </label>
          <label className="block w-28">
            <span className="mb-1 block text-xs text-[var(--taali-muted)]">Currency</span>
            <Input value={form.currency} onChange={(e) => setForm((f) => ({ ...f, currency: e.target.value }))} />
          </label>
          <label className="block w-32">
            <span className="mb-1 block text-xs text-[var(--taali-muted)]">Frequency</span>
            <Select value={form.pay_frequency} onChange={(e) => setForm((f) => ({ ...f, pay_frequency: e.target.value }))}>
              <option value="year">Year</option>
              <option value="month">Month</option>
              <option value="hour">Hour</option>
            </Select>
          </label>
          <Button variant="primary" disabled={busy} onClick={createOffer}>Create draft</Button>
        </div>
      </Card>

      {(list || []).length === 0 ? (
        <EmptyState title="No offers yet" description="Create a draft offer to start the offer flow." className="py-8" />
      ) : (
        <div className="space-y-2">
          {list.map((o) => (
            <Card key={o.id} className="px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm font-medium text-[var(--taali-text)]">
                  v{o.version} · {o.currency || ''} {o.base_salary_amount != null ? o.base_salary_amount.toLocaleString() : '—'}
                  {o.pay_frequency ? <span className="text-[var(--taali-muted)]"> / {o.pay_frequency}</span> : null}
                </div>
                <Badge variant={statusVariant(o.status)}>{o.status}</Badge>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {(NEXT[o.status] || []).map((next) => (
                  <Button key={next} variant="ghost" size="xs" onClick={() => transition(o.id, next)}>{next.replace(/_/g, ' ')}</Button>
                ))}
                <span className="mx-1 w-px self-stretch bg-[var(--taali-border-soft)]" />
                <Button variant="ghost" size="xs" onClick={() => showPayload(o.id, 'hris')}>HRIS payload</Button>
                <Button variant="ghost" size="xs" onClick={() => showPayload(o.id, 'esign')}>E-sign payload</Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={Boolean(payloadView)} onClose={() => setPayloadView(null)} title={payloadView?.title || ''}>
        <pre className="max-h-[60vh] overflow-auto rounded-[var(--taali-radius-control)] bg-[var(--taali-surface-subtle)] p-3 text-xs text-[var(--taali-text)]">
          {payloadView ? JSON.stringify(payloadView.data, null, 2) : ''}
        </pre>
      </Dialog>
    </div>
  );
};

export default OfferPanel;
