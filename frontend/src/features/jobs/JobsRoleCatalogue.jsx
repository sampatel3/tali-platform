import { useEffect, useMemo, useState } from 'react';
import {
  Archive,
  Building2,
  ChevronDown,
  Inbox,
  Link2,
  Pause,
  RefreshCw,
  Sparkles,
  Star,
} from 'lucide-react';

import { formatCount, inPipelineFromStageCounts } from '../../shared/metrics';
import {
  AgentLoop,
  MotionDisclosure,
  MotionLoop,
  m,
} from '../../shared/motion';
import { formatRelativeDateTime } from '../../shared/ui/RecruiterDesignPrimitives';
import {
  atsProviderLabel,
  AtsTypeTag,
  effectiveNativeJobStatus,
  roleAtsProvider,
  roleExternalJobLive,
  roleExternalJobState,
} from './atsType';
import { isRoleDimmed, JobsRoleGrid } from './JobsRoleGrid';
import { roleReferenceLabel } from './RoleFamilyHeaderUi';

const JOB_STATUS_META = {
  draft: { label: 'Draft', tone: 'draft' },
  open: { label: 'Open', tone: 'open' },
  filled: { label: 'Filled', tone: 'filled' },
  filled_external: { label: 'Filled · external', tone: 'ext' },
  cancelled: { label: 'Archived', tone: 'cancelled' },
};

// Keep every visible member of a shared ATS application beside its original
// role. The API supplies the complete family contract; relationship fields are
// retained as a compatibility fallback for cached and showcase payloads.
export const buildRoleFamilyCatalogue = (roles = []) => {
  const sourceRoles = [...roles];
  const rolesById = new Map(sourceRoles.map((role) => [Number(role?.id), role]));
  const groups = new Map();

  sourceRoles.forEach((role, sourceIndex) => {
    const payloadOwner = role?.role_family?.owner;
    const ownerId = Number(
      payloadOwner?.id
      || (role?.role_kind === 'sister' ? role?.ats_owner_role_id : role?.id)
      || role?.id,
    );
    const key = ownerId ? `family-${ownerId}` : `role-${role?.id ?? sourceIndex}`;
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        ownerId,
        firstIndex: sourceIndex,
        visibleRoles: [],
        references: new Map(),
        declaredRelatedCount: 0,
      });
    }
    const group = groups.get(key);
    group.visibleRoles.push(role);
    group.declaredRelatedCount = Math.max(
      group.declaredRelatedCount,
      Number(role?.sister_role_count || 0),
    );

    const loadedOwner = rolesById.get(ownerId);
    const ownerReference = payloadOwner
      || (loadedOwner ? { id: loadedOwner.id, name: loadedOwner.name } : null)
      || (role?.ats_owner_role_id ? {
        id: role.ats_owner_role_id,
        name: role.ats_owner_role_name,
      } : null)
      || { id: role?.id, name: role?.name };
    if (ownerReference?.id) group.references.set(Number(ownerReference.id), ownerReference);
    (role?.role_family?.related || []).forEach((reference) => {
      if (reference?.id) group.references.set(Number(reference.id), reference);
    });
    if (role?.id) group.references.set(Number(role.id), { id: role.id, name: role.name });
  });

  const orderedRoles = [];
  const orderedGroups = [];
  const familyByRoleId = new Map();
  [...groups.values()]
    .sort((left, right) => left.firstIndex - right.firstIndex)
    .forEach((group) => {
      const owner = group.references.get(group.ownerId)
        || { id: group.ownerId, name: rolesById.get(group.ownerId)?.name };
      const related = [...group.references.values()]
        .filter((reference) => Number(reference?.id) !== group.ownerId);
      const isLinked = related.length > 0
        || group.declaredRelatedCount > 0
        || group.visibleRoles.some((role) => role?.role_kind === 'sister');
      const context = { owner, related, isLinked };
      // The server supplies one deterministic agent-first/name sequence for
      // every bounded page. Keep it byte-for-byte so later pages only append.
      const visibleRoles = [...group.visibleRoles];
      const startIndex = orderedRoles.length;
      visibleRoles.forEach((role) => {
        orderedRoles.push(role);
        familyByRoleId.set(Number(role?.id), context);
      });
      orderedGroups.push({
        key: group.key,
        ownerId: group.ownerId,
        visibleRoles,
        context,
        startIndex,
      });
    });

  return { roles: orderedRoles, groups: orderedGroups, familyByRoleId };
};

export const partitionRolesAlphabetically = (roles) => {
  const catalogue = buildRoleFamilyCatalogue(roles);
  const activeRoles = [];
  const inactiveRoles = [];
  catalogue.roles.forEach((role) => {
    (isRoleDimmed(role) ? inactiveRoles : activeRoles).push(role);
  });
  return { activeRoles, inactiveRoles };
};

const inactiveRoleStatus = (role) => {
  if (!roleAtsProvider(role)) {
    return JOB_STATUS_META[effectiveNativeJobStatus(role)]
      || { label: 'Inactive', tone: 'cancelled' };
  }
  const externalStatus = roleExternalJobState(role);
  const externalLive = roleExternalJobLive(role);
  const formattedStatus = String(externalStatus || '')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
  return {
    label: {
      archived: 'Archived',
      cancelled: 'Cancelled',
      closed: 'Closed',
      draft: 'Draft',
      filled: 'Filled',
      inactive: 'Inactive',
    }[externalStatus] || formattedStatus || (externalLive === true ? 'Open' : 'Status pending sync'),
    tone: externalLive === true ? 'open'
      : externalStatus === 'draft' ? 'draft'
        : externalStatus === 'filled' ? 'filled' : 'cancelled',
  };
};

const CompactAgentStatus = ({ agentLive, role, workspacePaused }) => {
  const enabled = Boolean(role?.agentic_mode_enabled);
  const paused = enabled && Boolean(role?.agent_paused_at);
  const held = enabled && !paused && workspacePaused;
  const intent = role?.assessment_task_provisioning?.activation_intent;
  const intentStatus = String(intent?.status || '');
  const queued = !enabled && ['pending', 'retry_wait'].includes(intentStatus);
  const blocked = !enabled && intentStatus === 'blocked';
  const budget = Number(
    agentLive?.monthly_budget_cents ?? role?.monthly_usd_budget_cents ?? 0,
  ) / 100;
  const spent = agentLive ? Number(agentLive.monthly_spent_cents || 0) / 100 : null;

  if (paused) {
    return (
      <span className="job-agent-pill is-paused" title={budget > 0 ? `Agent paused · cap $${Math.round(budget)}` : 'Agent paused'}>
        <Pause size={10} aria-hidden="true" /> PAUSED
      </span>
    );
  }
  if (held) {
    return (
      <span className="job-agent-pill is-held" title="Workspace agent paused · this role remains on and will resume automatically">
        <Pause size={10} aria-hidden="true" /> ON · HELD
      </span>
    );
  }
  if (enabled) {
    return (
      <AgentLoop kind="flow" className="job-agent-pill is-on" title="Agent on for this role">
        <Sparkles size={10} aria-hidden="true" />
        {spent != null && budget > 0
          ? `ON · $${Math.round(spent)}/$${Math.round(budget)}`
          : budget > 0 ? `ON · cap $${Math.round(budget)}` : 'ON'}
      </AgentLoop>
    );
  }
  if (queued) {
    return (
      <span className="job-agent-pill is-queued" title="Turn on is saved; the backend is validating and preparing this role">
        <RefreshCw size={10} aria-hidden="true" /> TURN-ON QUEUED
      </span>
    );
  }
  if (blocked) {
    return (
      <span className="job-agent-pill is-needs-input" title={intent?.last_error || 'Turn on needs recruiter input'}>
        NEEDS INPUT
      </span>
    );
  }
  return <span className="job-agent-pill is-off" title="Agent off">OFF</span>;
};

const CompactRoleCard = ({
  activeAts,
  activeAtsLastSyncAt,
  agentLive,
  onNavigate,
  onToggleStar,
  reduced,
  role,
  roleFamily,
  workspacePaused,
}) => {
  const provider = roleAtsProvider(role);
  const providerLabel = atsProviderLabel(provider);
  const statusMeta = inactiveRoleStatus(role);
  const location = String(role?.location || role?.workable_location || '').trim();
  const department = String(role?.department || role?.workable_department || '').trim();
  const lastActivity = role?.last_candidate_activity_at
    || role?.updated_at
    || (provider === activeAts ? activeAtsLastSyncAt : null)
    || null;
  const ownerLabel = roleReferenceLabel(roleFamily?.owner);
  const relatedLabels = (roleFamily?.related || []).map(roleReferenceLabel).filter(Boolean);
  const familyRelationship = Number(role?.id) === Number(roleFamily?.owner?.id)
    ? (relatedLabels.length > 0 ? `Related: ${relatedLabels.join(', ')}` : 'Linked role details unavailable')
    : (ownerLabel ? `Original: ${ownerLabel}` : 'Linked role details unavailable');
  const pipelineCount = inPipelineFromStageCounts(role?.stage_counts || {});
  const pendingCount = Number(agentLive?.pending_decisions || 0);
  const starred = Boolean(role?.starred_for_auto_sync);
  const openPipeline = () => onNavigate('job-pipeline', { roleId: role.id });

  return (
    <m.div
      layout={reduced ? false : 'position'}
      className={`job-card is-compact not-live ${provider === 'workable' ? 'from-wk' : ''} ${role?.agentic_mode_enabled && !role?.agent_paused_at && !workspacePaused ? 'agent-on' : ''}`}
      data-role-family={roleFamily?.isLinked ? roleFamily?.owner?.id : undefined}
      onClick={openPipeline}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          openPipeline();
        }
      }}
      role="button"
      tabIndex={0}
      aria-label={`Open ${role?.name || 'role'} pipeline`}
    >
      <div className="job-card-compact-copy">
        <div className="job-card-compact-head">
          <button
            type="button"
            className="job-star"
            aria-label={starred ? 'Unstar role (stop auto-sync)' : 'Star role to enable auto-sync and real-time scoring'}
            aria-pressed={starred}
            onClick={(event) => {
              event.stopPropagation();
              void onToggleStar(role);
            }}
            title={starred ? 'Auto-sync enabled · click to disable' : `Star to auto-sync${provider ? ` from ${providerLabel}` : ''} and score in real-time`}
          >
            <Star size={14} fill={starred ? 'currentColor' : 'none'} aria-hidden="true" />
          </button>
          <div className="job-card-title">
            <div className="job-card-title-line">
              <h3 className="role-name" title={role?.name || 'Untitled role'}>
                {role?.name || 'Untitled role'}
              </h3>
              <span className="job-card-compact-id">#{role.id}</span>
            </div>
          </div>
          <span className="job-card-agent-slot">
            <CompactAgentStatus
              agentLive={agentLive}
              role={role}
              workspacePaused={workspacePaused}
            />
          </span>
        </div>
        {roleFamily?.isLinked ? (
          <div className="job-card-compact-family">
            <Link2 size={11} strokeWidth={2.2} aria-hidden="true" />
            <span>Shared pool · {familyRelationship}</span>
          </div>
        ) : null}
        <div className="role-meta">
          {[department || null, location || null, lastActivity
            ? `updated ${formatRelativeDateTime(lastActivity)}` : null]
            .filter(Boolean).join(' · ') || 'No details yet'}
        </div>
      </div>
      <div className="job-card-compact-bottom">
        <div className="job-card-pill-row job-card-compact-pill-row">
          <div className="job-card-pill-list">
            <AtsTypeTag role={role} size="sm" className="ats-tag" />
            <span className={`job-status-badge is-${statusMeta.tone}`}>{statusMeta.label}</span>
            {role?.client_name ? (
              <span className="job-client-chip" title={`Client · ${role.client_name}`}>
                <Building2 size={10} aria-hidden="true" /> {role.client_name}
              </span>
            ) : null}
          </div>
        </div>
        <div className="job-card-compact-tail">
          {pendingCount > 0 ? (
            <span className="job-foot-pending"><Inbox size={12} aria-hidden="true" /> {pendingCount} awaiting you</span>
          ) : null}
          <span className="job-card-compact-pipeline">
            {pipelineCount > 0 ? `${formatCount(pipelineCount)} in pipeline` : 'No open candidates'}
          </span>
          <span className="job-foot-open">Open →</span>
        </div>
      </div>
    </m.div>
  );
};

export function JobsRoleCatalogue({
  activeAts,
  activeAtsLastSyncAt,
  agentSpendByRole,
  autoExpandInactive,
  gridStaggerDone,
  loadedRoleCount,
  onNavigate,
  onRefresh,
  onToggleStar,
  reduced,
  refreshDisabled,
  roles,
  rolesPartial,
  sourceFilterLabel,
  workspacePaused,
}) {
  const [inactiveExpanded, setInactiveExpanded] = useState(Boolean(autoExpandInactive));
  const roleCatalogue = useMemo(
    () => buildRoleFamilyCatalogue(roles),
    [roles],
  );
  const { activeRoles, inactiveRoles } = useMemo(() => {
    const nextActive = [];
    const nextInactive = [];
    roleCatalogue.roles.forEach((role) => {
      (isRoleDimmed(role) ? nextInactive : nextActive).push(role);
    });
    return { activeRoles: nextActive, inactiveRoles: nextInactive };
  }, [roleCatalogue]);
  const activeRoleIds = useMemo(
    () => new Set(activeRoles.map((role) => Number(role?.id))),
    [activeRoles],
  );
  const activeGroups = useMemo(() => roleCatalogue.groups
    .map((group) => ({
      ...group,
      visibleRoles: group.visibleRoles.filter((role) => activeRoleIds.has(Number(role?.id))),
    }))
    .filter((group) => group.visibleRoles.length > 0), [activeRoleIds, roleCatalogue.groups]);

  useEffect(() => {
    if (autoExpandInactive && inactiveRoles.length > 0) setInactiveExpanded(true);
  }, [autoExpandInactive, inactiveRoles.length]);

  const loadedQualifier = rolesPartial ? ' loaded' : '';
  const inactiveButtonLabel = `${inactiveExpanded ? 'Hide' : 'Show'} archived and inactive roles (${inactiveRoles.length}${loadedQualifier})`;

  return (
    <>
      <section className="jobs-active-section" aria-labelledby="jobs-active-heading">
        <div className="jobs-role-group-heading">
          <div>
            <h2 id="jobs-active-heading">Active roles</h2>
            <p>{rolesPartial ? 'Loaded roles · agent-on first' : 'Agent-on roles first · A–Z'}</p>
          </div>
          <span>{activeRoles.length}{loadedQualifier} role{activeRoles.length === 1 ? '' : 's'}</span>
        </div>
        {activeRoles.length > 0 ? (
          <JobsRoleGrid
            activeAts={activeAts}
            activeAtsLastSyncAt={activeAtsLastSyncAt}
            agentSpendByRole={agentSpendByRole}
            gridStaggerDone={gridStaggerDone}
            onNavigate={onNavigate}
            onToggleStar={onToggleStar}
            reduced={reduced}
            roleGroups={activeGroups}
            roles={activeRoles}
            workspacePaused={workspacePaused}
          />
        ) : (
          <div className="jobs-active-empty" role="status">No active roles match these filters.</div>
        )}
      </section>

      {inactiveRoles.length > 0 ? (
        <section className="jobs-inactive-section" aria-labelledby="jobs-inactive-heading">
          <button
            type="button"
            className="jobs-inactive-toggle"
            aria-expanded={inactiveExpanded}
            aria-controls="jobs-inactive-roles"
            aria-label={inactiveButtonLabel}
            onClick={() => setInactiveExpanded((current) => !current)}
          >
            <span className="jobs-inactive-icon" aria-hidden="true"><Archive size={16} /></span>
            <span className="jobs-inactive-toggle-copy">
              <span id="jobs-inactive-heading" className="jobs-inactive-title">Archived &amp; inactive</span>
              <span className="jobs-inactive-subtitle">Hidden from the working grid · expand to review or reopen</span>
            </span>
            <span className="jobs-inactive-count">{inactiveRoles.length}</span>
            <span className="jobs-inactive-action">{inactiveExpanded ? 'Hide' : 'Show'}</span>
            <ChevronDown
              size={16}
              className={`jobs-inactive-chevron${inactiveExpanded ? ' is-open' : ''}`}
              aria-hidden="true"
            />
          </button>
          <MotionDisclosure
            open={inactiveExpanded}
            id="jobs-inactive-roles"
            className="jobs-inactive-disclosure"
          >
            <div className="jobs-inactive-grid">
              {inactiveRoles.map((role) => (
                <CompactRoleCard
                  key={role.id}
                  activeAts={activeAts}
                  activeAtsLastSyncAt={activeAtsLastSyncAt}
                  agentLive={agentSpendByRole?.[role.id] || null}
                  onNavigate={onNavigate}
                  onToggleStar={onToggleStar}
                  reduced={reduced}
                  role={role}
                  roleFamily={roleCatalogue.familyByRoleId.get(Number(role?.id))}
                  workspacePaused={workspacePaused}
                />
              ))}
            </div>
          </MotionDisclosure>
        </section>
      ) : null}

      <div className="card flat mt-5 flex flex-wrap items-center justify-between gap-3 px-5 py-4 text-xs text-[var(--mute)]">
        <span>
          Showing {activeRoles.length} loaded active role{activeRoles.length === 1 ? '' : 's'}
          {inactiveRoles.length > 0
            ? ` · ${inactiveRoles.length} loaded inactive ${inactiveExpanded ? 'shown' : 'hidden'}`
            : ''}
          {` · ${roles.length} of ${loadedRoleCount} loaded roles match`}
          {rolesPartial ? ' · more roles available' : ''}
          {sourceFilterLabel ? ` · filtered by ${sourceFilterLabel}` : ''}
        </span>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={onRefresh}
          disabled={refreshDisabled}
        >
          <MotionLoop kind="spin" active={refreshDisabled} className="inline-flex" aria-hidden="true">
            <RefreshCw size={13} />
          </MotionLoop>
          Refresh hub
        </button>
      </div>
    </>
  );
}

export default JobsRoleCatalogue;
