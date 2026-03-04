const sanitizeFilePart = (value, fallback) => {
  const cleaned = String(value || '')
    .replace(/[\\/:*?"<>|]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\.+$/g, '');

  return cleaned || fallback;
};

export const buildClientReportFilenameStem = (roleName, candidateName) => (
  `${sanitizeFilePart(roleName, 'Role')}-${sanitizeFilePart(candidateName, 'Candidate')}`
);

export const buildAssessmentReportIdentity = (assessment) => {
  const completedLabel = assessment?.completed_at
    ? new Date(assessment.completed_at).toLocaleDateString()
    : '';
  const durationMinutes = Number(assessment?.total_duration_seconds);

  return {
    sectionLabel: 'Assessment results',
    name: assessment?.candidate_name || assessment?.candidate?.full_name || assessment?.candidate_email || 'Candidate',
    email: assessment?.candidate_email || assessment?.candidate?.email || '',
    position: assessment?.candidate?.position || assessment?.candidate_position || '',
    taskName: assessment?.task_name || assessment?.task?.name || '',
    roleName: assessment?.role_name || assessment?.role?.name || '',
    applicationStatus: assessment?.application_status || '',
    durationLabel: Number.isFinite(durationMinutes) && durationMinutes > 0
      ? `${Math.round(durationMinutes / 60)}m`
      : '—',
    completedLabel,
  };
};
