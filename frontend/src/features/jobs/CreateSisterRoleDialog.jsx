import React, { useEffect, useState } from 'react';

import { Button, Dialog, Spinner } from '../../shared/ui/TaaliPrimitives';
import {
  isRelatedRolePaidAuthorizationError,
  relatedRolePublishAuthorization,
} from '../../shared/relatedRoles/paidWorkAuthorization';
import { getErrorMessage } from '../candidates/candidatesUiUtils';
import { atsProviderLabel, roleAtsProvider } from './atsType';

export function CreateSisterRoleDialog({ open, sourceRole, rolesApi, onClose, onCreated }) {
  const [name, setName] = useState('');
  const [jobSpecText, setJobSpecText] = useState('');
  const [preview, setPreview] = useState(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [monthlyBudgetDollars, setMonthlyBudgetDollars] = useState('');
  const sourceProviderLabel = atsProviderLabel(roleAtsProvider(sourceRole));

  useEffect(() => {
    if (!open || !sourceRole?.id) return undefined;
    setName(`${sourceRole.name || 'Role'} · Related`);
    setJobSpecText(sourceRole.job_spec_text || '');
    setError('');
    setPreview(null);
    let cancelled = false;
    setLoadingPreview(true);
    rolesApi.previewSister(sourceRole.id)
      .then((res) => {
        if (cancelled) return;
        const next = res?.data || null;
        setPreview(next);
        setMonthlyBudgetDollars(
          next?.proposed_monthly_budget_cents
            ? (Number(next.proposed_monthly_budget_cents) / 100).toFixed(2)
            : '',
        );
      })
      .catch((err) => { if (!cancelled) setError(getErrorMessage(err, 'Could not load the candidate roster.')); })
      .finally(() => { if (!cancelled) setLoadingPreview(false); });
    return () => { cancelled = true; };
  }, [open, rolesApi, sourceRole]);

  const previewSource = preview ? {
    id: preview.source_role_id,
    name: preview.source_role_name,
    version: preview.source_role_version,
  } : sourceRole;
  const paidAuthorization = relatedRolePublishAuthorization(
    previewSource,
    preview,
    monthlyBudgetDollars,
  );
  const canSubmit = Boolean(
    name.trim().length > 0
    && jobSpecText.trim().length >= 80
    && paidAuthorization
    && !saving,
  );
  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!canSubmit) return;
    setSaving(true);
    setError('');
    try {
      const res = await rolesApi.createSister(sourceRole.id, {
        name: name.trim(),
        job_spec_text: jobSpecText.trim(),
        related_role_authorization: paidAuthorization.request,
      });
      onCreated?.(res?.data?.role);
    } catch (err) {
      if (isRelatedRolePaidAuthorizationError(err)) {
        try {
          const refreshed = await rolesApi.previewSister(sourceRole.id);
          setPreview(refreshed?.data || null);
          setError(
            'The source role, candidate roster, or workspace cap changed. '
            + 'Nothing was created; review the refreshed scope and confirm again.',
          );
        } catch (refreshError) {
          setError(getErrorMessage(
            refreshError,
            'The paid-work scope changed and could not be refreshed.',
          ));
        }
      } else {
        setError(getErrorMessage(err, 'Failed to create the related role.'));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={saving ? () => {} : onClose}
      title="Create a related role"
      description={`Create a new Taali scoring view over ${sourceRole?.name || `this ${sourceProviderLabel} role`}. Candidate stages and actions stay coupled to the original ${sourceProviderLabel} job.`}
      panelClassName="max-w-3xl"
      footer={(
        <div className="flex items-center justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button type="submit" form="create-sister-role-form" variant="primary" disabled={!canSubmit}>
            {saving ? <><Spinner size={13} /> Creating and queueing scores…</> : 'Create and score candidates'}
          </Button>
        </div>
      )}
    >
      <form id="create-sister-role-form" onSubmit={handleSubmit} className="space-y-4">
        <label className="block text-sm font-medium">
          Role name
          <input
            className="mt-1 w-full rounded-lg border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] px-3 py-2"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={200}
            autoFocus
          />
        </label>
        <label className="block text-sm font-medium">
          Updated job specification
          <textarea
            className="mt-1 min-h-72 w-full resize-y rounded-lg border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] px-3 py-2 font-mono text-xs leading-5"
            value={jobSpecText}
            onChange={(event) => setJobSpecText(event.target.value)}
            placeholder="Paste the complete updated job specification…"
          />
          <span className="mt-1 block text-xs text-[var(--taali-muted)]">
            Use the complete spec, not only the differences. This text becomes the scoring rubric for the related role.
          </span>
        </label>
        <div className="rounded-xl border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-4 text-sm">
          {loadingPreview ? (
            <span className="inline-flex items-center gap-2"><Spinner size={13} /> Checking the shared roster…</span>
          ) : preview ? (
            <div className="space-y-1">
              <div><strong>{preview.candidates_total}</strong> candidates will appear in the related role.</div>
              <div><strong>{preview.candidates_scoreable ?? preview.candidates_with_cv}</strong> have CV text and will be scored now.</div>
              <div><strong>{preview.candidates_unscorable ?? preview.candidates_missing_cv}</strong> without CV text will show as “Not scorable”.</div>
              <div><strong>{preview.candidates_excluded ?? 0}</strong> closed applications are excluded from scoring and cost.</div>
              <div>
                Initial AI usage is about <strong>${Number(preview.estimated_cost_usd || 0).toFixed(2)}</strong>;
                the current roster needs a cap of at least <strong>${(Number(preview.minimum_initial_budget_cents || 0) / 100).toFixed(2)}</strong>.
              </div>
              <label className="block pt-2 text-sm font-medium">
                Monthly scoring cap (USD)
                <input
                  aria-label="Monthly scoring cap (USD)"
                  className="mt-1 w-40 rounded-lg border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] px-3 py-2"
                  inputMode="decimal"
                  value={monthlyBudgetDollars}
                  onChange={(event) => setMonthlyBudgetDollars(event.target.value)}
                />
              </label>
              <div className="pt-1 text-xs text-[var(--taali-muted)]">
                These are full holistic evaluations. Each future scoreable ATS application costs about ${preview.ongoing_score_cost_usd ?? 0.083} and is scored automatically only while this related role remains within its own monthly cap. Identical CV/spec pairs reuse Taali’s score cache.
              </div>
              {!paidAuthorization ? (
                <div role="alert" className="pt-1 text-xs text-[var(--taali-danger)]">
                  Choose a valid cap that covers the current initial scoreable roster.
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
        {error ? <div role="alert" className="text-sm text-[var(--taali-danger)]">{error}</div> : null}
      </form>
    </Dialog>
  );
}

export default CreateSisterRoleDialog;
