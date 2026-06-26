import React from 'react';

import { SCORING_METRIC_GLOSSARY, SCORING_METRIC_GROUPS } from '../../lib/scoringGlossary';
import { Card, cx } from './TaaliPrimitives';

export const SCORING_GLOSSARY_METRIC_COUNT = Object.keys(SCORING_METRIC_GLOSSARY).length;

// The ~30 heuristic metrics, organised as EVIDENCE under the 5 canonical
// scorecard axes (the 4 Ds + Deliverable) rather than as a flat list.
export const ScoringGlossaryPanel = ({ className = '' }) => (
  <div className={cx('grid gap-4', className)}>
    {SCORING_METRIC_GROUPS.filter((group) => group.metrics.length > 0).map((group) => (
      <div key={group.key}>
        <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]" title={group.blurb}>
          {group.label}
        </div>
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          {group.metrics.map((meta) => (
            <Card key={meta.key} className="p-3">
              <div className="font-mono text-xs font-bold text-[var(--taali-text)]">{meta.label}</div>
              <div className="mt-1 text-xs text-[var(--taali-muted)]">{meta.description}</div>
            </Card>
          ))}
        </div>
      </div>
    ))}
  </div>
);
