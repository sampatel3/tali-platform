import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Bot, AlertTriangle, ChevronDown, ChevronUp, Pause, Play, Settings as SettingsIcon, Sparkles } from 'lucide-react';

import * as apiClient from '../../shared/api';
import { Button, Input } from '../../shared/ui/TaaliPrimitives';
import { ConfirmActionDialog } from '../../shared/ui/ConfirmActionDialog';
import { useToast } from '../../context/ToastContext';
import { JobAgentSettings } from './JobAgentSettings';

const DEFAULT_BUDGET_USD = 50;
const POLL_INTERVAL_MS = 5_000;

const formatUsd = (cents) => {
  if (cents == null) return '—';
  const dollars = Number(cents) / 100;
  if (!Number.isFinite(dollars)) return '—';
  return dollars >= 100 ? `$${dollars.toFixed(0)}` : `$${dollars.toFixed(2)}`;
};

const formatRelative = (iso) => {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return null;
  const diff = Math.max(0, Date.now() - t);
  if (diff < 5_000) return 'just now';
  if (diff < 60_000) return `${Math.round(diff / 1_000)}s ago`;
  if (diff < 3_600_000) return `${Math.round(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)}h ago`;
  return `${Math.round(diff / 86_400_000)}d ago`;
};

const tickFromActivity = (activity) => {
  if (!activity) return null;
  const subject = activity.candidate_name
    ? activity.candidate_name
    : activity.application_id
      ? `application #${activity.application_id}`
      : 'candidate';
  switch (activity.event_type) {
    case 'pipeline_stage_changed':
      return `Advanced ${subject}`;
    case 'application_outcome_changed':
      return `Updated outcome on ${subject}`;
    case 'agent_paused':
      return `Paused — ${activity.reason || 'budget reached'}`;
    default:
      return activity.reason || activity.event_type.replace(/_/g, ' ');
  }
};

const tickFromRun = (run) => {
  if (!run) return null;
  const tools = Array.isArray(run.tools_called) ? run.tools_called : [];
  if (tools.length === 0) {
    return 'Cycle running…';
  }
  const last = tools[tools.length - 1];
  if (!last?.name) return 'Cycle running…';
  switch (last.name) {
    case 'score_cv': return 'Scoring a candidate';
    case 'queue_advance_decision': return 'Drafting an advance recommendation';
    case 'get_application': return 'Reading a candidate';
    case 'get_candidate_cv': return 'Inspecting a CV';
    default: return `${last.name.replace(/_/g, ' ')}`;
  }
};

const OffStateBar = ({ role, persistRole, saving }) => {
  const { showToast } = useToast();
  const [budget, setBudget] = useState(String(DEFAULT_BUDGET_USD));
  const [activating, setActivating] = useState(false);

  const handleActivate = useCallback(async () => {
    const numeric = Number(budget);
    if (!Number.isFinite(numeric) || numeric <= 0) {
      showToast?.({ type: 'error', message: 'Set a monthly budget greater than $0 before activating.' });
      return;
    }
    setActivating(true);
    try {
      await persistRole({
        agentic_mode_enabled: true,
        monthly_usd_budget_cents: Math.round(numeric * 100),
      });
      showToast?.({
        type: 'success',
        message: `Agentic mode is on — Taali will work this role with a $${numeric.toFixed(2)}/month budget.`,
      });
    } catch (err) {
      showToast?.({
        type: 'error',
        message: err?.response?.data?.detail || err.message || 'Failed to activate agentic mode',
      });
    } finally {
      setActivating(false);
    }
  }, [budget, persistRole, showToast]);

  return (
    <div
      className="agent-bar agent-bar-off"
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 40,
        margin: '0 0 16px',
        borderRadius: 14,
        padding: '14px 18px',
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: 14,
        background: 'linear-gradient(135deg, color-mix(in srgb, var(--purple) 14%, transparent) 0%, color-mix(in srgb, var(--purple) 6%, transparent) 100%)',
        border: '1px solid color-mix(in srgb, var(--purple) 30%, transparent)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: '1 1 320px', minWidth: 0 }}>
        <div
          style={{
            width: 38,
            height: 38,
            borderRadius: 10,
            background: 'var(--purple)',
            color: '#fff',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
          aria-hidden
        >
          <Sparkles size={18} />
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 15, lineHeight: 1.25 }}>
            Activate Agentic mode for this role
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--mute)', lineHeight: 1.35, marginTop: 2 }}>
            Taali screens, scores, and stages candidates so you focus on interviews. Set a monthly budget — it caps every Anthropic call on this role.
          </div>
        </div>
      </div>

      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11, color: 'var(--mute)' }}>
        <span style={{ fontWeight: 500 }}>Monthly budget (USD)</span>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <span style={{ color: 'var(--mute)' }}>$</span>
          <Input
            type="number"
            min={1}
            step={1}
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            style={{ width: 84 }}
            aria-label="Monthly budget in USD"
          />
        </div>
      </label>

      <Button
        variant="primary"
        size="md"
        onClick={handleActivate}
        disabled={saving || activating}
      >
        {activating ? 'Activating…' : 'Activate →'}
      </Button>
    </div>
  );
};

const OnStateBar = ({ role, status, onRefresh, persistRole, saving, settingsOpen, onToggleSettings }) => {
  const { showToast } = useToast();
  const [running, setRunning] = useState(false);
  const [confirmingDisable, setConfirmingDisable] = useState(false);
  const [tickPulse, setTickPulse] = useState(false);

  const paused = Boolean(status?.paused_at || role?.agent_paused_at);
  const inFlight = Boolean(status?.current_run);
  const pending = Number(status?.pending_decisions || 0);
  const budgetCents = status?.monthly_budget_cents ?? role?.monthly_usd_budget_cents ?? 0;
  const spentCents = Number(status?.monthly_spent_cents || 0);
  const pct = budgetCents > 0 ? Math.min(100, Math.round((spentCents / budgetCents) * 100)) : 0;
  const overEighty = pct >= 80;

  const tickText = useMemo(() => {
    if (paused) return status?.paused_reason || role?.agent_paused_reason || 'paused';
    if (inFlight) return tickFromRun(status.current_run) || 'Cycle running…';
    const fromActivity = tickFromActivity(status?.last_activity);
    if (fromActivity) {
      const rel = formatRelative(status?.last_activity?.created_at);
      return rel ? `${fromActivity} · ${rel}` : fromActivity;
    }
    if (status?.last_run_at) {
      return `Idle · last cycle ${formatRelative(status.last_run_at)}`;
    }
    return 'Idle · waiting for new candidates';
  }, [paused, inFlight, status, role?.agent_paused_reason]);

  const lastTickRef = useRef(tickText);
  useEffect(() => {
    if (tickText !== lastTickRef.current) {
      lastTickRef.current = tickText;
      setTickPulse(true);
      const t = window.setTimeout(() => setTickPulse(false), 900);
      return () => window.clearTimeout(t);
    }
    return undefined;
  }, [tickText]);

  const handleRunNow = useCallback(async () => {
    setRunning(true);
    try {
      const res = await apiClient.agent.runNow(role.id, {});
      if (res.data?.queued) {
        showToast?.({ type: 'success', message: 'Agent cycle queued.' });
        window.setTimeout(onRefresh, 500);
      } else {
        showToast?.({ type: 'info', message: res.data?.detail || 'Cycle was not queued.' });
      }
    } catch (err) {
      showToast?.({
        type: 'error',
        message: err?.response?.data?.detail || err.message || 'Failed to queue agent run',
      });
    } finally {
      setRunning(false);
    }
  }, [role?.id, onRefresh, showToast]);

  const handleDisableConfirmed = useCallback(async () => {
    setConfirmingDisable(false);
    try {
      if (pending > 0) {
        await apiClient.agent.discardPending(role.id);
      }
      await persistRole({ agentic_mode_enabled: false });
      showToast?.({ type: 'info', message: 'Agentic mode disabled.' });
    } catch (err) {
      showToast?.({
        type: 'error',
        message: err?.response?.data?.detail || err.message || 'Failed to disable',
      });
    }
  }, [pending, persistRole, role?.id, showToast]);

  const accent = paused ? 'var(--amber, #d97706)' : 'var(--purple)';
  const fillBg = pct >= 100 ? '#dc2626' : overEighty ? 'var(--amber, #d97706)' : 'var(--purple)';

  return (
    <>
      <div
        className={`agent-bar agent-bar-on ${paused ? 'is-paused' : 'is-active'}`}
        style={{
          position: 'sticky',
          top: 0,
          zIndex: 40,
          margin: '0 0 16px',
          borderRadius: 14,
          padding: '14px 18px',
          color: paused ? 'var(--ink)' : '#fff',
          border: paused ? '1px solid color-mix(in srgb, var(--amber, #d97706) 35%, transparent)' : '1px solid color-mix(in srgb, var(--purple) 70%, #000 0%)',
          isolation: 'isolate',
          overflow: 'hidden',
        }}
      >
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: '1 1 320px', minWidth: 0 }}>
            <div
              style={{
                width: 38,
                height: 38,
                borderRadius: 10,
                background: paused ? 'var(--amber, #d97706)' : 'rgba(255,255,255,0.2)',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
                position: 'relative',
              }}
              aria-hidden
            >
              {paused ? <AlertTriangle size={18} color="#fff" /> : <Bot size={18} color="#fff" />}
              {inFlight && !paused ? (
                <span
                  style={{
                    position: 'absolute',
                    inset: -3,
                    borderRadius: 13,
                    border: '2px solid rgba(255,255,255,0.55)',
                    animation: 'agent-pulse 1.6s ease-out infinite',
                    pointerEvents: 'none',
                  }}
                />
              ) : null}
            </div>
            <div style={{ minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 600, fontSize: 15, lineHeight: 1.2 }}>
                  {paused ? 'Agentic mode paused' : 'Agentic mode is ON'}
                </span>
                {pending > 0 ? (
                  <span
                    style={{
                      background: paused ? 'var(--amber, #d97706)' : 'rgba(255,255,255,0.22)',
                      color: paused ? '#fff' : '#fff',
                      borderRadius: 999,
                      fontSize: 11,
                      fontWeight: 700,
                      padding: '2px 8px',
                    }}
                  >
                    {pending} awaiting your review
                  </span>
                ) : null}
              </div>
              <div
                style={{
                  fontSize: 12.5,
                  marginTop: 3,
                  color: paused ? 'var(--mute)' : 'rgba(255,255,255,0.85)',
                  transition: 'opacity 0.2s ease',
                  opacity: tickPulse ? 0.55 : 1,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  maxWidth: 520,
                }}
              >
                {tickText}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 200 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: paused ? 'var(--mute)' : 'rgba(255,255,255,0.85)' }}>
              <span>This month</span>
              <span style={{ fontWeight: 600 }}>
                {formatUsd(spentCents)} / {formatUsd(budgetCents)}
              </span>
            </div>
            <div
              style={{
                height: 7,
                borderRadius: 4,
                background: paused ? 'rgba(0,0,0,0.10)' : 'rgba(255,255,255,0.22)',
                overflow: 'hidden',
                position: 'relative',
              }}
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="Monthly Anthropic spend"
            >
              <div
                style={{
                  width: `${Math.min(100, pct)}%`,
                  height: '100%',
                  background: paused ? 'var(--amber, #d97706)' : '#fff',
                  borderRadius: 4,
                  transition: 'width 0.4s ease',
                }}
              />
              <div
                style={{
                  position: 'absolute',
                  top: 0,
                  bottom: 0,
                  left: '80%',
                  width: 1,
                  background: paused ? 'rgba(0,0,0,0.18)' : 'rgba(255,255,255,0.45)',
                }}
                aria-hidden
              />
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            <button
              type="button"
              onClick={handleRunNow}
              disabled={running || saving || paused}
              style={{
                background: paused ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.18)',
                color: paused ? 'var(--ink)' : '#fff',
                border: '1px solid ' + (paused ? 'rgba(0,0,0,0.10)' : 'rgba(255,255,255,0.30)'),
                borderRadius: 8,
                padding: '6px 12px',
                fontSize: 12.5,
                fontWeight: 500,
                cursor: running || saving || paused ? 'not-allowed' : 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              <Play size={12} /> Run now
            </button>
            <button
              type="button"
              onClick={() => setConfirmingDisable(true)}
              disabled={saving}
              style={{
                background: 'transparent',
                color: paused ? 'var(--ink)' : '#fff',
                border: '1px solid ' + (paused ? 'rgba(0,0,0,0.18)' : 'rgba(255,255,255,0.40)'),
                borderRadius: 8,
                padding: '6px 12px',
                fontSize: 12.5,
                fontWeight: 500,
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              <Pause size={12} /> Pause
            </button>
            <button
              type="button"
              onClick={onToggleSettings}
              aria-expanded={settingsOpen}
              aria-label={settingsOpen ? 'Hide agent settings' : 'Show agent settings'}
              style={{
                background: 'transparent',
                color: paused ? 'var(--ink)' : '#fff',
                border: '1px solid ' + (paused ? 'rgba(0,0,0,0.18)' : 'rgba(255,255,255,0.40)'),
                borderRadius: 8,
                padding: '6px 10px',
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <SettingsIcon size={13} />
              {settingsOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            </button>
          </div>
        </div>

        <style>{`
          @keyframes agent-pulse {
            0% { transform: scale(1); opacity: 0.65; }
            70% { transform: scale(1.4); opacity: 0; }
            100% { transform: scale(1.4); opacity: 0; }
          }
          @keyframes agent-aurora {
            0%   { background-position: 0% 50%, 100% 50%, 50% 0%; }
            50%  { background-position: 100% 50%, 0% 50%, 50% 100%; }
            100% { background-position: 0% 50%, 100% 50%, 50% 0%; }
          }
          @keyframes agent-rise {
            from { transform: translateY(6px); opacity: 0; }
            to   { transform: translateY(0);   opacity: 1; }
          }
          .agent-bar-on {
            animation: agent-rise 360ms cubic-bezier(0.2, 0.7, 0.2, 1) both;
          }
          .agent-bar-on.is-active {
            background:
              radial-gradient(ellipse 60% 120% at 12% 50%, rgba(167, 139, 250, 0.55) 0%, transparent 60%),
              radial-gradient(ellipse 50% 130% at 88% 40%, rgba(244, 114, 182, 0.40) 0%, transparent 65%),
              radial-gradient(ellipse 70% 100% at 50% 110%, rgba(99, 102, 241, 0.45) 0%, transparent 70%),
              linear-gradient(135deg, var(--purple) 0%, color-mix(in srgb, var(--purple) 80%, #000 0%) 100%);
            background-size: 220% 220%, 200% 240%, 220% 200%, 100% 100%;
            background-position: 0% 50%, 100% 50%, 50% 0%, 0 0;
            animation: agent-rise 360ms cubic-bezier(0.2, 0.7, 0.2, 1) both,
                       agent-aurora 18s ease-in-out infinite;
            box-shadow:
              0 12px 28px -14px color-mix(in srgb, var(--purple) 70%, transparent),
              0 4px 10px -4px color-mix(in srgb, var(--purple) 40%, transparent),
              inset 0 1px 0 rgba(255, 255, 255, 0.10);
          }
          .agent-bar-on.is-paused {
            background:
              linear-gradient(135deg,
                color-mix(in srgb, var(--amber, #d97706) 12%, transparent) 0%,
                color-mix(in srgb, var(--amber, #d97706) 4%, transparent) 100%);
            box-shadow: 0 6px 20px -10px color-mix(in srgb, var(--amber, #d97706) 50%, transparent);
          }
          .agent-bar-on.is-active::before {
            content: '';
            position: absolute;
            inset: 0;
            background-image:
              linear-gradient(rgba(255, 255, 255, 0.06) 1px, transparent 1px),
              linear-gradient(90deg, rgba(255, 255, 255, 0.06) 1px, transparent 1px);
            background-size: 24px 24px;
            mask-image: radial-gradient(ellipse 80% 80% at 50% 50%, #000 40%, transparent 100%);
            -webkit-mask-image: radial-gradient(ellipse 80% 80% at 50% 50%, #000 40%, transparent 100%);
            pointer-events: none;
            z-index: -1;
          }
        `}</style>
      </div>

      <ConfirmActionDialog
        open={confirmingDisable}
        title="Disable agentic mode?"
        description={
          pending > 0
            ? `${pending} pending agent decision${pending === 1 ? '' : 's'} will be discarded. Re-enabling will start fresh.`
            : 'No pending decisions to discard. The agent will stop reacting to new events for this role.'
        }
        confirmLabel="Disable"
        variant="danger"
        onClose={() => setConfirmingDisable(false)}
        onConfirm={handleDisableConfirmed}
      />
    </>
  );
};

export const AgentTopBar = ({ role, onRoleUpdated }) => {
  const [status, setStatus] = useState(null);
  const [saving, setSaving] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const enabled = Boolean(role?.agentic_mode_enabled);

  const persistRole = useCallback(async (patch) => {
    if (!role?.id) return null;
    setSaving(true);
    try {
      const res = await apiClient.roles.update(role.id, patch);
      onRoleUpdated?.(res.data);
      return res.data;
    } finally {
      setSaving(false);
    }
  }, [role?.id, onRoleUpdated]);

  const fetchStatus = useCallback(async () => {
    if (!role?.id || !enabled) return;
    try {
      const res = await apiClient.agent.status(role.id);
      setStatus(res.data || null);
    } catch {
      // best-effort; the bar still renders from the role data we already have
    }
  }, [role?.id, enabled]);

  useEffect(() => {
    if (!enabled) {
      setStatus(null);
      return undefined;
    }
    fetchStatus();
    const handle = window.setInterval(fetchStatus, POLL_INTERVAL_MS);
    return () => window.clearInterval(handle);
  }, [enabled, fetchStatus]);

  if (!role) return null;

  return (
    <>
      {enabled ? (
        <OnStateBar
          role={role}
          status={status}
          onRefresh={fetchStatus}
          persistRole={persistRole}
          saving={saving}
          settingsOpen={settingsOpen}
          onToggleSettings={() => setSettingsOpen((v) => !v)}
        />
      ) : (
        <OffStateBar role={role} persistRole={persistRole} saving={saving} />
      )}

      {enabled && settingsOpen ? (
        <div style={{ marginBottom: 16 }}>
          <JobAgentSettings role={role} onRoleUpdated={onRoleUpdated} variant="disclosure" />
        </div>
      ) : null}
    </>
  );
};

export default AgentTopBar;
