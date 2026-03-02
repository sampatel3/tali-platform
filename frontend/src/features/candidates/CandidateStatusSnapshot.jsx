import React from 'react';

import { Panel } from '../../shared/ui/TaaliPrimitives';
import {
  buildApplicationStatusMeta,
  formatStatusLabel,
} from './candidatesUiUtils';

const InfoCard = ({ label, value }) => (
  <div className="border border-[var(--taali-border-muted)] bg-[var(--taali-surface-subtle)] px-3 py-3">
    <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">{label}</p>
    <p className="mt-2 text-sm font-semibold text-[var(--taali-text)]">{value}</p>
  </div>
);

const resolveAssessmentStatus = (application) => {
  const rawStatus = application?.score_summary?.assessment_status || application?.valid_assessment_status || '';
  if (!String(rawStatus || '').trim()) return 'Not started';
  return formatStatusLabel(rawStatus);
};

const resolveCvStatus = (application) => (
  application?.cv_filename || application?.cv_text ? 'Uploaded' : 'Missing'
);

export function CandidateStatusSnapshot({ application, title = 'General status' }) {
  const items = [
    ...buildApplicationStatusMeta(application?.status, application?.workable_stage),
    { label: 'Assessment status', value: resolveAssessmentStatus(application) },
    { label: 'CV status', value: resolveCvStatus(application) },
  ];

  return (
    <Panel className="p-3.5">
      <p className="mb-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">{title}</p>
      <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
        {items.map((item) => (
          <InfoCard key={item.label} label={item.label} value={item.value} />
        ))}
      </div>
    </Panel>
  );
}
