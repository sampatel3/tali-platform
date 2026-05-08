import React from 'react';

/**
 * Pre-screen recommendation chip.
 * Maps the four backend recommendation strings to colour-coded badges.
 *
 * recommendation: "Strong match" | "Proceed to screening" |
 *                 "Manual review recommended" | "Below threshold" | null
 */
export function PreScreenChip({ recommendation, runAt = null, compact = false }) {
  const r = (recommendation || '').toLowerCase();
  let cls = 'ps-chip ps-chip--unrun';
  let label = 'Not pre-screened';
  if (r.startsWith('strong')) {
    cls = 'ps-chip ps-chip--strong';
    label = compact ? 'Strong' : 'Strong match';
  } else if (r.startsWith('proceed')) {
    cls = 'ps-chip ps-chip--proceed';
    label = compact ? 'Proceed' : 'Proceed';
  } else if (r.startsWith('manual')) {
    cls = 'ps-chip ps-chip--review';
    label = compact ? 'Review' : 'Manual review';
  } else if (r.startsWith('below')) {
    cls = 'ps-chip ps-chip--rejected';
    label = compact ? 'Rejected' : 'Below threshold';
  }
  const title = runAt ? `Pre-screen run: ${new Date(runAt).toLocaleString()}` : 'No pre-screen yet';
  return <span className={cls} title={title}>{label}</span>;
}

/**
 * Fraud / CV-plagiarism chip.
 * Reads `pre_screen_evidence.fraud_signals.cv_copy_paste` produced by the
 * pre-screen agent. Renders nothing when no signal is present or when the
 * detector did not trigger — we don't want to clutter clean rows.
 */
export function FraudChip({ application, compact = false }) {
  const signal = application?.pre_screen_evidence?.fraud_signals?.cv_copy_paste;
  if (!signal || !signal.triggered) return null;
  const pct = Math.round(Number(signal.score || 0) * 100);
  const title = (
    `CV plagiarism detected: ${pct}% of the CV text is copied verbatim from the `
    + `job description (threshold ${Math.round(Number(signal.threshold || 0) * 100)}%).`
  );
  return (
    <span className="ps-chip ps-chip--rejected" title={title}>
      {compact ? `Plagiarism · ${pct}%` : `Possible CV plagiarism · ${pct}%`}
    </span>
  );
}

/**
 * Graph sync status chip.
 * Reads `graph_synced_at` and `graph_stale` from the application/candidate row.
 */
export function GraphStatusChip({ syncedAt, stale = false, compact = false }) {
  if (!syncedAt) {
    return (
      <span className="graph-chip graph-chip--none" title="Not synced to knowledge graph">
        {compact ? '—' : 'Not in graph'}
      </span>
    );
  }
  if (stale) {
    const title = `CV updated since last graph sync (${new Date(syncedAt).toLocaleString()})`;
    return (
      <span className="graph-chip graph-chip--stale" title={title}>
        {compact ? 'Stale' : 'Graph stale'}
      </span>
    );
  }
  const title = `Synced to graph at ${new Date(syncedAt).toLocaleString()}`;
  return (
    <span className="graph-chip graph-chip--in" title={title}>
      {compact ? 'In graph' : 'In graph'}
    </span>
  );
}
