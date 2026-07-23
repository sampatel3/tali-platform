// `/applications` can expose the same physical ATS application once per
// logical role membership. Keep those identities separate in UI state while
// still using the physical application id for report/evidence/action APIs.
export const applicationPhysicalId = (application) => (
  application?.application_id ?? application?.id ?? null
);

export const applicationLogicalRoleId = (application) => (
  application?.logical_role_id ?? application?.role_id ?? null
);

export const applicationLogicalMembershipId = (application) => {
  const explicit = application?.logical_membership_id;
  if (explicit != null && String(explicit).trim()) return String(explicit);

  const applicationId = applicationPhysicalId(application);
  const roleId = applicationLogicalRoleId(application);
  if (applicationId == null) return null;
  return roleId == null
    ? `application:${applicationId}`
    : `${roleId}:${applicationId}`;
};
