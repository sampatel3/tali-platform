import React from 'react';
import '../../styles/20-role-agent-tab.css';
import { Check, Edit3, Search, X } from 'lucide-react';

import CriteriaEditor from '../../shared/ui/CriteriaEditor';
import RecruiterAnswersLog from './RecruiterAnswersLog';
import RoleFeedbackNotes from './RoleFeedbackNotes';
import RoleScreeningQuestions from './RoleScreeningQuestions';
import {
  agentIntakeLifecycleCopy,
  atsProviderLabel,
  roleAtsProvider,
  roleExternalJobLive,
  roleExternalJobState,
} from './atsType';
import { MotionList, MotionListItem, PresenceSwap } from '../../shared/motion';
import { Select } from '../../shared/ui/TaaliPrimitives';

// RoleAgentSettingsTab — merged Agent settings panel per HANDOFF v2 §4.3.
// Hero banner with ON/OFF + CV scoring criteria editor + reject threshold +
// pipeline-distribution dot grid + autonomy toggles, with a sticky sidebar
// for budget / must-haves / pause threshold / audit footer.
const RoleAgentSettingsTab = ({
  role,
  agentStatus = null,
  canControlAgent = true,
  controlDisabledReason = null,
  roleCriteria,
  workspaceCriteria,
  criteriaBusy,
  criteriaSyncing,
  criteriaResetting,
  onCreateCriterion,
  onUpdateCriterion,
  onDeleteCriterion,
  onSyncCriteria,
  onResetCriteria,
  onRestoreHiddenCriterion,
  thresholdDraft,
  setThresholdDraft,
  thresholdValue,
  recruiterCriteria,
  activeApplications,
  belowThresholdCount,
  savingRoleConfig,
  usageBreakdown,
  onSave,
  onScrollToReview,
  onSaveBudget,
  onAutonomyChange,
  thresholdMode,
  onThresholdModeChange,
  suggestedThreshold,
  savingThresholdMode,
  roleTasks = [],
  allTasks = [],
  onAssignAssessmentTasks,
  savingAssessmentTask = false,
  onRoleVersionChange,
  onRoleConflict,
}) => {
  const controlsReadOnly = !canControlAgent;
  const sharedPoolActionsReadOnly = role?.role_kind === 'sister';
  const roleLabel = `${String(role?.name || 'Related role').trim()}${role?.id != null ? ` #${role.id}` : ''}`;
  const originalRoleLabel = `${String(role?.ats_owner_role_name || 'original role').trim()}${role?.ats_owner_role_id != null ? ` #${role.ats_owner_role_id}` : ''}`;
  const sharedPoolActionReason = sharedPoolActionsReadOnly
    ? `${roleLabel} shares one ATS application with ${originalRoleLabel}, so its candidate actions remain behind recruiter approval.`
    : null;
  const total = activeApplications.length;
  const above = Math.max(0, total - belowThresholdCount);
  const sliderValue = thresholdDraft !== '' ? Number(thresholdDraft) : (thresholdValue ?? 55);
  const thresholdDisplay = Math.max(0, Math.min(100, sliderValue));
  // Read the cap from the role record (the field PATCH writes, refreshed on
  // save) first; agent/status only echoes it on a 30s poll, so preferring
  // the poll makes a fresh save look like it didn't take. Spend stays live
  // from agent/status. Default ($50) applies only when neither is set.
  const monthlyBudgetCents = Number(
    role?.monthly_usd_budget_cents
    ?? agentStatus?.monthly_budget_cents
    ?? 5000
  );
  const monthlySpentCents = Number(agentStatus?.monthly_spent_cents ?? 0);
  const budgetPct = monthlyBudgetCents > 0
    ? Math.min(100, Math.round((monthlySpentCents / monthlyBudgetCents) * 100))
    : 0;
  const fmtUsd = (cents) => `$${Math.round((Number(cents) || 0) / 100)}`;
  const dayOfMonth = new Date().getDate();
  const daysInMonth = new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0).getDate();
  const projectedCents = dayOfMonth ? Math.round((monthlySpentCents * daysInMonth) / dayOfMonth) : monthlySpentCents;
  // Role-level autonomy controls. Granular positive-action fields fall back to
  // the legacy auto_promote umbrella until a role has an explicit override.
  // agent_effective_policy is the backend-resolved workspace→role view when
  // available; keeping the local fallback makes old cached role payloads safe.
  const autoReject = Boolean(role?.auto_reject);
  const autoPromote = Boolean(role?.auto_promote);
  const persistedAutoSkipAssessment = Boolean(role?.auto_skip_assessment);
  const effectivePolicy = role?.agent_effective_policy || {};
  const hasGranularAutomation = [
    role?.auto_send_assessment,
    role?.auto_resend_assessment,
    role?.auto_advance,
  ].some((value) => value != null);
  // Untouched roles have nullable granular fields. Preview those as requiring
  // recruiter approval; activation materializes the same HITL-safe policy.
  const autoSendAssessment = hasGranularAutomation
    ? Boolean(effectivePolicy.auto_send_assessment ?? role?.auto_send_assessment ?? autoPromote)
    : false;
  const autoResendAssessment = hasGranularAutomation
    ? Boolean(effectivePolicy.auto_resend_assessment ?? role?.auto_resend_assessment ?? autoPromote)
    : false;
  const autoAdvance = hasGranularAutomation
    ? Boolean(effectivePolicy.auto_advance ?? role?.auto_advance ?? autoPromote)
    : false;
  const configuredPreScreenReject = effectivePolicy.auto_reject_pre_screen
    ?? role?.auto_reject_pre_screen;
  const autoRejectPreScreen = configuredPreScreenReject == null
    ? true
    : Boolean(configuredPreScreenReject);
  // Provider lifecycle is independent from the Agent settings themselves. A
  // non-live external job can still be configured, but write-backs remain
  // blocked until it is reopened in its owning ATS.
  const externalProvider = roleAtsProvider(role);
  const externalProviderLabel = atsProviderLabel(externalProvider);
  const externalJobLive = roleExternalJobLive(role);
  const externalJobState = roleExternalJobState(role);
  // A switch save is one shared-role mutation. Keep exactly one in flight so
  // impatient/rapid clicks cannot dispatch the same rendered role version
  // twice (the second request would truthfully conflict with the first). The
  // local pending value paints immediately; the parent replaces it with the
  // authoritative response, or the freshly-refetched role after a real 409.
  const autonomySaveInFlightRef = React.useRef(false);
  const [pendingAutonomy, setPendingAutonomy] = React.useState(null);
  const handleAutonomyToggle = async (key, value) => {
    if (controlsReadOnly || sharedPoolActionsReadOnly || autonomySaveInFlightRef.current || typeof onAutonomyChange !== 'function') return;
    autonomySaveInFlightRef.current = true;
    setPendingAutonomy({ key, value: Boolean(value) });
    try {
      await onAutonomyChange(key, Boolean(value));
    } finally {
      autonomySaveInFlightRef.current = false;
      setPendingAutonomy(null);
    }
  };
  const visibleAutonomyValue = (key, savedValue) => (
    pendingAutonomy?.key === key ? pendingAutonomy.value : savedValue
  );

  // Assessment tasks live with the rest of the agent configuration. A role may
  // have none, one, or an A/B set; the parent persists the complete ID array so
  // changing one checkbox never silently drops another linked task.
  const assignedTasks = Array.isArray(roleTasks) ? roleTasks : [];
  const activeAssignedTasks = assignedTasks.filter((task) => task?.is_active !== false);
  const hasActiveAssessmentTask = activeAssignedTasks.length > 0;
  // Without an active task there is no valid assessment stage. This derived
  // value keeps legacy records truthful while the backend persists the same
  // invariant on every write.
  const autoSkipAssessment = hasActiveAssessmentTask
    ? persistedAutoSkipAssessment
    : true;
  const assignedTaskIdsFromProps = activeAssignedTasks
    .map((task) => Number(task?.id))
    .filter(Number.isFinite);
  const assignedTaskSignature = [...assignedTaskIdsFromProps].sort((a, b) => a - b).join(',');
  const [selectedAssessmentTaskIds, setSelectedAssessmentTaskIds] = React.useState(assignedTaskIdsFromProps);
  const [assessmentTaskSearch, setAssessmentTaskSearch] = React.useState('');
  const [assessmentChangePending, setAssessmentChangePending] = React.useState(false);

  React.useEffect(() => {
    setSelectedAssessmentTaskIds(assignedTaskIdsFromProps);
    setAssessmentTaskSearch('');
    // The signature is deliberately stable when a parent reload returns new
    // task objects with the same IDs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [role?.id, assignedTaskSignature]);

  // Merge the catalogue with assigned tasks so a linked task remains visible
  // while the organisation-wide task library is still loading.
  const assessmentTaskOptions = (() => {
    const byId = new Map();
    for (const task of (Array.isArray(allTasks) ? allTasks : [])) {
      if (task?.id != null) byId.set(String(task.id), task);
    }
    for (const task of activeAssignedTasks) {
      if (task?.id != null) byId.set(String(task.id), task);
    }
    return [...byId.values()];
  })();
  const selectedAssessmentTaskIdSet = new Set(selectedAssessmentTaskIds);
  const selectedAssessmentTasks = assessmentTaskOptions.filter((task) => (
    selectedAssessmentTaskIdSet.has(Number(task.id))
  ));
  const normalizedAssessmentSearch = assessmentTaskSearch.trim().toLowerCase();
  const filteredAssessmentTaskOptions = normalizedAssessmentSearch
    ? assessmentTaskOptions.filter((task) => {
        const haystack = `${task?.name || ''} ${task?.description || ''}`.toLowerCase();
        return haystack.includes(normalizedAssessmentSearch);
      })
    : assessmentTaskOptions;
  const assessmentBusy = savingAssessmentTask || assessmentChangePending;
  const handleAssessmentToggle = async (taskId) => {
    if (controlsReadOnly || assessmentBusy || typeof onAssignAssessmentTasks !== 'function') return;
    const id = Number(taskId);
    if (!Number.isFinite(id)) return;
    const previous = selectedAssessmentTaskIds;
    const next = selectedAssessmentTaskIdSet.has(id)
      ? previous.filter((currentId) => currentId !== id)
      : [...previous, id];
    setSelectedAssessmentTaskIds(next);
    setAssessmentChangePending(true);
    try {
      await onAssignAssessmentTasks(next);
    } catch {
      // The parent owns the error toast. Restore the visible selection so the
      // manager never claims a failed change was saved.
      setSelectedAssessmentTaskIds(previous);
    } finally {
      setAssessmentChangePending(false);
    }
  };

  // Per-role monthly budget editor — HANDOFF v2 §4.3 wants
  // "Monthly cap $50 · Edit" in the budget sidebar. Falls back to
  // the org default of $50 when the role hasn't set one.
  const [budgetEditing, setBudgetEditing] = React.useState(false);
  const [budgetDraftDollars, setBudgetDraftDollars] = React.useState('');
  const [budgetSaving, setBudgetSaving] = React.useState(false);
  const monthlyBudgetDollars = Math.round(monthlyBudgetCents / 100);
  const startBudgetEdit = () => {
    if (controlsReadOnly) return;
    setBudgetDraftDollars(String(monthlyBudgetDollars));
    setBudgetEditing(true);
  };
  const cancelBudgetEdit = () => {
    setBudgetEditing(false);
    setBudgetDraftDollars('');
  };
  const submitBudgetEdit = async () => {
    if (controlsReadOnly || !onSaveBudget) {
      setBudgetEditing(false);
      return;
    }
    const parsed = Number(budgetDraftDollars);
    if (!Number.isFinite(parsed) || parsed <= 0) return;
    setBudgetSaving(true);
    try {
      await onSaveBudget(parsed);
      setBudgetEditing(false);
    } catch {
      // onSaveBudget already toasted; keep the editor open for a retry.
    } finally {
      setBudgetSaving(false);
    }
  };

  return (
    <div className="mc-agent-settings">
      <div className="mc-agent-settings-main">
        {/* Configure-only header. The on/off toggle and live state live
            in the AgentHeader banner at the top of every role page —
            having a second toggle here was a confusing duplicate. This
            tab is purely "configure how the agent runs when it's on." */}
        <section className="mc-agent-settings-intro">
          <div className="mc-kicker">HOW THE AGENT RUNS THIS ROLE</div>
          <p className="mc-agent-settings-intro-help">
            Starts from your <a href="/settings#agent" style={{ color: 'var(--purple)' }}>workspace defaults</a>, with explicit overrides for this role. The controls below show which candidate actions can run without recruiter approval.
          </p>
        </section>

        {controlsReadOnly ? (
          <div className="mc-agent-warn" role="status" title={controlDisabledReason || undefined}>
            <div>
              <div className="mc-agent-warn-title">Agent settings are read-only</div>
              <div className="mc-agent-warn-body">{controlDisabledReason}</div>
            </div>
          </div>
        ) : null}

        {/* Recruiter intent for this role */}
        <section className="mc-agent-settings-card">
          <div className="mc-agent-settings-card-head">
            <div>
              <h2 className="mc-agent-settings-card-title">
                Role <em>criteria</em>
              </h2>
              <p className="mc-agent-settings-card-help">
                Add, edit, or remove chips freely — this role inherits from workspace defaults and you can customize per role. <strong>Sync workspace</strong> pulls in workspace updates without losing chips you've added here. <strong>Reset</strong> drops your customizations and re-snapshots workspace.
              </p>
            </div>
          </div>
          <CriteriaEditor
            mode="role"
            criteria={roleCriteria}
            workspaceCriteria={workspaceCriteria}
            suppressedIds={Array.isArray(role?.suppressed_org_criterion_ids) ? role.suppressed_org_criterion_ids : []}
            busy={criteriaBusy || controlsReadOnly}
            syncing={criteriaSyncing}
            resetting={criteriaResetting}
            onCreate={onCreateCriterion}
            onUpdate={onUpdateCriterion}
            onDelete={onDeleteCriterion}
            onSync={onSyncCriteria}
            onReset={onResetCriteria}
            onRestoreHidden={onRestoreHiddenCriterion}
          />
        </section>

        {/* Standing recruiter feedback to the agent — append-only log;
            recent entries inline into the agent's system prompt. */}
        <RoleFeedbackNotes
          roleId={role?.id}
          roleVersion={role?.version}
          onRoleVersionChange={onRoleVersionChange}
          onRoleConflict={onRoleConflict}
          readOnly={controlsReadOnly}
          readOnlyReason={controlDisabledReason}
        />

        {/* Q&A history with the agent — recent answers to the agent's
            role-config questions (must-haves, threshold, budget). Hidden
            entirely when there's no history. */}
        <RecruiterAnswersLog roleId={role?.id} />

        {role?.id ? (
          <RoleScreeningQuestions
            roleId={role.id}
            roleVersion={role.version}
            onRoleVersionChange={onRoleVersionChange}
            readOnly={controlsReadOnly}
            readOnlyReason={controlDisabledReason}
          />
        ) : null}

        {/* Screening threshold */}
        <section className="mc-agent-settings-card">
          <div className="mc-agent-settings-card-head">
            <div>
              <h2 className="mc-agent-settings-card-title">
                Screening <em>threshold</em>
              </h2>
              <p className="mc-agent-settings-card-help">
                Candidates below this score fail pre-screen. The two rejection controls below independently govern pre-screen and full CV/role-fit scoring; assessment rejections still need your approval.
              </p>
            </div>
          </div>
          {/* Mode select + the live cut-off read inline ("Currently 55%"),
              matching pipeline-preview's .selrow. The earlier giant 60px
              number floated to the right of the header was off-spec. */}
          <div className="mc-agent-settings-threshold-row">
            <label className="mc-agent-settings-threshold-mode">
              <span className="kicker mute">MODE</span>
              <Select
                inline
                value={thresholdMode}
                onChange={(event) => onThresholdModeChange?.(event.target.value)}
                aria-label="Threshold mode"
                disabled={savingThresholdMode || controlsReadOnly}
              >
                <option value="manual">Manual</option>
                <option value="auto">Agent-managed (dynamic)</option>
              </Select>
            </label>
            <span className="mc-agent-settings-threshold-current">
              {thresholdMode === 'auto'
                ? <>Currently <b>Dynamic</b></>
                : <>Currently <b>{thresholdDisplay}%</b></>}
            </span>
            {thresholdMode === 'auto' && suggestedThreshold?.rationale ? (
              <span className="mc-agent-settings-threshold-rationale">
                {suggestedThreshold.rationale}
              </span>
            ) : null}
          </div>
          {thresholdMode === 'auto' ? (
            <p className="mc-agent-settings-card-help" style={{ marginTop: 4 }}>
              The agent adjusts this threshold from your strongest candidates and hiring outcomes.
            </p>
          ) : (
            <div className="mc-agent-settings-slider">
              <input
                type="range"
                min={0}
                max={100}
                step={1}
                value={thresholdDisplay}
                onChange={(event) => setThresholdDraft(event.target.value)}
                aria-label="Screening threshold percent"
                className="ce-range mc-agent-settings-slider-input"
                style={{ '--ce-range-val': thresholdDisplay }}
                disabled={controlsReadOnly}
              />
              <div className="mc-agent-settings-slider-scale">
                <span>0%</span><span>25%</span><span>50%</span><span>75%</span><span>100%</span>
              </div>
            </div>
          )}
          {total > 0 ? (
            <>
              <div className="mc-kicker is-mute" style={{ marginTop: 18, marginBottom: 12 }}>
                PIPELINE DISTRIBUTION · {total} SCORED
              </div>
              <div className="mc-agent-settings-dotgrid">
                {Array.from({ length: total }).map((_, i) => (
                  <span
                    key={i}
                    className={`mc-agent-settings-dot ${i < belowThresholdCount ? 'is-below' : 'is-above'}`}
                    aria-hidden="true"
                  />
                ))}
              </div>
              <div className="mc-agent-settings-distribution-summary">
                <span>
                  <b style={{ color: 'var(--ink-2)' }}>{belowThresholdCount}</b> below threshold ·{' '}
                  <b style={{ color: 'var(--purple-2)' }}>{above}</b> above
                </span>
                {belowThresholdCount > 0 ? (
                  <button type="button" className="btn btn-ghost btn-sm" onClick={onScrollToReview}>
                    Review the {belowThresholdCount} →
                  </button>
                ) : null}
              </div>
            </>
          ) : (
            <p className="mc-agent-settings-card-help" style={{ marginTop: 18 }}>
              Pipeline distribution will populate once candidates are scored.
            </p>
          )}
        </section>

        {/* Assessment tasks — managed here alongside the behaviour that sends
            them. One selected task is the default; 2+ creates a stable A/B
            rotation without sending the recruiter to another tab. */}
        <section className="mc-agent-settings-card">
          <div className="mc-agent-settings-card-head">
            <div>
              <h2 className="mc-agent-settings-card-title">
                Assessment <em>tasks</em>
              </h2>
              <p className="mc-agent-settings-card-help">
                Choose the assessment sent to qualified candidates. Without an active task, candidates skip this stage.
              </p>
            </div>
          </div>
          <PresenceSwap
            presenceKey={`assessment-count-${selectedAssessmentTasks.length}`}
            className="mc-agent-settings-task-status"
            aria-live="polite"
          >
            {selectedAssessmentTasks.length ? (
              <div className="mc-agent-settings-task-summary">
                <span className="mc-agent-settings-task-summary-icon" aria-hidden="true">
                  <Check size={14} strokeWidth={2.5} />
                </span>
                <div className="mc-agent-settings-task-summary-copy">
                  <strong>
                    {selectedAssessmentTasks.length === 1
                      ? '1 task assigned'
                      : `${selectedAssessmentTasks.length} tasks in A/B rotation`}
                  </strong>
                  <span>{selectedAssessmentTasks.map((task) => task.name).join(' · ')}</span>
                </div>
                {selectedAssessmentTasks.length > 1 ? (
                  <span className="mc-agent-settings-task-ab-badge">A/B</span>
                ) : null}
              </div>
            ) : (
              <div className="mc-agent-settings-task-summary" role="status">
                <span className="mc-agent-settings-task-summary-icon" aria-hidden="true">—</span>
                <div>
                  <div className="mc-agent-settings-rule-title">No assessment task assigned</div>
                  <div className="mc-agent-settings-card-help">
                    Candidates will skip the assessment stage until you assign an active task.
                  </div>
                </div>
              </div>
            )}
          </PresenceSwap>

          {assessmentTaskOptions.length > 6 ? (
            <label className="mc-agent-settings-task-search">
              <span className="sr-only">Search assessment tasks</span>
              <Search size={15} aria-hidden="true" />
              <input
                type="search"
                value={assessmentTaskSearch}
                onChange={(event) => setAssessmentTaskSearch(event.target.value)}
                placeholder="Search assessment tasks"
              />
            </label>
          ) : null}

          {assessmentTaskOptions.length ? (
            <fieldset
              className="mc-agent-settings-task-picker"
              aria-busy={assessmentBusy ? 'true' : 'false'}
            >
              <legend className="sr-only">Tasks assigned to this role</legend>
              <MotionList className="mc-agent-settings-task-list">
                {filteredAssessmentTaskOptions.map((task, index) => {
                  const taskId = Number(task.id);
                  const checked = selectedAssessmentTaskIdSet.has(taskId);
                  return (
                    <MotionListItem
                      key={task.id}
                      index={index}
                      density="compact"
                      className="mc-agent-settings-task-option-wrap"
                    >
                      <label className={`mc-agent-settings-task-option ${checked ? 'is-selected' : ''}`}>
                        <input
                          className="mc-agent-settings-task-checkbox"
                          type="checkbox"
                          checked={checked}
                          onChange={() => handleAssessmentToggle(taskId)}
                          disabled={controlsReadOnly || assessmentBusy || typeof onAssignAssessmentTasks !== 'function'}
                        />
                        <span className="mc-agent-settings-task-option-copy">
                          <strong>{task.name}</strong>
                          {task.description ? <span>{task.description}</span> : null}
                        </span>
                      </label>
                    </MotionListItem>
                  );
                })}
                {filteredAssessmentTaskOptions.length === 0 ? (
                  <div className="mc-agent-settings-task-empty">
                    No tasks match “{assessmentTaskSearch.trim()}”.
                  </div>
                ) : null}
              </MotionList>
              <div className="mc-agent-settings-task-picker-foot" aria-live="polite">
                <span>
                  {selectedAssessmentTasks.length > 1
                    ? 'A/B rotation is split evenly and stays stable for each candidate.'
                    : 'Select multiple tasks to create an A/B rotation.'}
                </span>
                {assessmentBusy ? <span className="mc-agent-settings-task-saving">Saving…</span> : null}
              </div>
            </fieldset>
          ) : (
            <p className="mc-agent-settings-card-help mc-agent-settings-task-library-empty">
              No reusable tasks are available yet. Create one in Tasks, then return here to assign it.
            </p>
          )}
        </section>

        {/* Candidate actions that may bypass recruiter approval */}
        <section className="mc-agent-settings-card">
          <div className="mc-agent-settings-card-head">
            <div>
              <h2 className="mc-agent-settings-card-title">
                Actions <em>without approval</em>
              </h2>
              <p className="mc-agent-settings-card-help">
                Keep an action off when a recruiter must approve it first.
              </p>
            </div>
            <span className="mc-kicker is-mute" role="status" aria-live="polite">
              {sharedPoolActionsReadOnly
                ? 'RECRUITER APPROVAL'
                : pendingAutonomy ? 'Saving…' : 'SAVES INSTANTLY'}
            </span>
          </div>
          {sharedPoolActionsReadOnly ? (
            <div className="mc-agent-warn" role="status" title={sharedPoolActionReason}>
              <div>
                <div className="mc-agent-warn-title">Shared candidate pool</div>
                <div className="mc-agent-warn-body">{sharedPoolActionReason}</div>
              </div>
            </div>
          ) : null}
          {externalProvider && externalJobLive === false && (
            <div className="mc-agent-warn" role="alert">
              <svg
                className="mc-agent-warn-icon"
                viewBox="0 0 24 24"
                fill="none"
                aria-hidden="true"
              >
                <path
                  d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z M12 9v4 M12 17h.01"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              <div>
                <div className="mc-agent-warn-title">
                  {externalProviderLabel} write-backs may be unavailable — this job is {externalJobState || 'not live'}
                </div>
                <div className="mc-agent-warn-body">
                  Re-publish the job in {externalProviderLabel} before relying on synced rejections or stage changes. Taali’s local history remains available.
                </div>
              </div>
            </div>
          )}
          {[
            {
              key: 'auto_reject_pre_screen',
              value: sharedPoolActionsReadOnly
                ? false
                : visibleAutonomyValue('auto_reject_pre_screen', autoRejectPreScreen),
              title: 'Auto-reject pre-screen failures',
              sub: 'Reject candidates who fail a required screening question or the cheap pre-screen gate before full scoring.',
            },
            {
              key: 'auto_reject',
              value: sharedPoolActionsReadOnly
                ? false
                : visibleAutonomyValue('auto_reject', autoReject),
              title: 'Auto-reject after scoring',
              sub: 'Reject candidates when completed CV and role-fit scoring produces an on-policy deterministic reject. Assessment-stage and LLM-only rejects still need approval.',
            },
            {
              key: 'auto_send_assessment',
              value: sharedPoolActionsReadOnly
                ? false
                : visibleAutonomyValue('auto_send_assessment', autoSendAssessment),
              title: 'Auto-send assessments',
              sub: 'Send the approved assessment when a candidate passes pre-screen.',
            },
            {
              key: 'auto_resend_assessment',
              value: sharedPoolActionsReadOnly
                ? false
                : visibleAutonomyValue('auto_resend_assessment', autoResendAssessment),
              title: 'Auto-retry assessment invites',
              sub: 'Retry an assessment invite when the delivery policy allows it.',
            },
            {
              key: 'auto_skip_assessment',
              value: visibleAutonomyValue('auto_skip_assessment', autoSkipAssessment),
              title: 'Skip assessment stage',
              disabled: !hasActiveAssessmentTask,
              sub: !hasActiveAssessmentTask
                ? 'Fixed on until an active assessment task is assigned above.'
                : 'Let qualified candidates bypass the assigned assessment. Advancement still requires approval unless enabled separately.',
            },
            {
              key: 'auto_advance',
              value: sharedPoolActionsReadOnly
                ? false
                : visibleAutonomyValue('auto_advance', autoAdvance),
              title: 'Auto-advance qualified candidates',
              sub: 'Move qualified candidates to recruiter handoff. Interviews, offers, and hiring remain human decisions.',
            },
          ].map((rule, idx) => (
            <label
              key={rule.key}
              className={`mc-agent-settings-rule ${idx === 0 ? '' : 'is-divided'}`}
              aria-busy={pendingAutonomy?.key === rule.key ? 'true' : undefined}
            >
              <button
                type="button"
                className={`mc-switch ${rule.value ? 'on' : ''}`}
                onClick={() => {
                  if (!rule.disabled) handleAutonomyToggle(rule.key, !rule.value);
                }}
                disabled={Boolean(controlsReadOnly || sharedPoolActionsReadOnly || rule.disabled || pendingAutonomy)}
                aria-pressed={Boolean(rule.value)}
                aria-label={rule.title}
                title={sharedPoolActionReason || undefined}
              />
              <div>
                <div className="mc-agent-settings-rule-title">{rule.title}</div>
                <div className="mc-agent-settings-rule-sub">{rule.sub}</div>
              </div>
            </label>
          ))}
        </section>

        {/* Save bar */}
        <div className="mc-agent-settings-savebar">
          <span>
            {sharedPoolActionsReadOnly ? (
              'Shared-pool candidate actions remain behind recruiter approval.'
            ) : (
              <>
                Automatic actions save instantly. Off means recruiter approval is required —{' '}
                <a href="/settings#agent" style={{ color: 'var(--purple)' }}>edit workspace defaults →</a>
              </>
            )}
          </span>
          <button type="button" className="btn btn-purple btn-sm" onClick={onSave} disabled={controlsReadOnly || savingRoleConfig} title={controlsReadOnly ? controlDisabledReason : undefined}>
            {savingRoleConfig ? 'Saving…' : 'Save threshold'}
          </button>
        </div>
      </div>

      {/* Sidebar */}
      <aside className="mc-agent-settings-side">
        <div className="mc-agent-settings-side-card">
          <div className="mc-kicker is-mute" style={{ marginBottom: 8 }}>ROLE AI-USAGE BUDGET · THIS MONTH</div>
          <p className="mc-agent-settings-card-help" style={{ marginTop: 0, marginBottom: 10 }}>
            AI usage for pre-screening, scoring, search, assessment grading, and agent reasoning. Other operating costs appear in Settings → Billing.
          </p>
          <div className="mc-agent-settings-budget-amount">
            <span className="big">{fmtUsd(monthlySpentCents)}</span>
            <span className="of">of {fmtUsd(monthlyBudgetCents)}</span>
          </div>
          <div className="mc-agent-settings-budget-bar">
            <i style={{ width: `${budgetPct}%` }} />
          </div>
          <div className="mc-agent-settings-budget-foot">
            EOM PROJECTION ≈ {fmtUsd(projectedCents)} ·{' '}
            {projectedCents > monthlyBudgetCents ? 'over budget' : 'paced under budget'}
          </div>
          {Array.isArray(usageBreakdown?.by_feature) && usageBreakdown.by_feature.length > 0 ? (
            <ul className="mc-agent-settings-budget-breakdown">
              {(() => {
                // Roll up the per-feature lines into the recruiter-facing
                // labels (Scoring, Pre-screen, Semantic search, etc.) the
                // backend already grouped by, then render one row each.
                const grouped = new Map();
                for (const line of usageBreakdown.by_feature) {
                  const key = line.label || line.feature;
                  const prev = grouped.get(key) || { label: key, cost_cents: 0, event_count: 0 };
                  prev.cost_cents += Number(line.cost_cents || 0);
                  prev.event_count += Number(line.event_count || 0);
                  grouped.set(key, prev);
                }
                return [...grouped.values()]
                  .sort((a, b) => b.cost_cents - a.cost_cents)
                  .map((row) => (
                    <li key={row.label}>
                      <span className="mc-agent-settings-budget-breakdown-label">{row.label}</span>
                      <span className="mc-agent-settings-budget-breakdown-amt">{fmtUsd(row.cost_cents)}</span>
                    </li>
                  ));
              })()}
            </ul>
          ) : monthlySpentCents > 0 ? null : (
            <div className="mc-agent-settings-card-help" style={{ marginTop: 12 }}>
              No spend yet this month.
            </div>
          )}
          {/* HANDOFF v2 §4.3 — Monthly cap $X · Edit. Recruiters can
              raise / lower the per-role cap inline; saved value is
              persisted on the role record (monthly_usd_budget_cents),
              not a session-only override. */}
          {budgetEditing ? (
            <div className="mc-agent-settings-budget-edit">
              <label className="mc-agent-settings-budget-edit-label">
                Monthly cap (USD)
                <div className="mc-agent-settings-budget-edit-input">
                  <span className="prefix">$</span>
                  <input
                    type="number"
                    min={1}
                    step={5}
                    value={budgetDraftDollars}
                    onChange={(event) => setBudgetDraftDollars(event.target.value)}
                  aria-label="Monthly budget in dollars"
                  autoFocus
                  disabled={controlsReadOnly}
                  />
                </div>
              </label>
              <div className="mc-agent-settings-budget-edit-actions">
                <button
                  type="button"
                  className="btn btn-outline btn-xs"
                  onClick={cancelBudgetEdit}
                  disabled={budgetSaving}
                >
                  <X size={11} />
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn btn-purple btn-xs"
                  onClick={submitBudgetEdit}
                  disabled={
                    controlsReadOnly
                    || budgetSaving
                    || budgetDraftDollars === ''
                    || !Number.isFinite(Number(budgetDraftDollars))
                    || Number(budgetDraftDollars) <= 0
                  }
                >
                  <Check size={11} />
                  {budgetSaving ? 'Saving…' : 'Save cap'}
                </button>
              </div>
            </div>
          ) : (
            <div className="mc-agent-settings-budget-cap-row">
              <span>Monthly cap {fmtUsd(monthlyBudgetCents)}</span>
              <button
                type="button"
                className="taali-text-btn mc-agent-settings-budget-edit-link"
                onClick={startBudgetEdit}
                disabled={controlsReadOnly}
                title={controlsReadOnly ? controlDisabledReason : undefined}
              >
                <Edit3 size={11} />
                Edit
              </button>
            </div>
          )}
        </div>

        {/* The scoring requirements (must-haves, nice-to-haves, dealbreakers)
            are edited above in the Role criteria editor — no separate read-only
            must-have card here, so there's one source of truth. */}

        <div className="mc-agent-settings-side-card">
          <div className="mc-kicker is-mute" style={{ marginBottom: 8 }}>PAUSE BEHAVIOR</div>
          <p className="mc-agent-settings-card-help" style={{ marginBottom: 10 }}>
            Budget, credit, and startup holds recover automatically. A manual pause waits for you to resume it. {agentIntakeLifecycleCopy(role)}
          </p>
        </div>

        <div className="mc-agent-settings-audit-callout">
          Starts from <a href="/settings#agent" style={{ color: 'var(--purple)' }}>workspace defaults</a>. Explicit changes here apply to this role only.
        </div>
      </aside>
    </div>
  );
};

export { RoleAgentSettingsTab };
