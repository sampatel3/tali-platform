import React from 'react';

const fmtDate = (v) => {
  if (!v) return null;
  try {
    return new Date(v).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  } catch {
    return null;
  }
};

/**
 * Score provenance line shown under a candidate score everywhere:
 * "Scored 14 Jun 2026 · v2.1.0 · Sonnet".
 *
 * Fed by `application.score_summary.score_provenance`
 * ({ engine_version, scored_at, model }). A legacy/stale engine version
 * (1.x) renders muted so a recruiter can spot a candidate that needs
 * re-scoring at a glance.
 *
 * `density`:
 *   - 'full'    (default): date + version pill + model  (hero / detail cards)
 *   - 'compact': date + version pill                    (medium surfaces)
 *   - 'pill'    : version pill only                      (list rows / chips)
 */
export function ScoreProvenance({ provenance, density = 'full', className = '' }) {
  if (!provenance) return null;
  const { engine_version: version, scored_at: scoredAt, model } = provenance;
  if (!version && !scoredAt) return null;

  const date = fmtDate(scoredAt);
  const isStale = Boolean(version) && /^1\./.test(version);
  const dot = <span aria-hidden="true" className="text-[var(--taali-border)]">·</span>;

  return (
    <span
      className={`inline-flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px] leading-tight text-[var(--taali-muted)] ${className}`}
    >
      {date && density !== 'pill' ? (
        <span>{density === 'compact' ? date : `Scored ${date}`}</span>
      ) : null}
      {version ? (
        <>
          {date && density !== 'pill' ? dot : null}
          <span
            title={[
              isStale
                ? `Scored by an older engine (v${version}) — may need re-scoring`
                : `Scoring engine v${version}`,
              model ? `model: ${model}` : null,
            ]
              .filter(Boolean)
              .join(' · ')}
            className={`rounded px-1.5 py-px font-mono text-[10px] ${
              isStale
                ? 'bg-[var(--taali-surface-muted)] text-[var(--taali-muted)]'
                : 'bg-[var(--taali-purple-soft)] text-[var(--taali-purple)]'
            }`}
          >
            v{version}
          </span>
        </>
      ) : null}
    </span>
  );
}

export default ScoreProvenance;
