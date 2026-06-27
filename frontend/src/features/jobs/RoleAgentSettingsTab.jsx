import React from 'react';
import { Check, Edit3, X } from 'lucide-react';

import CriteriaEditor from '../../shared/ui/CriteriaEditor';
import RecruiterAnswersLog from './RecruiterAnswersLog';
import RoleFeedbackNotes from './RoleFeedbackNotes';
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
}) => {
  const total = activeApplications.length;
  const above = Math.max(0, total - belowThresholdCount);
  const sliderValue = thresholdDraft !== '' ? Number(thresholdDraft) : (thresholdValue ?? 55);
  const thresholdDisplay = Math.max(0, Math.min(100, sliderValue));
  const mustHaves = Array.isArray(role?.must_haves) ? role.must_haves : [];
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
  // Two real HITL toggles, persisted on the role record. Default off
  // (= every candidate-affecting decision goes to the Decision Hub for
  // human approval). Flipping on lets the agent execute that family of
  // actions immediately and audit-log the result.
  const autoReject = Boolean(role?.auto_reject);
  const autoPromote = Boolean(role?.auto_promote);
  // When the linked Workable req is archived/closed/draft, Workable refuses
  // candidate write-backs (disqualify/move) with a 403 — so Taali acts locally
  // instead (rejects still complete here, just not synced upstream). The agent
  // toggles stay functional; this only surfaces the no-sync reality.
  const workableJobLive = role?.workable_job_live !== false;
  const workableJobState = String(role?.workable_job_state || '').toLowerCase();
  const handleAutonomyToggle = (key, value) => {
    if (typeof onAutonomyChange === 'function') onAutonomyChange(key, value);
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
    if (!Number.isFinite(parsed) || parsed < 0) return;
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
            Overrides your <a href="#org-defaults" style={{ color: 'var(--purple)' }}>org defaults</a> for this role only. Configure scoring, threshold, autonomy, and budget here. To turn the agent on, off, or pause it, use the agent panel at the top of this page.
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

        {/* Reject threshold */}
        <section className="mc-agent-settings-card">
          <div className="mc-agent-settings-card-head">
            <div>
              <h2 className="mc-agent-settings-card-title">
                Reject <em>threshold</em>
              </h2>
              <p className="mc-agent-settings-card-help">
                Below this CV score, candidates are flagged for bulk reject. Nothing auto-rejects without your approval unless autonomy is enabled below.
              </p>
            </div>
            <div className="mc-agent-settings-threshold-display">
              {thresholdMode === 'auto'
                ? <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--purple, #7c5cff)' }}>Dynamic</span>
                : <>{thresholdDisplay}<span className="mc-agent-settings-threshold-pct">%</span></>}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--ink)' }}>
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
            {thresholdMode === 'auto' && suggestedThreshold?.rationale ? (
              <span style={{ fontSize: 12, color: 'var(--mute)', flex: 1, minWidth: 0 }}>
                {suggestedThreshold.rationale}
              </span>
            ) : null}
          </div>
          {thresholdMode === 'auto' ? (
            <p className="mc-agent-settings-card-help" style={{ marginTop: 4 }}>
              Agent-managed — no fixed number. A general quality bar (top candidates across all your roles), recalibrated as you hire; weak pipelines surface fewer.
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

        {/* Autonomy rules */}
        <section className="mc-agent-settings-card">
          <h2 className="mc-agent-settings-card-title">
            Autonomy <em>rules</em>
          </h2>
          <p className="mc-agent-settings-card-help" style={{ marginBottom: 14 }}>
            By default every candidate-affecting decision the agent makes goes to your Decision Hub for approval. Flip these on to let the agent act without asking.
          </p>
          {!workableJobLive && (
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
                  Changes won’t sync to Workable — this job is {workableJobState || 'not live'}
                </div>
                <div className="mc-agent-warn-body">
                  Workable doesn’t accept updates on a job in this state, so Taali
                  can’t write rejections or stage changes (such as moving a candidate
                  to interview) back to it. Taali still applies them here — candidates
                  are rejected, scored, and tracked as normal. Re-publish this job in
                  Workable to restore two-way sync.
                </div>
              </div>
            </div>
          )}
          {[
            {
              key: 'auto_reject',
              value: autoReject,
              title: 'Auto-reject',
              sub: 'Below-threshold candidates are rejected immediately (pre-screen, scoring, and assessment stages). Off: every reject lands in the Decision Hub for one-click approval.',
            },
            {
              key: 'auto_promote',
              value: autoPromote,
              title: 'Auto-promote',
              sub: 'Sending an assessment and advancing to interview happen without approval. Off: each invite/advance queues as a Decision Hub card.',
            },
          ].map((rule, idx) => (
            <label key={rule.key} className={`mc-agent-settings-rule ${idx === 0 ? '' : 'is-divided'}`}>
              <button
                type="button"
                className={`mc-switch ${rule.value ? 'on' : ''}`}
                onClick={() => handleAutonomyToggle(rule.key, !rule.value)}
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
            <a href="#org-defaults" style={{ color: 'var(--purple)' }}>edit org defaults →</a>
          </span>
          <button type="button" className="btn btn-purple btn-sm" onClick={onSave} disabled={savingRoleConfig}>
            {savingRoleConfig ? 'Saving…' : 'Save role settings'}
          </button>
        </div>
      </div>

      {/* Sidebar */}
      <aside className="mc-agent-settings-side">
        <div className="mc-agent-settings-side-card">
          <div className="mc-kicker is-mute" style={{ marginBottom: 8 }}>ROLE BUDGET · THIS MONTH</div>
          <p className="mc-agent-settings-card-help" style={{ marginTop: 0, marginBottom: 10 }}>
            One pot for everything we do on this role: pre-screen, scoring, semantic search, assessments, and the agent.
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
          {usageBreakdown?.monthly_raw_anthropic_cost_cents != null ? (
            <div className="mc-agent-settings-budget-foot">
              ANTHROPIC COST ≈ {fmtUsd(usageBreakdown.monthly_raw_anthropic_cost_cents)} ·{' '}
              MARGIN {fmtUsd(usageBreakdown.monthly_margin_cents)}
              {usageBreakdown.margin_pct ? ` (${Math.round(usageBreakdown.margin_pct)}%)` : ''}
            </div>
          ) : null}
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
                    min={0}
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
                  disabled={budgetSaving || budgetDraftDollars === ''}
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
                className="mc-agent-settings-budget-edit-link"
                onClick={startBudgetEdit}
              >
                <Edit3 size={11} />
                Edit
              </button>
            </div>
          )}
        </div>

        <div className="mc-agent-settings-side-card">
          <div className="mc-kicker is-mute" style={{ marginBottom: 8 }}>MUST-HAVE REQUIREMENTS</div>
          <p className="mc-agent-settings-card-help" style={{ marginBottom: 10 }}>The agent rejects below these — no exceptions.</p>
          {mustHaves.length ? (
            <ul className="mc-agent-settings-mustlist">
              {mustHaves.map((item, idx) => (
                <li key={`${item}-${idx}`}>· {item}</li>
              ))}
            </ul>
          ) : (
            <div className="mc-agent-settings-card-help">No must-haves set yet.</div>
          )}
        </div>

        <div className="mc-agent-settings-side-card">
          <div className="mc-kicker is-mute" style={{ marginBottom: 8 }}>PAUSE THRESHOLD</div>
          <p className="mc-agent-settings-card-help" style={{ marginBottom: 10 }}>Agent pauses itself when budget reaches this %.</p>
          <select className="mc-agent-settings-select" defaultValue={80}>
            <option value={70}>70%</option>
            <option value={80}>80%</option>
            <option value={90}>90%</option>
          </select>
        </div>

        <div className="mc-agent-settings-audit-callout">
          Inherits from <a href="#org-defaults" style={{ color: 'var(--purple)' }}>org defaults</a>. Changes here apply to this role only.
        </div>
      </aside>
    </div>
  );
};

export { RoleAgentSettingsTab };
