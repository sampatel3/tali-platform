import React, { useCallback, useEffect, useState } from 'react';

import * as apiClient from '../../shared/api';
import { Button, Input, Panel } from '../../shared/ui/TaaliPrimitives';
import { useToast } from '../../context/ToastContext';

const numericOrNull = (raw) => {
  const trimmed = String(raw || '').trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
};

/**
 * Disclosure-style settings used under the AgentTopBar.
 *
 * The bar handles activate / pause / run-now; this component is just the
 * advanced budget knobs (per-cycle limits + the universal monthly USD cap).
 */
export const JobAgentSettings = ({ role, onRoleUpdated }) => {
  const { showToast } = useToast();
  const [tokenBudget, setTokenBudget] = useState('');
  const [decisionBudget, setDecisionBudget] = useState('');
  const [monthlyUsd, setMonthlyUsd] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setTokenBudget(role?.agent_token_budget_per_cycle != null ? String(role.agent_token_budget_per_cycle) : '');
    setDecisionBudget(role?.agent_decision_budget_per_cycle != null ? String(role.agent_decision_budget_per_cycle) : '');
    setMonthlyUsd(
      role?.monthly_usd_budget_cents != null
        ? String((Number(role.monthly_usd_budget_cents) / 100).toFixed(2)).replace(/\.00$/, '')
        : ''
    );
  }, [role?.id, role?.agent_token_budget_per_cycle, role?.agent_decision_budget_per_cycle, role?.monthly_usd_budget_cents]);

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

  const handleSave = useCallback(async () => {
    const usdValue = numericOrNull(monthlyUsd);
    await persistRole({
      agent_token_budget_per_cycle: numericOrNull(tokenBudget),
      agent_decision_budget_per_cycle: numericOrNull(decisionBudget),
      monthly_usd_budget_cents: usdValue == null ? null : Math.round(usdValue * 100),
    });
    showToast?.({ type: 'success', message: 'Agent budgets updated.' });
  }, [persistRole, tokenBudget, decisionBudget, monthlyUsd, showToast]);

  return (
    <Panel className="flex flex-col gap-3 p-4">
      <header className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-taali-fg-muted">Advanced settings</h3>
      </header>

      <p className="text-xs text-taali-fg-muted">
        The monthly USD cap is the universal budget — every Anthropic call on this role (CV scoring, pre-screen, assessment, agent) feeds into it.
        Per-cycle limits are runaway-loop guards for a single agent cycle.
      </p>

      <div className="grid grid-cols-1 gap-3 border-t border-taali-border pt-3 sm:grid-cols-3">
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium text-taali-fg-muted">Monthly budget (USD)</span>
          <Input
            type="number"
            min={1}
            step={1}
            value={monthlyUsd}
            onChange={(e) => setMonthlyUsd(e.target.value)}
            placeholder="50"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium text-taali-fg-muted">Tokens per agent cycle</span>
          <Input
            type="number"
            min={1000}
            max={500000}
            step={1000}
            value={tokenBudget}
            onChange={(e) => setTokenBudget(e.target.value)}
            placeholder="50000"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium text-taali-fg-muted">Queued decisions per cycle</span>
          <Input
            type="number"
            min={1}
            max={200}
            value={decisionBudget}
            onChange={(e) => setDecisionBudget(e.target.value)}
            placeholder="20"
          />
        </label>
        <div className="sm:col-span-3">
          <Button variant="primary" size="xs" onClick={handleSave} disabled={saving}>
            Save settings
          </Button>
        </div>
      </div>
    </Panel>
  );
};

export default JobAgentSettings;
