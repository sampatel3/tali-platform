import React from 'react';
import { Link2 } from 'lucide-react';

import {
  m,
  motionTransition,
  useReducedMotionSync,
} from '../../shared/motion';

export const roleReferenceLabel = (reference) => {
  const name = String(reference?.name || '').trim();
  const id = Number(reference?.id || 0);
  if (name && id) return `${name} #${id}`;
  return null;
};

export const roleFamilyOwner = (role) => (
  role?.role_family?.owner
  || (role?.role_kind === 'sister' ? {
    id: role?.ats_owner_role_id,
    name: role?.ats_owner_role_name,
  } : { id: role?.id, name: role?.name })
);

export const roleFamilyReferences = (role) => {
  // Only enumerate a family when the API supplied the complete contract. A
  // sister's owner/current-role fallbacks are useful for navigation, but are
  // not proof that no additional related roles share the same application.
  const owner = role?.role_family?.owner;
  const related = Array.isArray(role?.role_family?.related)
    ? [...role.role_family.related]
    : [];
  if (!roleReferenceLabel(owner)
    || related.length === 0
    || related.some((reference) => !roleReferenceLabel(reference))) return [];
  const seen = new Set();
  return [owner, ...related].filter((reference) => {
    const id = Number(reference?.id || 0);
    if (!id || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
};

export const RoleFamilyHeaderNote = ({ role, providerLabel }) => {
  const references = roleFamilyReferences(role);
  const owner = roleFamilyOwner(role);
  const ownerLabel = roleReferenceLabel(owner);
  const isShared = references.length > 1
    || role?.role_kind === 'sister'
    || Number(role?.sister_role_count || 0) > 0;
  if (!isShared) return null;

  return (
    <span className="related-role-header-note" role="note">
      <Link2 size={13} strokeWidth={2.2} aria-hidden="true" />
      <span>
        Shared {providerLabel} candidate pool
        {role?.role_kind === 'sister' && ownerLabel ? ` with ${ownerLabel} (original)` : ''}.
        {' '}Rejecting and advancing apply to {references.length > 1
          ? references.map(roleReferenceLabel).filter(Boolean).join(', ')
          : 'the original and every related role'}.
      </span>
    </span>
  );
};

export const OriginalRoleButton = ({ owner, onOpen }) => {
  const reduced = useReducedMotionSync();
  const label = roleReferenceLabel(owner);
  if (!owner?.id || !label) return null;

  return (
    <m.button
      type="button"
      className="btn btn-outline btn-sm related-role-origin-button"
      initial={reduced ? false : {
        scale: 1,
        boxShadow: '0 0 0 0 rgba(106, 70, 190, 0)',
      }}
      animate={reduced ? undefined : {
        scale: [1, 1.025, 1],
        boxShadow: [
          '0 0 0 0 rgba(106, 70, 190, 0)',
          '0 0 0 6px rgba(106, 70, 190, 0.22)',
          '0 0 0 0 rgba(106, 70, 190, 0)',
        ],
      }}
      transition={reduced ? motionTransition.instant : {
        duration: 1.15,
        times: [0, 0.45, 1],
        repeat: 1,
        repeatDelay: 0.22,
      }}
      onClick={onOpen}
      title={`Open original role ${label}`}
      aria-label={`Open original role ${label}`}
      data-motion-role-origin={reduced ? 'static' : 'two-beat'}
    >
      <Link2 size={12} strokeWidth={2.2} aria-hidden="true" />
      <span className="related-role-origin-label">Original: {label}</span>
    </m.button>
  );
};
