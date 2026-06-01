import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Bot, AlertTriangle, RefreshCw } from 'lucide-react';

import * as apiClient from '../../../shared/api';
import { Button, Panel, Spinner } from '../../../shared/ui/TaaliPrimitives';
import { useToast } from '../../../context/ToastContext';
import { AgentDecisionCard } from './AgentDecisionCard';

const POLL_INTERVAL_MS = 30_000;

export const PendingAgentDecisionsPanel = ({ role, onAfterAction }) => {
  const { showToast } = useToast();
  const [decisions, setDecisions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [resolvingId, setResolvingId] = useState(null);
  const [error, setError] = useState(null);

  const fetchDecisions = useCallback(async () => {
    if (!role?.id) return;
    setLoading(true);
    try {
      const response = await apiClient.agent.listDecisions({
        role_id: role.id,
        status: 'pending',
        limit: 50,
      });
      setDecisions(Array.isArray(response.data) ? response.data : []);
      setError(null);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load agent decisions');
    } finally {
      setLoading(false);
    }
  }, [role?.id]);

  useEffect(() => {
    fetchDecisions();
    if (!role?.id) return undefined;
    const handle = window.setInterval(fetchDecisions, POLL_INTERVAL_MS);
    return () => window.clearInterval(handle);
  }, [fetchDecisions, role?.id]);

  const handleApprove = useCallback(async (decision) => {
    setResolvingId(decision.id);
    try {
      await apiClient.agent.approveDecision(decision.id);
      showToast?.({ type: 'success', message: `Approved agent recommendation #${decision.id}` });
      await fetchDecisions();
      onAfterAction?.();
    } catch (err) {
      showToast?.({
        type: 'error',
        message: err?.response?.data?.detail || err.message || 'Failed to approve',
      });
    } finally {
      setResolvingId(null);
    }
  }, [fetchDecisions, onAfterAction, showToast]);

  const handleOverride = useCallback(async (decision) => {
    setResolvingId(decision.id);
    try {
      await apiClient.agent.overrideDecision(decision.id, {
        override_action: 'manual_review',
      });
      showToast?.({ type: 'info', message: `Overrode agent recommendation #${decision.id}` });
      await fetchDecisions();
      onAfterAction?.();
    } catch (err) {
      showToast?.({
        type: 'error',
        message: err?.response?.data?.detail || err.message || 'Failed to override',
      });
    } finally {
      setResolvingId(null);
    }
  }, [fetchDecisions, onAfterAction, showToast]);

  const handleReEvaluate = useCallback(async (decision) => {
    setResolvingId(decision.id);
    try {
      const response = await apiClient.agent.reEvaluateDecision(decision.id);
      const queued = response?.data?.queued;
      showToast?.({
        type: queued ? 'success' : 'info',
        message: queued
          ? `Re-evaluating #${decision.id} — the agent will decide again on fresh inputs.`
          : `Discarded stale decision #${decision.id}. ${response?.data?.detail || ''}`.trim(),
      });
      await fetchDecisions();
      onAfterAction?.();
    } catch (err) {
      const detail = err?.response?.data?.detail;
      showToast?.({
        type: 'error',
        message: (detail && (detail.message || detail)) || err.message || 'Failed to re-evaluate',
      });
    } finally {
      setResolvingId(null);
    }
  }, [fetchDecisions, onAfterAction, showToast]);

  const pausedBanner = useMemo(() => {
    if (!role?.agent_paused_at) return null;
    return (
      <div className="taali-card flex items-start gap-3 border-l-4 border-amber-500 px-4 py-3 text-sm">
        <AlertTriangle size={18} className="mt-0.5 text-amber-500" aria-hidden />
        <div>
          <div className="font-medium">Agent paused</div>
          <div className="text-taali-fg-muted">
            {role.agent_paused_reason || 'No new agent decisions will be queued until you re-enable agent mode.'}
          </div>
        </div>
      </div>
    );
  }, [role?.agent_paused_at, role?.agent_paused_reason]);

  if (!role?.agentic_mode_enabled) {
    return null;
  }

  const hasPending = decisions.length > 0;

  return (
    <Panel className="flex flex-col gap-3 p-4">
      <header className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Bot size={18} className="text-taali-accent" aria-hidden />
          <h2 className="text-base font-semibold">Pending agent decisions</h2>
          {hasPending ? (
            <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-taali-accent px-1.5 text-[0.6875rem] font-semibold text-white">
              {decisions.length}
            </span>
          ) : null}
        </div>
        <Button
          variant="ghost"
          size="xs"
          onClick={fetchDecisions}
          disabled={loading}
          aria-label="Refresh pending decisions"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} aria-hidden />
        </Button>
      </header>

      {pausedBanner}

      {error ? (
        <div className="rounded-md border border-rose-500 bg-rose-50 px-3 py-2 text-xs text-rose-700">
          {error}
        </div>
      ) : null}

      {loading && !decisions.length ? (
        <div className="flex items-center gap-2 px-1 py-2 text-sm text-taali-fg-muted">
          <Spinner size={14} /> Loading agent decisions…
        </div>
      ) : null}

      {!loading && !hasPending && !error ? (
        <p className="text-sm text-taali-fg-muted">
          The agent has no pending recommendations for this role.
        </p>
      ) : null}

      {hasPending ? (
        <div className="flex flex-col gap-2">
          {decisions.map((decision) => (
            <AgentDecisionCard
              key={decision.id}
              decision={decision}
              onApprove={() => handleApprove(decision)}
              onOverride={() => handleOverride(decision)}
              onReEvaluate={() => handleReEvaluate(decision)}
              busy={resolvingId === decision.id}
            />
          ))}
        </div>
      ) : null}
    </Panel>
  );
};

export default PendingAgentDecisionsPanel;
