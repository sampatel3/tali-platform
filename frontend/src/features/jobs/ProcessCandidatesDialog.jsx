import React, { useEffect, useState } from 'react';

import * as apiClient from '../../shared/api';
import { Button, Dialog } from '../../shared/ui/TaaliPrimitives';

/**
 * ProcessCandidatesDialog
 *
 * One dialog to drive the cascade: fetch CVs → pre-screen → score.
 * Each step has a checkbox. Score has a 3-way radio (none / new / all).
 * "Refresh pre-screen" lives under an Advanced disclosure since it's
 * destructive (overrides existing pre-screen results).
 *
 * Counts come from the backend's dry_run preview, recomputed every time
 * the user toggles a step so the numbers reflect the actual cascade.
 *
 * Props:
 *   open       — boolean
 *   roleId     — number
 *   onClose    — () => void
 *   onConfirm  — async (body) => void.  body shape:
 *                  { fetch_cvs, pre_screen, refresh_pre_screen, score }
 */
export function ProcessCandidatesDialog({
  open,
  roleId,
  defaults,
  onClose,
  onConfirm,
}) {
  const rolesApi = apiClient.roles;

  const [opts, setOpts] = useState(() => ({
    fetch_cvs: true,
    pre_screen: true,
    refresh_pre_screen: false,
    score: 'new',
    sync_graph: false,
    refresh_graph: false,
    ...(defaults || {}),
  }));
  const [counts, setCounts] = useState(null);
  const [previewError, setPreviewError] = useState(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  // Reset when dialog opens.
  useEffect(() => {
    if (!open) return;
    setOpts({
      fetch_cvs: true,
      pre_screen: true,
      refresh_pre_screen: false,
      score: 'new',
      sync_graph: false,
      refresh_graph: false,
      ...(defaults || {}),
    });
    setSubmitting(false);
    setPreviewError(null);
    // counts will load via the next effect
  }, [open, defaults]);

  // Whenever opts change, refetch the dry-run preview. Debounced lightly so
  // toggling several boxes doesn't fire multiple requests at once.
  useEffect(() => {
    if (!open) return;
    setLoadingPreview(true);
    setPreviewError(null);
    const handle = setTimeout(async () => {
      try {
        const body = {
          fetch_cvs: !!opts.fetch_cvs,
          pre_screen: !!opts.pre_screen,
          refresh_pre_screen: !!opts.refresh_pre_screen,
          score: opts.score || 'none',
          sync_graph: !!opts.sync_graph,
          refresh_graph: !!opts.refresh_graph,
        };
        const res = await rolesApi.processRole(roleId, body, { dry_run: true });
        setCounts(res?.data ?? null);
      } catch (err) {
        setCounts(null);
        setPreviewError(err?.response?.data?.detail || err?.message || 'Preview failed.');
      } finally {
        setLoadingPreview(false);
      }
    }, 150);
    return () => clearTimeout(handle);
  }, [open, opts, roleId, rolesApi]);

  // If user picks Refresh pre-screen, force pre_screen on too (refresh implies running it).
  const setRefresh = (v) => {
    setOpts((s) => ({
      ...s,
      refresh_pre_screen: !!v,
      pre_screen: v ? true : s.pre_screen,
    }));
  };

  const fetchCount = Number(counts?.fetch_cvs?.will_attempt ?? 0);
  const fetchUnavailable = Number(counts?.fetch_cvs?.no_cv_no_workable ?? 0);
  const preScreenCount = Number(counts?.pre_screen?.will_run ?? 0);
  const scoreCount = Number(counts?.score?.will_run ?? 0);
  const graphSyncCount = Number(counts?.graph_sync?.will_run ?? 0);

  const willDoSomething = (
    (opts.fetch_cvs && fetchCount > 0)
    || ((opts.pre_screen || opts.refresh_pre_screen) && preScreenCount > 0)
    || (opts.score !== 'none' && scoreCount > 0)
    || (opts.sync_graph && graphSyncCount > 0)
  );

  const stepCount = (
    (opts.fetch_cvs ? 1 : 0)
    + (opts.pre_screen || opts.refresh_pre_screen ? 1 : 0)
    + (opts.score !== 'none' ? 1 : 0)
    + (opts.sync_graph ? 1 : 0)
  );

  const confirmLabel = (() => {
    if (submitting) return 'Starting…';
    if (loadingPreview) return 'Loading…';
    if (!willDoSomething) return 'Nothing to do';
    return `Run ${stepCount} step${stepCount === 1 ? '' : 's'}`;
  })();

  const handleConfirm = async () => {
    if (!willDoSomething) return;
    setSubmitting(true);
    try {
      await onConfirm?.({
        fetch_cvs: !!opts.fetch_cvs,
        pre_screen: !!opts.pre_screen,
        refresh_pre_screen: !!opts.refresh_pre_screen,
        score: opts.score || 'none',
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={submitting ? undefined : onClose}
      title="Process candidates"
      description="Pick which steps to run. They execute in order: fetch CVs → pre-screen → score."
      footer={(
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="primary"
            disabled={!willDoSomething || submitting || loadingPreview}
            onClick={handleConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      )}
    >
      <div className="process-dialog">
        {previewError ? (
          <div className="process-dialog__error" role="alert">{previewError}</div>
        ) : null}

        {/* ── Fetch CVs ──────────────────────────────────────────────── */}
        <label className="process-row">
          <input
            type="checkbox"
            checked={!!opts.fetch_cvs}
            onChange={(e) => setOpts((s) => ({ ...s, fetch_cvs: e.target.checked }))}
          />
          <div className="process-row__body">
            <div className="process-row__title">Fetch missing CVs from Workable</div>
            <div className="process-row__sub">
              {fetchCount > 0
                ? `${fetchCount} candidate${fetchCount === 1 ? '' : 's'} need a CV.`
                : 'No CVs are missing.'}
              {fetchUnavailable > 0 ? (
                <span className="process-row__warn">
                  {' '}{fetchUnavailable} candidate{fetchUnavailable === 1 ? '' : 's'} have no Workable record — these will be skipped.
                </span>
              ) : null}
            </div>
          </div>
          <div className="process-row__count">{fetchCount}</div>
        </label>

        {/* ── Pre-screen ─────────────────────────────────────────────── */}
        <label className="process-row">
          <input
            type="checkbox"
            checked={!!(opts.pre_screen || opts.refresh_pre_screen)}
            disabled={!!opts.refresh_pre_screen}
            onChange={(e) => setOpts((s) => ({ ...s, pre_screen: e.target.checked }))}
          />
          <div className="process-row__body">
            <div className="process-row__title">
              {opts.refresh_pre_screen ? 'Refresh pre-screen' : 'Pre-screen new candidates'}
            </div>
            <div className="process-row__sub">
              {preScreenCount > 0
                ? `${preScreenCount} candidate${preScreenCount === 1 ? '' : 's'}${opts.fetch_cvs && fetchCount > 0 ? ' (incl. those just fetched)' : ''}.`
                : 'Nothing to pre-screen.'}
            </div>
          </div>
          <div className="process-row__count">{preScreenCount}</div>
        </label>

        {/* ── Score ──────────────────────────────────────────────────── */}
        <fieldset className="process-row process-row--fieldset">
          <legend className="process-row__title">Score</legend>
          <div className="process-row__radios">
            <label>
              <input
                type="radio"
                name="score-mode"
                checked={opts.score === 'none'}
                onChange={() => setOpts((s) => ({ ...s, score: 'none' }))}
              />
              Don't score
            </label>
            <label>
              <input
                type="radio"
                name="score-mode"
                checked={opts.score === 'new'}
                onChange={() => setOpts((s) => ({ ...s, score: 'new' }))}
              />
              Score new only
              <span className="process-row__count process-row__count--inline">
                {opts.score === 'new' ? scoreCount : ''}
              </span>
            </label>
            <label>
              <input
                type="radio"
                name="score-mode"
                checked={opts.score === 'all'}
                onChange={() => setOpts((s) => ({ ...s, score: 'all' }))}
              />
              Re-score everyone (overwrites existing scores)
              <span className="process-row__count process-row__count--inline">
                {opts.score === 'all' ? scoreCount : ''}
              </span>
            </label>
          </div>
        </fieldset>

        {/* ── Advanced ───────────────────────────────────────────────── */}
        <button
          type="button"
          className="process-dialog__advanced-toggle"
          onClick={() => setAdvancedOpen((v) => !v)}
        >
          {advancedOpen ? '▾ Advanced' : '▸ Advanced'}
        </button>
        {advancedOpen ? (
          <div className="process-dialog__advanced">
            <label className="process-row">
              <input
                type="checkbox"
                checked={!!opts.refresh_pre_screen}
                onChange={(e) => setRefresh(e.target.checked)}
              />
              <div className="process-row__body">
                <div className="process-row__title">Refresh pre-screen</div>
                <div className="process-row__sub">
                  Re-runs pre-screen for every candidate with a CV, even ones already pre-screened.
                  Existing scores are kept — only the pre-screen result is overwritten.
                </div>
              </div>
            </label>
            {/* Graph sync — per-role: projects this job's candidates with a
                CV into the Graphiti / Neo4j knowledge graph. Skips candidates
                already synced (same CV) so it's safe to leave on. */}
            <label className="process-row">
              <input
                type="checkbox"
                checked={!!opts.sync_graph}
                onChange={(e) => setOpts((s) => ({ ...s, sync_graph: e.target.checked }))}
              />
              <div className="process-row__body">
                <div className="process-row__title">Sync candidates to knowledge graph</div>
                <div className="process-row__sub">
                  {opts.sync_graph && graphSyncCount === 0
                    ? 'All candidates with a CV on this role are already in the graph and up to date.'
                    : `Project this job's candidates with a CV into Neo4j via Graphiti. Skips candidates already synced (same CV).`}
                </div>
              </div>
              <div className="process-row__count">
                {opts.sync_graph ? graphSyncCount : ''}
              </div>
            </label>
            {opts.sync_graph ? (
              <label className="process-row" style={{ marginLeft: 24 }}>
                <input
                  type="checkbox"
                  checked={!!opts.refresh_graph}
                  onChange={(e) => setOpts((s) => ({ ...s, refresh_graph: e.target.checked }))}
                />
                <div className="process-row__body">
                  <div className="process-row__title">Force re-sync (ignore last_synced_at)</div>
                  <div className="process-row__sub">
                    Re-projects every candidate even if their CV hasn't changed since the last sync.
                    Use only when you suspect drift.
                  </div>
                </div>
              </label>
            ) : null}
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}

export default ProcessCandidatesDialog;
