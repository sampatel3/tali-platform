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
}) => {
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
  const autoRejectPreScreen = Boolean(role?.auto_reject_pre_screen);
  const autoPromote = Boolean(role?.auto_promote);
  const autoSkipAssessment = Boolean(role?.auto_skip_assessment);
  const effectivePolicy = role?.agent_effective_policy || {};
  const hasGranularAutomation = [
    role?.auto_send_assessment,
    role?.auto_resend_assessment,
    role?.auto_advance,
  ].some((value) => value != null);
  // Untouched roles have nullable granular fields and the historical database
  // default auto_promote=false. First Turn on deliberately materializes the
  // platform default of all reversible actions ON, so settings must preview
  // that policy before activation rather than displaying a false opt-out.
  const autoSendAssessment = hasGranularAutomation
    ? Boolean(effectivePolicy.auto_send_assessment ?? role?.auto_send_assessment ?? autoPromote)
    : true;
  const autoResendAssessment = hasGranularAutomation
    ? Boolean(effectivePolicy.auto_resend_assessment ?? role?.auto_resend_assessment ?? autoPromote)
    : true;
  const autoAdvance = hasGranularAutomation
    ? Boolean(effectivePolicy.auto_advance ?? role?.auto_advance ?? autoPromote)
    : true;
  const deterministicReject = Boolean(
    effectivePolicy.auto_reject_pre_screen
    ?? autoRejectPreScreen
    ?? autoReject
  ) || autoReject;
  // Provider lifecycle is independent from the Agent settings themselves. A
  // non-live external job can still be configured, but write-backs remain
  // blocked until it is reopened in its owning ATS.
  const externalProvider = roleAtsProvider(role);
  const externalProviderLabel = atsProviderLabel(externalProvider);
  const externalJobLive = roleExternalJobLive(role);
  const externalJobState = roleExternalJobState(role);
  const handleAutonomyToggle = (key, value) => {
    if (typeof onAutonomyChange === 'function') onAutonomyChange(key, value);
  };

  // Assessment tasks live with the rest of the agent configuration. A role may
  // have none, one, or an A/B set; the parent persists the complete ID array so
  // changing one checkbox never silently drops another linked task.
  const assignedTasks = Array.isArray(roleTasks) ? roleTasks : [];
  const activeAssignedTasks = assignedTasks.filter((task) => task?.is_active !== false);
  const generatedDraft = assignedTasks.find((task) => (
    task?.is_active === false && task?.generated && task?.needs_review !== false
  )) || null;
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
    if (assessmentBusy || typeof onAssignAssessmentTasks !== 'function') return;
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
    setBudgetDraftDollars(String(monthlyBudgetDollars));
    setBudgetEditing(true);
  };
  const cancelBudgetEdit = () => {
    setBudgetEditing(false);
    setBudgetDraftDollars('');
  };
  const submitBudgetEdit = async () => {
    if (!onSaveBudget) {
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
            Starts from your <a href="/settings#agent" style={{ color: 'var(--purple)' }}>workspace defaults</a>, with explicit overrides for this role. Configure screening, scoring, assessment flow, autonomy, and budget here. Turn on uses the effective policy shown below without silently changing it.
          </p>
        </section>

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
            busy={criteriaBusy}
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
        <RoleFeedbackNotes roleId={role?.id} />

        {/* Q&A history with the agent — recent answers to the agent's
            role-config questions (must-haves, threshold, budget). Hidden
            entirely when there's no history. */}
        <RecruiterAnswersLog roleId={role?.id} />

        {role?.id ? <RoleScreeningQuestions roleId={role.id} /> : null}

        {/* Reject threshold */}
        <section className="mc-agent-settings-card">
          <div className="mc-agent-settings-card-head">
            <div>
              <h2 className="mc-agent-settings-card-title">
                Reject <em>threshold</em>
              </h2>
              <p className="mc-agent-settings-card-help">
                Below this CV score, candidates are flagged for reject review. Full-score and assessment reject recommendations always need human confirmation. Only a deterministic pre-screen failure can auto-reject, and only when explicitly enabled below.
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
                disabled={savingThresholdMode}
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
              Agent-managed — no fixed number. The agent holds candidates to a quality bar set by your top candidates across all roles, and recalibrates it as you hire.
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
                aria-label="Reject threshold percent"
                className="ce-range mc-agent-settings-slider-input"
                style={{ '--ce-range-val': thresholdDisplay }}
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
                Which skills assessment qualified candidates receive. If none is assigned, Turn on generates, repairs, battle-tests, and approves one automatically; choosing a library task or A/B set is an optional override.
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
              <div className="mc-agent-warn mc-agent-settings-task-warning" role="status">
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
                    {generatedDraft ? 'Generated assessment awaiting Turn on validation' : 'No assessment task assigned'}
                  </div>
                  <div className="mc-agent-warn-body">
                    {generatedDraft
                      ? `${generatedDraft.name} is still a draft. Turn on once and the agent will validate and approve it automatically, or explicitly skip the assessment stage.`
                      : (role?.agentic_mode_enabled
                        ? 'This running role is skipping the assessment stage. Choose an active task before turning assessment skipping off.'
                        : 'The agent has nothing to send after screening yet. No manual task setup is required: Turn on will generate and validate a role-specific task automatically, or you can choose a library task or explicitly skip the stage.')}
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
                          disabled={assessmentBusy || typeof onAssignAssessmentTasks !== 'function'}
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
              No reusable tasks in the library yet. Turn on will generate and validate one for this role automatically.
            </p>
          )}
        </section>

        {/* Autonomy rules */}
        <section className="mc-agent-settings-card">
          <h2 className="mc-agent-settings-card-title">
            Autonomy <em>rules</em>
          </h2>
          <p className="mc-agent-settings-card-help" style={{ marginBottom: 14 }}>
            These are the exact reversible actions Turn on will authorize. Screening and scoring always run while the agent is on; irreversible full-score and assessment rejections always wait for you.
          </p>
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
                  Changes won’t sync to {externalProviderLabel} — this job is {externalJobState || 'not live'}
                </div>
                <div className="mc-agent-warn-body">
                  {externalProviderLabel} doesn’t accept updates on a job in this state, so Taali
                  can’t write rejections or stage changes (such as moving a candidate
                  to interview) back to it. Existing history and recruiter actions
                  remain available here, while new autonomous ATS processing waits.
                  Re-publish this job in {externalProviderLabel} to restore the full flow.
                </div>
              </div>
            </div>
          )}
          <div className="mc-agent-settings-card-help" style={{ marginBottom: 8 }}>
            Effective policy: assessments <strong>{autoSendAssessment ? 'send automatically' : 'wait for approval'}</strong>
            {' · '}resends <strong>{autoResendAssessment ? 'run automatically' : 'wait for approval'}</strong>
            {' · '}advances <strong>{autoAdvance ? 'run automatically' : 'wait for approval'}</strong>
            {' · '}deterministic screening rejects <strong>{deterministicReject ? 'may execute under safeguards' : 'wait for approval'}</strong>.
          </div>
          {[
            {
              key: 'auto_send_assessment',
              value: autoSendAssessment,
              title: 'Send assessments automatically',
              sub: 'Send the approved assessment immediately when a candidate passes the role policy. Off: each initial invite waits in the Decision Hub.',
            },
            {
              key: 'auto_resend_assessment',
              value: autoResendAssessment,
              title: 'Resend assessment invites automatically',
              sub: 'Retry an existing assessment invitation when the delivery policy calls for it. Off: each resend waits for approval.',
            },
            {
              key: 'auto_advance',
              value: autoAdvance,
              title: 'Advance on-policy candidates automatically',
              sub: 'Move qualified candidates into the recruiter handoff without a routine click. Interviews, offers, and hiring remain human decisions.',
            },
            {
              key: 'deterministic_pre_screen_reject',
              value: deterministicReject,
              title: 'Reject deterministic screening failures automatically',
              sub: 'One explicit opt-in for rules-based pre-screen failures when policy and ATS safeguards pass. Full-score, assessment, ambiguous, and off-policy rejections always remain in the Decision Hub for human confirmation.',
            },
            {
              key: 'auto_skip_assessment',
              value: autoSkipAssessment,
              title: 'Auto skip assessment',
              disabled: Boolean(
                role?.agentic_mode_enabled
                && autoSkipAssessment
                && activeAssignedTasks.length === 0
              ),
              sub: (
                role?.agentic_mode_enabled
                && autoSkipAssessment
                && activeAssignedTasks.length === 0
              )
                ? 'Choose an active assessment task above before turning this off. Until then, qualified candidates bypass the assessment stage.'
                : 'Bypass the assessment stage: strong candidates queue as advance-to-interview cards in the Decision Hub instead of receiving an assessment invite. Combine with automatic advance to move them without approval.',
            },
          ].map((rule, idx) => (
            <label key={rule.key} className={`mc-agent-settings-rule ${idx === 0 ? '' : 'is-divided'}`}>
              <button
                type="button"
                className={`mc-switch ${rule.value ? 'on' : ''}`}
                onClick={() => {
                  if (!rule.disabled) handleAutonomyToggle(rule.key, !rule.value);
                }}
                disabled={Boolean(rule.disabled)}
                aria-pressed={Boolean(rule.value)}
                aria-label={rule.title}
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
            Changes apply to this role only. Org defaults stay intact —{' '}
            <a href="/settings#agent" style={{ color: 'var(--purple)' }}>edit workspace defaults →</a>
          </span>
          <button type="button" className="btn btn-purple btn-sm" onClick={onSave} disabled={savingRoleConfig}>
            {savingRoleConfig ? 'Saving…' : 'Save threshold'}
          </button>
        </div>
      </div>

      {/* Sidebar */}
      <aside className="mc-agent-settings-side">
        <div className="mc-agent-settings-side-card">
          <div className="mc-kicker is-mute" style={{ marginBottom: 8 }}>ROLE AI-USAGE BUDGET · THIS MONTH</div>
          <p className="mc-agent-settings-card-help" style={{ marginTop: 0, marginBottom: 10 }}>
            Covers model-backed pre-screening, scoring, semantic search, assessment grading, and agent reasoning. Sandbox runtime, email, storage, and repository hosting are separate; see Settings → Billing for available operational estimates.
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
                    budgetSaving
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
          <div className="mc-kicker is-mute" style={{ marginBottom: 8 }}>AUTOMATIC HOLDS</div>
          <p className="mc-agent-settings-card-help" style={{ marginBottom: 10 }}>
            The agent pauses at the monthly cap, when usage credits run out, or when startup cannot complete. System holds recover and retry automatically after the dependency clears; a manual Pause remains until you explicitly resume it. Pause or Turn off stops autonomous processing and AI spend. {agentIntakeLifecycleCopy(role)}
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
