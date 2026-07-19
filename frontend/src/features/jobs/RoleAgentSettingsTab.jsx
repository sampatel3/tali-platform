import React from 'react';
import '../../styles/20-role-agent-tab.css';
import {
  Check,
  Edit3,
  History,
  LayoutDashboard,
  Search,
  SlidersHorizontal,
  Target,
  WalletCards,
  X,
} from 'lucide-react';
import { Link, useLocation } from 'react-router-dom';

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
import { ConfirmActionDialog } from '../../shared/ui/ConfirmActionDialog';
import { FocusedSectionLayout } from '../../shared/ui/SectionNavigation';
import { SegmentedControl } from '../../shared/ui/TaaliPrimitives';
import { expectedRoleFamilySnapshot } from '../../shared/decisions/decisionActions';
import {
  resolvedDeterministicReject,
  resolvedRoleAutomation,
  resolvedRoleAutoSkipAssessment,
  resolvedScoredReject,
} from './jobPipelineUtils';

const AGENT_SETTING_SECTIONS = ['overview', 'guidance', 'decisions', 'budget', 'history'];
const THRESHOLD_MODE_OPTIONS = [
  { value: 'manual', label: 'Manual' },
  { value: 'auto', label: 'Agent-managed' },
];

// Focused, URL-backed configuration for one role. Sections mount on first
// visit and remain mounted so local form drafts survive section navigation.
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
  roleTasksFetchKnown = true,
  roleTasksLoadError = '',
  onRetryTasks,
  allTasks = [],
  taskCatalogueLoading = false,
  taskCatalogueError = '',
  taskCatalogueHasMore = false,
  onTaskCatalogueSearchChange,
  onRetryTaskCatalogue,
  onLoadMoreTaskCatalogue,
  onAssignAssessmentTasks,
  savingAssessmentTask = false,
  onRoleVersionChange,
  onRoleConflict,
}) => {
  const location = useLocation();
  const requestedSection = new URLSearchParams(location.search).get('section') || 'overview';
  const activeSection = AGENT_SETTING_SECTIONS.includes(requestedSection)
    ? requestedSection
    : 'overview';
  const [visitedSections, setVisitedSections] = React.useState(() => new Set([activeSection]));

  React.useEffect(() => {
    setVisitedSections((current) => {
      if (current.has(activeSection)) return current;
      return new Set([...current, activeSection]);
    });
  }, [activeSection]);
  const shouldRenderSection = (sectionId) => (
    activeSection === sectionId || visitedSections.has(sectionId)
  );

  const controlsReadOnly = !canControlAgent;
  const assessmentControlsReadOnly = controlsReadOnly || !roleTasksFetchKnown;
  const hasSharedApplication = role?.role_kind === 'sister'
    || Number(role?.sister_role_count || 0) > 0
    || (Array.isArray(role?.role_family?.related) && role.role_family.related.length > 0);
  const roleLabel = `${String(role?.name || 'Related role').trim()}${role?.id != null ? ` #${role.id}` : ''}`;
  const sharedRejectReason = hasSharedApplication
    ? 'Automatic rejection is unavailable for linked roles because rejection closes the shared ATS application across the original role and every related role.'
    : null;
  const sharedAdvanceReason = hasSharedApplication
    ? `Turning this on for ${roleLabel} advances qualified candidates in the original role and every related role because they share one ATS application.`
    : null;
  const sliderValue = thresholdDraft !== '' ? Number(thresholdDraft) : (thresholdValue ?? 55);
  const thresholdDisplay = Math.max(0, Math.min(100, sliderValue));
  const effectiveThreshold = thresholdMode === 'auto'
    ? Number(suggestedThreshold?.value)
    : thresholdDisplay;
  const scoredApplications = (Array.isArray(activeApplications) ? activeApplications : []).filter(
    (application) => application?.pre_screen_score != null
      && Number.isFinite(Number(application.pre_screen_score)),
  );
  const total = scoredApplications.length;
  const previewBelowThresholdCount = Number.isFinite(effectiveThreshold)
    ? scoredApplications.filter(
        (application) => Number(application.pre_screen_score) < effectiveThreshold,
      ).length
    : belowThresholdCount;
  const above = Math.max(0, total - previewBelowThresholdCount);
  const distributionDotCount = Math.min(total, 100);
  const roundedDistributionBelowCount = total > 0
    ? Math.round((distributionDotCount * previewBelowThresholdCount) / total)
    : 0;
  // The dot grid is a capped proportional preview, while the text remains
  // exact. Keep both sides visible for genuinely mixed large cohorts.
  const distributionBelowCount = previewBelowThresholdCount > 0
    && previewBelowThresholdCount < total
    ? Math.max(1, Math.min(distributionDotCount - 1, roundedDistributionBelowCount))
    : roundedDistributionBelowCount;
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
  const autoReject = resolvedScoredReject(role);
  const autoPromote = Boolean(role?.auto_promote);
  const persistedAutoSkipAssessment = resolvedRoleAutoSkipAssessment(role);
  const autoSendAssessment = resolvedRoleAutomation(role, 'auto_send_assessment');
  const autoResendAssessment = resolvedRoleAutomation(role, 'auto_resend_assessment');
  const autoAdvance = resolvedRoleAutomation(role, 'auto_advance');
  const autoRejectPreScreen = resolvedDeterministicReject(role);
  const linkedAutoRejectPreScreen = Boolean(
    role?.agent_effective_policy?.auto_reject_pre_screen ?? role?.auto_reject_pre_screen,
  );
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
  const [sharedActionToConfirm, setSharedActionToConfirm] = React.useState(null);
  const handleAutonomyToggle = async (key, value) => {
    if (controlsReadOnly || autonomySaveInFlightRef.current || typeof onAutonomyChange !== 'function') return;
    autonomySaveInFlightRef.current = true;
    setPendingAutonomy({ key, value: Boolean(value) });
    try {
      const expectedRoleFamily = key === 'auto_reject' || key === 'auto_reject_pre_screen'
        ? expectedRoleFamilySnapshot(role?.role_family)
        : null;
      if (expectedRoleFamily) {
        await onAutonomyChange(key, Boolean(value), { expectedRoleFamily });
      } else {
        await onAutonomyChange(key, Boolean(value));
      }
    } finally {
      autonomySaveInFlightRef.current = false;
      setPendingAutonomy(null);
    }
  };
  const visibleAutonomyValue = (key, savedValue) => (
    pendingAutonomy?.key === key ? pendingAutonomy.value : savedValue
  );
  const visiblePreScreenReject = visibleAutonomyValue(
    'auto_reject_pre_screen',
    hasSharedApplication ? linkedAutoRejectPreScreen : autoRejectPreScreen,
  );
  const visibleScoredReject = visibleAutonomyValue('auto_reject', autoReject);
  const requestAutonomyToggle = (rule) => {
    const nextValue = !rule.value;
    if (rule.confirmSharedApplicationOnEnable && nextValue) {
      setSharedActionToConfirm({ key: rule.key, value: nextValue });
      return;
    }
    void handleAutonomyToggle(rule.key, nextValue);
  };

  // Assessment tasks live with the rest of the agent configuration. A role may
  // have none, one, or an A/B set; the parent persists the complete ID array so
  // changing one checkbox never silently drops another linked task.
  const assignedTasks = Array.isArray(roleTasks) ? roleTasks : [];
  const activeAssignedTasks = assignedTasks.filter((task) => task?.is_active === true);
  const hasActiveAssessmentTask = activeAssignedTasks.length > 0;
  // Without an active task there is no valid assessment stage. This derived
  // value keeps legacy records truthful while the backend persists the same
  // invariant on every write.
  const autoSkipAssessment = roleTasksFetchKnown && !hasActiveAssessmentTask
    ? true
    : persistedAutoSkipAssessment;
  // Keep every linked ID in the mutation set, including inactive legacy links.
  // Only explicitly-active links make the assessment stage eligible, but
  // toggling a different task must never silently unlink retained history.
  const assignedTaskIdsFromProps = assignedTasks
    .map((task) => Number(task?.id))
    .filter(Number.isFinite);
  const assignedTaskSignature = [...assignedTaskIdsFromProps].sort((a, b) => a - b).join(',');
  const assignedTaskIdsRef = React.useRef(assignedTaskIdsFromProps);
  assignedTaskIdsRef.current = assignedTaskIdsFromProps;
  const [selectedAssessmentTaskIds, setSelectedAssessmentTaskIds] = React.useState(assignedTaskIdsFromProps);
  const [assessmentTaskSearch, setAssessmentTaskSearch] = React.useState('');
  const [assessmentChangePending, setAssessmentChangePending] = React.useState(false);

  React.useEffect(() => {
    setSelectedAssessmentTaskIds(assignedTaskIdsRef.current);
    setAssessmentTaskSearch('');
    // The signature is deliberately stable when a parent reload returns new
    // task objects with the same IDs.

  }, [role?.id, assignedTaskSignature]);

  React.useEffect(() => {
    onTaskCatalogueSearchChange?.(assessmentTaskSearch);
  }, [assessmentTaskSearch, onTaskCatalogueSearchChange]);

  // Merge the catalogue with assigned tasks so a linked task remains visible
  // while the organisation-wide task library is still loading.
  const assessmentTaskOptions = (() => {
    const byId = new Map();
    for (const task of (Array.isArray(allTasks) ? allTasks : [])) {
      if (task?.id != null) byId.set(String(task.id), task);
    }
    for (const task of assignedTasks) {
      if (task?.id != null) byId.set(String(task.id), task);
    }
    return [...byId.values()];
  })();
  const selectedAssessmentTaskIdSet = new Set(selectedAssessmentTaskIds);
  const selectedActiveAssessmentTasks = assessmentTaskOptions.filter((task) => (
    selectedAssessmentTaskIdSet.has(Number(task.id)) && task?.is_active === true
  ));
  const normalizedAssessmentSearch = assessmentTaskSearch.trim().toLowerCase();
  const filteredAssessmentTaskOptions = normalizedAssessmentSearch
    ? assessmentTaskOptions.filter((task) => {
        if (selectedAssessmentTaskIdSet.has(Number(task.id))) return true;
        const haystack = `${task?.name || ''} ${task?.description || ''}`.toLowerCase();
        return haystack.includes(normalizedAssessmentSearch);
      })
    : assessmentTaskOptions;
  const assessmentBusy = savingAssessmentTask || assessmentChangePending;
  const handleAssessmentToggle = async (taskId) => {
    if (assessmentControlsReadOnly || assessmentBusy || typeof onAssignAssessmentTasks !== 'function') return;
    const id = Number(taskId);
    if (!Number.isFinite(id)) return;
    const task = assessmentTaskOptions.find((option) => Number(option?.id) === id);
    if (task?.is_active === false || (selectedAssessmentTaskIdSet.has(id) && task?.is_active !== true)) return;
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

  const hrefForSection = (sectionId) => {
    const params = new URLSearchParams(location.search);
    if (sectionId === 'overview') params.delete('section');
    else params.set('section', sectionId);
    const query = params.toString();
    return `${location.pathname}${query ? `?${query}` : ''}${location.hash || ''}`;
  };

  const sections = [
    {
      id: 'overview',
      label: 'Overview',
      description: 'Current role configuration',
      meta: autoReject || autoPromote ? 'Custom' : 'Review first',
      Icon: LayoutDashboard,
    },
    {
      id: 'guidance',
      label: 'Guidance',
      description: 'Criteria and recruiter feedback',
      meta: `${roleCriteria?.length || 0} criteria`,
      Icon: Target,
    },
    {
      id: 'decisions',
      label: 'Decision rules',
      description: 'Threshold, tasks, and autonomy',
      meta: thresholdMode === 'auto' ? 'Dynamic' : `${thresholdDisplay}%`,
      Icon: SlidersHorizontal,
    },
    {
      id: 'budget',
      label: 'Budget & limits',
      description: 'AI usage and monthly cap',
      meta: `${fmtUsd(monthlySpentCents)} / ${fmtUsd(monthlyBudgetCents)}`,
      Icon: WalletCards,
    },
    {
      id: 'history',
      label: 'Recruiter answers',
      description: 'Resolved questions and guidance',
      Icon: History,
    },
  ].map((section) => ({ ...section, to: hrefForSection(section.id) }));

  const overviewCards = [
    {
      id: 'guidance',
      eyebrow: 'Guidance',
      title: `${roleCriteria?.length || 0} role criteria`,
      body: 'What the agent should value, plus standing feedback and screening guidance.',
      status: roleCriteria?.length ? 'Configured' : 'Needs guidance',
    },
    {
      id: 'decisions',
      eyebrow: 'Decision rules',
      title: thresholdMode === 'auto' ? 'Dynamic reject threshold' : `${thresholdDisplay}% reject threshold`,
      body: 'Threshold, assessment routing, and which candidate actions can run automatically.',
      status: autoReject || autoPromote ? 'Some autonomy' : 'Review first',
    },
    {
      id: 'budget',
      eyebrow: 'AI usage budget',
      title: `${fmtUsd(monthlySpentCents)} of ${fmtUsd(monthlyBudgetCents)}`,
      body: `Projected ${fmtUsd(projectedCents)} by month end. Agent work pauses at the cap.`,
      status: budgetPct >= 90 ? 'Near limit' : `${budgetPct}% used`,
    },
    {
      id: 'history',
      eyebrow: 'Recruiter answers',
      title: 'Resolved Q&A history',
      body: 'Review the role questions the agent asked and what your team answered.',
      status: 'Auditable',
    },
  ];

  return (
    <FocusedSectionLayout
      items={sections}
      activeId={activeSection}
      ariaLabel="Agent settings sections"
      idPrefix="role-agent-settings"
      className="mc-agent-settings"
      contentClassName="mc-agent-settings-content"
    >
      <div className="mc-agent-settings-main">
        {controlsReadOnly ? (
          <div className="mc-agent-warn" role="status" title={controlDisabledReason || undefined}>
            <div>
              <div className="mc-agent-warn-title">Agent settings are read-only</div>
              <div className="mc-agent-warn-body">{controlDisabledReason}</div>
            </div>
          </div>
        ) : null}

        {shouldRenderSection('overview') ? (
          <div className="mc-agent-settings-section" hidden={activeSection !== 'overview'}>
        {/* Configure-only header. The on/off toggle and live state live
            in the AgentHeader banner at the top of every role page —
            having a second toggle here was a confusing duplicate. This
            tab is purely "configure how the agent runs when it's on." */}
        <section className="mc-agent-settings-intro">
          <div className="mc-kicker">HOW THE AGENT RUNS THIS ROLE</div>
          <p className="mc-agent-settings-intro-help">
            These settings override your <a href="/settings#agent" style={{ color: 'var(--purple)' }}>workspace defaults</a> for this role only. Configure one focused area at a time; the role header remains the place to turn the agent on, off, or pause it.
          </p>
        </section>

        <div className="mc-agent-settings-overview" aria-label="Agent configuration summary">
          {overviewCards.map((card) => (
            <Link
              key={card.id}
              to={hrefForSection(card.id)}
              className="mc-agent-settings-overview-card"
            >
              <span className="mc-kicker is-mute">{card.eyebrow}</span>
              <strong>{card.title}</strong>
              <span>{card.body}</span>
              <small>{card.status} <span aria-hidden="true">→</span></small>
            </Link>
          ))}
        </div>
          </div>
        ) : null}

        {shouldRenderSection('guidance') ? (
          <div className="mc-agent-settings-section" hidden={activeSection !== 'guidance'}>
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

        {role?.id ? (
          <RoleScreeningQuestions
            roleId={role.id}
            roleVersion={role.version}
            onRoleVersionChange={onRoleVersionChange}
            readOnly={controlsReadOnly}
            readOnlyReason={controlDisabledReason}
          />
        ) : null}
          </div>
        ) : null}

        {shouldRenderSection('history') ? (
          <div className="mc-agent-settings-section" hidden={activeSection !== 'history'}>
            <RecruiterAnswersLog roleId={role?.id} hideWhenEmpty={false} />
          </div>
        ) : null}

        {shouldRenderSection('decisions') ? (
          <div className="mc-agent-settings-section" hidden={activeSection !== 'decisions'}>
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
          {/* Mode and live cut-off stay together as one compact choice. */}
          <div className="mc-agent-settings-threshold-row">
            <div className="mc-agent-settings-threshold-mode">
              <span className="kicker mute">MODE</span>
              <SegmentedControl
                ariaLabel="Threshold mode"
                density="compact"
                value={thresholdMode}
                options={THRESHOLD_MODE_OPTIONS.map((option) => ({
                  ...option,
                  disabled: savingThresholdMode || controlsReadOnly,
                }))}
                onChange={(mode) => onThresholdModeChange?.(mode)}
              />
            </div>
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
              The agent recalibrates this threshold from scored candidates and hiring outcomes. The current recommendation is used in the preview below.
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
                {Array.from({ length: distributionDotCount }).map((_, i) => (
                  <span
                    key={i}
                    className={`mc-agent-settings-dot ${i < distributionBelowCount ? 'is-below' : 'is-above'}`}
                    aria-hidden="true"
                  />
                ))}
              </div>
              <div className="mc-agent-settings-distribution-summary">
                <span>
                  <b style={{ color: 'var(--ink-2)' }}>{previewBelowThresholdCount}</b> below threshold ·{' '}
                  <b style={{ color: 'var(--purple-2)' }}>{above}</b> above
                </span>
                {previewBelowThresholdCount > 0 ? (
                  <button type="button" className="btn btn-ghost btn-sm" onClick={onScrollToReview}>
                    Review the {previewBelowThresholdCount} →
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

        <div className="mc-agent-settings-savebar">
          <span>
            The threshold applies to this role only. Other candidate actions save instantly —{' '}
            <a href="/settings#agent" style={{ color: 'var(--purple)' }}>edit workspace defaults →</a>
          </span>
          <button type="button" className="btn btn-purple btn-sm" onClick={onSave} disabled={controlsReadOnly || savingRoleConfig} title={controlsReadOnly ? controlDisabledReason : undefined}>
            {savingRoleConfig ? 'Saving…' : 'Save reject threshold'}
          </button>
        </div>

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
            presenceKey={roleTasksFetchKnown ? `assessment-count-${selectedActiveAssessmentTasks.length}` : 'assessment-unknown'}
            className="mc-agent-settings-task-status"
            aria-live="polite"
          >
            {!roleTasksFetchKnown ? (
              <div className="mc-agent-settings-task-summary" role={roleTasksLoadError ? 'alert' : 'status'}>
                <span className="mc-agent-settings-task-summary-icon" aria-hidden="true">—</span>
                <div className="mc-agent-settings-task-summary-copy">
                  <strong>{roleTasksLoadError ? 'Assessment tasks unavailable' : 'Checking assessment tasks'}</strong>
                  <span>{roleTasksLoadError || 'Confirming the current assignment before enabling task-dependent controls.'}</span>
                </div>
                {roleTasksLoadError && typeof onRetryTasks === 'function' ? (
                  <button type="button" className="btn btn-outline btn-sm" onClick={onRetryTasks}>Retry</button>
                ) : null}
              </div>
            ) : selectedActiveAssessmentTasks.length ? (
              <div className="mc-agent-settings-task-summary">
                <span className="mc-agent-settings-task-summary-icon" aria-hidden="true">
                  <Check size={14} strokeWidth={2.5} />
                </span>
                <div className="mc-agent-settings-task-summary-copy">
                  <strong>
                    {selectedActiveAssessmentTasks.length === 1
                      ? '1 task assigned'
                      : `${selectedActiveAssessmentTasks.length} tasks in A/B rotation`}
                  </strong>
                  <span>{selectedActiveAssessmentTasks.map((task) => task.name).join(' · ')}</span>
                </div>
                {selectedActiveAssessmentTasks.length > 1 ? (
                  <span className="mc-agent-settings-task-ab-badge">A/B</span>
                ) : null}
              </div>
            ) : (
              <div className="mc-agent-settings-task-summary" role="status">
                <span className="mc-agent-settings-task-summary-icon" aria-hidden="true">—</span>
                <div>
                  <div className="mc-agent-settings-rule-title">
                    {assignedTasks.length ? 'No active assessment task assigned' : 'No assessment task assigned'}
                  </div>
                  <div className="mc-agent-settings-card-help">
                    {assignedTasks.length
                      ? 'Inactive linked tasks are retained below, but candidates skip assessment until an active task is assigned.'
                      : 'Candidates will skip the assessment stage until you assign an active task.'}
                  </div>
                </div>
              </div>
            )}
          </PresenceSwap>

          {roleTasksFetchKnown && (
            assessmentTaskOptions.length > 6
            || taskCatalogueHasMore
            || assessmentTaskSearch
            || taskCatalogueLoading
            || taskCatalogueError
          ) ? (
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

          {roleTasksFetchKnown && taskCatalogueError ? (
            <div className="mc-agent-settings-task-summary" role="alert">
              <span className="mc-agent-settings-task-summary-icon" aria-hidden="true">—</span>
              <div className="mc-agent-settings-task-summary-copy">
                <strong>Task library unavailable</strong>
                <span>{taskCatalogueError}</span>
              </div>
              {typeof onRetryTaskCatalogue === 'function' ? (
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={onRetryTaskCatalogue}
                  disabled={taskCatalogueLoading}
                >Retry</button>
              ) : null}
            </div>
          ) : null}

          {!roleTasksFetchKnown ? null : assessmentTaskOptions.length ? (
            <fieldset
              className="mc-agent-settings-task-picker"
              aria-busy={assessmentBusy || taskCatalogueLoading ? 'true' : 'false'}
            >
              <legend className="sr-only">Tasks assigned to this role</legend>
              <MotionList className="mc-agent-settings-task-list">
                {filteredAssessmentTaskOptions.map((task, index) => {
                  const taskId = Number(task.id);
                  const checked = selectedAssessmentTaskIdSet.has(taskId);
                  const inactiveLinkedTask = checked && task?.is_active !== true;
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
                          disabled={assessmentControlsReadOnly || assessmentBusy
                            || task?.is_active === false || inactiveLinkedTask
                            || typeof onAssignAssessmentTasks !== 'function'}
                        />
                        <span className="mc-agent-settings-task-option-copy">
                          <strong>{task.name}</strong>
                          {inactiveLinkedTask ? (
                            <span>Inactive linked task · retained but not used for assessment eligibility.</span>
                          ) : task.description ? <span>{task.description}</span> : null}
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
                  {selectedActiveAssessmentTasks.length > 1
                    ? 'A/B rotation is split evenly and stays stable for each candidate.'
                    : 'Select multiple tasks to create an A/B rotation.'}
                </span>
                {assessmentBusy || taskCatalogueLoading ? (
                  <span className="mc-agent-settings-task-saving">
                    {assessmentBusy ? 'Saving…' : 'Loading tasks…'}
                  </span>
                ) : null}
                {taskCatalogueHasMore && typeof onLoadMoreTaskCatalogue === 'function' ? (
                  <button
                    type="button"
                    className="btn btn-outline btn-xs"
                    onClick={onLoadMoreTaskCatalogue}
                    disabled={assessmentBusy || taskCatalogueLoading}
                  >Load more tasks</button>
                ) : null}
              </div>
            </fieldset>
          ) : taskCatalogueLoading ? (
            <p className="mc-agent-settings-card-help mc-agent-settings-task-library-empty" role="status">
              Loading reusable tasks…
            </p>
          ) : taskCatalogueError ? null : (
            <p className="mc-agent-settings-card-help mc-agent-settings-task-library-empty">
              No reusable tasks are available yet. Create one in Tasks and return here to assign it, or use the explicit generate-and-validate choice when turning on the agent.
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
                Choose what the agent can do without asking you. Keep an action off when a recruiter must approve it first.
              </p>
            </div>
            <span className="mc-kicker is-mute" role="status" aria-live="polite">
              {pendingAutonomy ? 'Saving…' : 'SAVES INSTANTLY'}
            </span>
          </div>
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
          {hasSharedApplication ? (
            <div className="mc-agent-settings-callout" role="note">
              <span className="mc-agent-settings-callout-tag">Linked roles</span>
              <span>{sharedRejectReason}</span>
            </div>
          ) : null}
          {[
            {
              key: 'auto_reject_pre_screen',
              value: visiblePreScreenReject,
              title: 'Auto-reject pre-screen failures',
              disabled: hasSharedApplication && !visiblePreScreenReject,
              disabledReason: sharedRejectReason,
              sub: 'Reject candidates who fail a required screening question or the cheap pre-screen gate before full scoring.',
            },
            {
              key: 'auto_reject',
              value: visibleScoredReject,
              title: 'Auto-reject after scoring',
              disabled: hasSharedApplication && !visibleScoredReject,
              disabledReason: sharedRejectReason,
              sub: 'Reject candidates when completed CV and role-fit scoring produces an on-policy deterministic reject. Assessment-stage and LLM-only rejects still need approval.',
            },
            {
              key: 'auto_send_assessment',
              value: visibleAutonomyValue('auto_send_assessment', autoSendAssessment),
              title: 'Auto-send assessments',
              disabled: !roleTasksFetchKnown,
              sub: roleTasksFetchKnown
                ? 'Send the approved assessment when a candidate passes pre-screen.'
                : 'Unavailable until the current task assignment is confirmed.',
            },
            {
              key: 'auto_resend_assessment',
              value: visibleAutonomyValue('auto_resend_assessment', autoResendAssessment),
              title: 'Auto-retry assessment invites',
              disabled: !roleTasksFetchKnown,
              sub: roleTasksFetchKnown
                ? 'Retry an assessment invite when the delivery policy allows it.'
                : 'Unavailable until the current task assignment is confirmed.',
            },
            {
              key: 'auto_skip_assessment',
              value: visibleAutonomyValue('auto_skip_assessment', autoSkipAssessment),
              title: 'Skip assessment stage',
              disabled: !roleTasksFetchKnown || !hasActiveAssessmentTask,
              sub: !roleTasksFetchKnown
                ? 'Unavailable until the current task assignment is confirmed.'
                : !hasActiveAssessmentTask
                  ? 'Fixed on until an active assessment task is assigned above.'
                  : 'Let qualified candidates bypass the assigned assessment. Advancement still requires approval unless enabled separately.',
            },
            {
              key: 'auto_advance',
              value: visibleAutonomyValue('auto_advance', autoAdvance),
              title: 'Auto-advance qualified candidates',
              confirmSharedApplicationOnEnable: hasSharedApplication,
              sub: hasSharedApplication
                ? `${sharedAdvanceReason} You will confirm before turning this on.`
                : 'Move qualified candidates to recruiter handoff. Interviews, offers, and hiring remain human decisions.',
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
                  if (!rule.disabled) requestAutonomyToggle(rule);
                }}
                disabled={Boolean(controlsReadOnly || rule.disabled || pendingAutonomy)}
                aria-pressed={Boolean(rule.value)}
                aria-label={rule.title}
                title={rule.disabledReason || undefined}
              />
              <div>
                <div className="mc-agent-settings-rule-title">{rule.title}</div>
                <div className="mc-agent-settings-rule-sub">{rule.sub}</div>
              </div>
            </label>
          ))}
        </section>

          </div>
        ) : null}
      </div>

      {shouldRenderSection('budget') ? (
      <aside className="mc-agent-settings-side" hidden={activeSection !== 'budget'}>
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
      ) : null}

      <ConfirmActionDialog
        open={sharedActionToConfirm?.key === 'auto_advance'}
        title="Turn on auto-advance across linked roles?"
        description={`${sharedAdvanceReason || ''} Each automatic advancement updates the one shared ATS application, so it appears in every linked role.`}
        confirmLabel="Turn on auto-advance"
        onClose={() => setSharedActionToConfirm(null)}
        onConfirm={() => {
          const action = sharedActionToConfirm;
          setSharedActionToConfirm(null);
          if (action) void handleAutonomyToggle(action.key, action.value);
        }}
      />
    </FocusedSectionLayout>
  );
};

export { RoleAgentSettingsTab };
