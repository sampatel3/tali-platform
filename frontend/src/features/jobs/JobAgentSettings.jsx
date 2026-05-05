import React, { useCallback, useEffect, useState } from 'react';
import { Bot, AlertTriangle, Play } from 'lucide-react';

import * as apiClient from '../../shared/api';
import { Button, Input, Panel } from '../../shared/ui/TaaliPrimitives';
import { ConfirmActionDialog } from '../../shared/ui/ConfirmActionDialog';
import { useToast } from '../../context/ToastContext';

const numericInput = (raw) => {
  const trimmed = String(raw || '').trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
};

export const JobAgentSettings = ({ role, onRoleUpdated }) => {
  const { showToast } = useToast();
  const enabled = Boolean(role?.agentic_mode_enabled);
  const paused = Boolean(role?.agent_paused_at);

  const [tokenBudget, setTokenBudget] = useState('');
  const [decisionBudget, setDecisionBudget] = useState('');
  const [usdBudgetCents, setUsdBudgetCents] = useState('');
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  const [confirmingDisable, setConfirmingDisable] = useState(false);

  useEffect(() => {
    setTokenBudget(role?.agent_token_budget_per_cycle != null ? String(role.agent_token_budget_per_cycle) : '');
    setDecisionBudget(role?.agent_decision_budget_per_cycle != null ? String(role.agent_decision_budget_per_cycle) : '');
    setUsdBudgetCents(role?.agent_usd_budget_monthly_cents != null ? String(role.agent_usd_budget_monthly_cents) : '');
  }, [role?.id, role?.agent_token_budget_per_cycle, role?.agent_decision_budget_per_cycle, role?.agent_usd_budget_monthly_cents]);

  const fetchPendingCount = useCallback(async () => {
    if (!role?.id) return;
    try {
      const res = await apiClient.agent.listDecisions({ role_id: role.id, status: 'pending', limit: 200 });
      setPendingCount(Array.isArray(res.data) ? res.data.length : 0);
    } catch {
      // best-effort; the count is informational
    }
  }, [role?.id]);

  useEffect(() => {
    fetchPendingCount();
  }, [fetchPendingCount]);

  const persistRole = useCallback(async (patch) => {
    setSaving(true);
    try {
      const res = await apiClient.roles.update(role.id, patch);
      onRoleUpdated?.(res.data);
      return res.data;
    } catch (err) {
      showToast?.({
        type: 'error',
        message: err?.response?.data?.detail || err.message || 'Failed to update role',
      });
      throw err;
    } finally {
      setSaving(false);
    }
  }, [role?.id, onRoleUpdated, showToast]);

  const handleEnable = useCallback(async () => {
    await persistRole({ agentic_mode_enabled: true });
    showToast?.({ type: 'success', message: 'Agentic mode enabled. The agent will react to new candidate events.' });
  }, [persistRole, showToast]);

  const handleDisableConfirmed = useCallback(async () => {
    setConfirmingDisable(false);
    try {
      // Discard pending decisions first so the queue isn't left with rows
      // that no longer surface in the UI.
      if (pendingCount > 0) {
        await apiClient.agent.discardPending(role.id);
      }
      await persistRole({ agentic_mode_enabled: false });
      await fetchPendingCount();
      showToast?.({ type: 'info', message: 'Agentic mode disabled.' });
    } catch {
      // toast surfaced in persistRole or in discardPending error handler
    }
  }, [persistRole, role?.id, pendingCount, fetchPendingCount, showToast]);

  const handleSaveBudgets = useCallback(async () => {
    await persistRole({
      agent_token_budget_per_cycle: numericInput(tokenBudget),
      agent_decision_budget_per_cycle: numericInput(decisionBudget),
      agent_usd_budget_monthly_cents: numericInput(usdBudgetCents),
    });
    showToast?.({ type: 'success', message: 'Agent budgets updated.' });
  }, [persistRole, tokenBudget, decisionBudget, usdBudgetCents, showToast]);

  const handleRunNow = useCallback(async () => {
    if (!role?.id) return;
    setRunning(true);
    try {
      const res = await apiClient.agent.runNow(role.id, {});
      if (res.data?.queued) {
        showToast?.({ type: 'success', message: 'Agent cycle queued.' });
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
  }, [role?.id, showToast]);

  return (
    <>
      <Panel className="flex flex-col gap-3 p-4">
        <header className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Bot size={18} className="text-taali-accent" aria-hidden />
            <h3 className="text-sm font-semibold uppercase tracking-wide text-taali-fg-muted">Agentic mode</h3>
            {enabled ? (
              <span className="inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700">ON</span>
            ) : (
              <span className="inline-flex items-center rounded-full bg-taali-bg-muted px-2 py-0.5 text-[11px] font-medium text-taali-fg-muted">OFF</span>
            )}
            {paused ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                <AlertTriangle size={11} aria-hidden /> Paused
              </span>
            ) : null}
          </div>
          {enabled ? (
            <Button
              variant="ghost"
              size="xs"
              onClick={handleRunNow}
              disabled={running || saving || paused}
            >
              <Play size={12} aria-hidden /> Run now
            </Button>
          ) : null}
        </header>

        <p className="text-xs text-taali-fg-muted">
          When enabled, an autonomous agent reads new candidates as they arrive, scores them, and recommends advances or rejects.
          Reject and advance recommendations queue for one-click recruiter approval; sends + scoring auto-execute.
        </p>

        {paused ? (
          <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            <strong>Paused: </strong>{role?.agent_paused_reason || 'budget/limit reached'} — re-enable below to resume.
          </div>
        ) : null}

        {!enabled ? (
          <Button variant="primary" size="sm" onClick={handleEnable} disabled={saving}>
            Enable agentic mode
          </Button>
        ) : (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setConfirmingDisable(true)}
            disabled={saving}
          >
            Disable agentic mode
          </Button>
        )}

        {enabled ? (
          <div className="grid grid-cols-1 gap-3 border-t border-taali-border pt-3 sm:grid-cols-3">
            <label className="flex flex-col gap-1 text-xs">
              <span className="font-medium text-taali-fg-muted">Token budget / cycle</span>
              <Input
                type="number"
                min={1000}
                max={500000}
                step={1000}
                value={tokenBudget}
                onChange={(e) => setTokenBudget(e.target.value)}
                placeholder="default 50000"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="font-medium text-taali-fg-muted">Queued decisions / cycle</span>
              <Input
                type="number"
                min={1}
                max={200}
                value={decisionBudget}
                onChange={(e) => setDecisionBudget(e.target.value)}
                placeholder="default 20"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="font-medium text-taali-fg-muted">Monthly cap (USD cents)</span>
              <Input
                type="number"
                min={0}
                step={100}
                value={usdBudgetCents}
                onChange={(e) => setUsdBudgetCents(e.target.value)}
                placeholder="default 5000 (= $50)"
              />
            </label>
            <div className="sm:col-span-3">
              <Button variant="ghost" size="xs" onClick={handleSaveBudgets} disabled={saving}>
                Save budgets
              </Button>
            </div>
          </div>
        ) : null}

        {enabled && pendingCount > 0 ? (
          <p className="text-xs text-taali-fg-muted">
            <strong>{pendingCount}</strong> pending agent decision{pendingCount === 1 ? '' : 's'} for this role — see the panel above the candidates list.
          </p>
        ) : null}
      </Panel>

      <ConfirmActionDialog
        open={confirmingDisable}
        title="Disable agentic mode?"
        description={
          pendingCount > 0
            ? `${pendingCount} pending agent decision${pendingCount === 1 ? '' : 's'} will be discarded. This cannot be undone — re-enabling will start fresh.`
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

export default JobAgentSettings;
