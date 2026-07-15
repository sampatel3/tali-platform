export const roleExpectedVersion = (role) => (
  Number.isInteger(role?.version) && role.version >= 1 ? role.version : 1
);

export const versionedRolePayload = (role, payload) => ({
  ...payload,
  expected_version: roleExpectedVersion(role),
});

export const roleVersionConflict = (error) => {
  const detail = error?.response?.data?.detail;
  if (error?.response?.status !== 409 || detail?.code !== 'ROLE_VERSION_CONFLICT') return null;
  return {
    message: detail.message,
    currentRole: detail.current_role,
    currentVersion: detail.current_version,
    changedBy: detail.changed_by,
  };
};

export const conflictActorLabel = (changedBy) => {
  if (typeof changedBy === 'string') return changedBy;
  return changedBy?.name || changedBy?.full_name || changedBy?.email || '';
};

export const reconcileRoleVersionConflict = (error, setRole, showToast) => {
  const conflict = roleVersionConflict(error);
  if (!conflict) return false;
  setRole((current) => {
    if (conflict.currentRole) return current
      ? { ...current, ...conflict.currentRole }
      : conflict.currentRole;
    return current && Number.isInteger(conflict.currentVersion)
      ? { ...current, version: conflict.currentVersion }
      : current;
  });
  const actorName = conflictActorLabel(conflict.changedBy);
  const actorCopy = actorName ? ` by ${actorName}` : '';
  showToast(
    `${conflict.message || `This job was changed${actorCopy} before your update was saved.`}${actorName && conflict.message ? ` Changed by ${actorName}.` : ''} Latest settings are shown; review them and try again.`,
    'error',
  );
  return true;
};
