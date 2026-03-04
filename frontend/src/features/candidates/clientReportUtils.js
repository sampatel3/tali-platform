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
