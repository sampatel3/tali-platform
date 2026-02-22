import React from 'react';

import { SCORING_METRIC_GLOSSARY } from '../../lib/scoringGlossary';
import { Card, cx } from './TaaliPrimitives';

export const SCORING_GLOSSARY_METRIC_COUNT = Object.keys(SCORING_METRIC_GLOSSARY).length;

export const ScoringGlossaryPanel = ({ className = '' }) => {
  const entries = Object.entries(SCORING_METRIC_GLOSSARY);
  return (
    <div className={cx('grid gap-2 md:grid-cols-2', className)}>
      {entries.map(([key, meta]) => (
        <Card key={key} className="p-3">
          <div className="font-mono text-xs font-bold text-[var(--taali-text)]">{meta.label}</div>
          <div className="mt-1 text-xs text-[var(--taali-muted)]">{meta.description}</div>
        </Card>
      ))}
    </div>
  );
};

