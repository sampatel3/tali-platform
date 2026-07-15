// The chat dock (Option C) — converse with one role's agent. Fetches the
// merged timeline, renders chat + the agent's questions + decisions + impact
// cards, and posts messages that run the agent turn. It is the same complete
// role thread exposed by Chat > Agents.

import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { CircleHelp, MessageSquare, PanelRightClose, Users, X } from 'lucide-react';

import { agentChat } from '../../../shared/api';
import { useToast } from '../../../context/ToastContext';
import {
  ChatComposer,
  ChatEmptyState,
  ChatMessage,
  ChatSurface,
  NewMessageNotice,
  RoleAgentTimeline,
  ThinkingDots,
  useAgentRequestReply,
  useAgentUpdateAwareness,
} from '../../../shared/chat';
import { DraftTaskCard, ImpactCard } from './cards.jsx';
import CandidateEvidenceCard from '../../chat/CandidateEvidenceCard';
import {
  AgentLoop,
  MotionAttentionBadge,
  motionSafeScrollBehavior,
} from '../../../shared/motion';
import { useRoleDecisionDetails } from '../../../shared/decisions/useRoleDecisionDetails';

// Role-scoped empty-state prompts. Off roles get an activation suggestion that
// drives the agent's set_agent_state tool, so you can light one up from Home.
const ON_SUGGESTIONS = [
  'Cap salary at AED 25k',
  'Who in the pool is based in MENA?',
  'Drop the score cut-off to 65',
  'Show me the draft tasks',
];
const OFF_SUGGESTIONS = [
  'Turn the agent on at $50/month',
  'What would you screen for on this role?',
  'Show me who is waiting on a decision',
];
const READ_ACK_DELAY_MS = 1000;

export function AgentChatDock({
  roleId,
  roleName,
  agentEnabled = true,
  onReload,
  onCollapse,
  // Bulk mode: when ≥1 role is selected in the rail, the composer fans out to
  // all of them instead of the single active role.
  bulkSelectedRoles = [],
  onSendBulk,
  onClearBulk,
}) {
  const { showToast } = useToast() || { showToast: () => {} };
  const isBulk = (bulkSelectedRoles?.length || 0) > 0;
  const [timeline, setTimeline] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [input, setInput] = useState('');
  // Durable "agent is working…" — the turn now runs in a worker, so this is
  // driven by the server and survives navigation / an agent switch, resuming
  // when you reopen the thread.
  const [agentWorking, setAgentWorking] = useState(false);
  // A timeline fetch and a read acknowledgement are deliberately separate:
  // fetching keeps the thread live, while this marker lets us wait until the
  // selected thread has actually been visible for a moment before acknowledging
  // it through the explicit read endpoint.
  const [loadedRoleId, setLoadedRoleId] = useState(null);
  // Set when the in-flight poll hits its time cap without the reply landing —
  // rather than freezing "Working…" forever with a locked composer, we surface
  // a "taking longer than expected" notice, unlock the composer, and keep
  // polling at a slower cadence so a late reply still lands.
  const [stalled, setStalled] = useState(false);
  const scrollRef = useRef(null);
  const composerRef = useRef(null);
  const timelineRegionId = useId();
  const [composerAnnouncement, setComposerAnnouncement] = useState('');
  // Guards async results against an agent switch: a slow turn or fetch that
  // resolves after you've moved to another agent must not clobber the new
  // thread (the message itself is safe — it's already persisted server-side).
  const activeRoleRef = useRef(roleId);
  useEffect(() => {
    activeRoleRef.current = roleId;
    // This component is reused as the recruiter switches the Home rail. Clear
    // the old role synchronously so its messages/questions never render under
    // the newly selected role heading while the next request is in flight.
    setTimeline([]);
    setAgentWorking(false);
    setStalled(false);
    setLoadedRoleId(null);
    setLoading(Boolean(roleId));
  }, [roleId]);

  const load = useCallback(async (opts = {}) => {
    if (!roleId) return;
    const forRole = roleId;
    if (!opts.silent) setLoading(true);
    try {
      const { data } = await agentChat.getTimeline(roleId);
      if (activeRoleRef.current !== forRole) return; // switched away mid-fetch
      setTimeline(data.timeline || []);
      setAgentWorking(Boolean(data.agent_working));
      setLoadedRoleId(forRole);
      // A successful timeline read means the poll is healthy again — clear any
      // stalled notice (the reply either landed, or work is genuinely ongoing).
      setStalled(false);
    } catch {
      if (!opts.silent && activeRoleRef.current === forRole) setTimeline([]);
    } finally {
      if (!opts.silent && activeRoleRef.current === forRole) setLoading(false);
    }
  }, [roleId]);

  useEffect(() => {
    load();
  }, [load]);

  // Acknowledge only after a successful load has remained selected in a
  // visible tab for a short dwell. Bulk mode does not show this role's thread,
  // so it must not consume its unread state.
  useEffect(() => {
    if (!roleId || loadedRoleId !== roleId || isBulk) return undefined;
    let timer = null;
    let acknowledged = false;
    const clear = () => {
      if (timer != null) window.clearTimeout(timer);
      timer = null;
    };
    const schedule = () => {
      clear();
      if (acknowledged || (typeof document !== 'undefined' && document.visibilityState !== 'visible')) return;
      timer = window.setTimeout(() => {
        timer = null;
        if (activeRoleRef.current !== roleId
            || (typeof document !== 'undefined' && document.visibilityState !== 'visible')) return;
        acknowledged = true;
        void Promise.resolve(agentChat.markRead(roleId)).catch(() => {});
      }, READ_ACK_DELAY_MS);
    };
    const onVisibility = () => {
      if (document.visibilityState === 'visible') schedule();
      else clear();
    };
    schedule();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      clear();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [isBulk, loadedRoleId, roleId]);

  const send = useCallback(
    async (text) => {
      const msg = (text || '').trim();
      // A stalled turn is unblocked on purpose — let the recruiter send again
      // (the previous turn keeps running server-side and its reply still lands).
      if (!msg || sending || (agentWorking && !stalled) || !roleId) return;
      const forRole = roleId;
      setInput('');
      setSending(true);
      setStalled(false);
      // Optimistic: show the recruiter's message immediately.
      setTimeline((t) => [
        ...t,
        { kind: 'message', id: `local-${t.length}`, author: 'recruiter', text: msg, created_at: new Date().toISOString() },
      ]);
      try {
        // The send persists the message and accepts the turn; the agent's reply
        // is produced by a worker and arrives via the poll below. If you switch
        // agents before it returns, drop the result — the message is already
        // durable server-side and will be there when you come back.
        const { data } = await agentChat.sendMessage(forRole, msg);
        if (activeRoleRef.current !== forRole) return;
        setTimeline(data.timeline || []);
        setAgentWorking(data.agent_working !== false);
        // The turn may have changed constraints/thresholds → refresh the feed.
        onReload?.();
      } catch (err) {
        if (activeRoleRef.current === forRole) {
          const status = err?.response?.status;
          // Never lose the typed message on failure — restore it unless the
          // user has already started typing something new. A 409 means the
          // agent's still on the previous message (info, not an error).
          setInput((cur) => cur || msg);
          showToast?.(
            err?.response?.data?.detail || 'Couldn’t send that. Try again.',
            status === 409 ? 'info' : 'error',
          );
          load();
        }
      } finally {
        if (activeRoleRef.current === forRole) setSending(false);
      }
    },
    [roleId, sending, agentWorking, stalled, onReload, showToast, load]
  );

  const answer = useCallback(
    async (needsInputId, response) => {
      try {
        await agentChat.answerNeedsInput(needsInputId, response);
        load();
        onReload?.();
        return true;
      } catch {
        showToast?.('Couldn’t record that answer.', 'error');
        return false;
      }
    },
    [load, onReload, showToast]
  );

  const {
    beginReply,
    cancelReply,
    replyTo,
    replying,
    submitReply,
    submitting: replySubmitting,
  } = useAgentRequestReply({
    value: input,
    onChange: setInput,
    onAnswer: answer,
  });
  const replyScopeRef = useRef(`${isBulk ? 'bulk' : 'role'}:${roleId || ''}`);

  useEffect(() => {
    if (replying) composerRef.current?.focus();
  }, [replying]);

  useEffect(() => {
    const scope = `${isBulk ? 'bulk' : 'role'}:${roleId || ''}`;
    if (scope === replyScopeRef.current) return;
    replyScopeRef.current = scope;
    if (replying) cancelReply();
  }, [cancelReply, isBulk, replying, roleId]);

  const dismiss = useCallback(
    async (needsInputId) => {
      try {
        await agentChat.dismissNeedsInput(needsInputId);
        load();
        return true;
      } catch {
        showToast?.('Couldn’t dismiss that.', 'error');
        return false;
      }
    },
    [load, showToast]
  );

  const approveDraft = useCallback(
    async (taskId) => {
      if (!roleId || sending) return;
      setSending(true);
      try {
        const { data } = await agentChat.approveDraftTask(roleId, taskId);
        setTimeline(data.timeline || []);
        onReload?.();
      } catch (err) {
        showToast?.(err?.response?.data?.detail || 'Couldn’t approve that draft.', 'error');
      } finally {
        setSending(false);
      }
    },
    [roleId, sending, onReload, showToast]
  );

  const reviseDraft = useCallback(
    async (taskId, feedback) => {
      if (!roleId || sending) return;
      // Revising re-authors the task (one model call) — show the thinking
      // bubble while it runs, then drop in the revised review card.
      setSending(true);
      try {
        const { data } = await agentChat.reviseDraftTask(roleId, taskId, feedback);
        setTimeline(data.timeline || []);
        onReload?.();
      } catch (err) {
        showToast?.(err?.response?.data?.detail || 'Couldn’t revise that draft.', 'error');
        load();
      } finally {
        setSending(false);
      }
    },
    [roleId, sending, onReload, showToast, load]
  );

  // One submit path for both modes: bulk fans out to the selection, otherwise
  // the message runs on the active role.
  const submitComposer = useCallback(
    (text) => {
      const msg = (text || '').trim();
      if (!msg) return;
      if (isBulk) {
        onSendBulk?.(msg);
        setInput('');
      } else {
        send(msg);
      }
    },
    [isBulk, onSendBulk, send]
  );

  const prefillPrompt = useCallback((prompt) => {
    const next = String(prompt || '').trim();
    if (!next) return;
    setInput(next);
    composerRef.current?.focus();
    setComposerAnnouncement('Added to composer');
  }, []);

  useEffect(() => {
    if (!composerAnnouncement) return undefined;
    const timer = window.setTimeout(() => setComposerAnnouncement(''), 1600);
    return () => window.clearTimeout(timer);
  }, [composerAnnouncement]);

  // Effects clear state after a role change, but effects run after paint. Gate
  // the render source too, so the previous role cannot flash for even one frame
  // under the new role heading.
  const visibleTimeline = loadedRoleId === roleId ? timeline : [];

  const {
    byId: decisionDetails,
    loading: decisionDetailsLoading,
    error: decisionDetailsError,
    refresh: refreshDecisionDetails,
  } = useRoleDecisionDetails(roleId, isBulk ? [] : visibleTimeline);

  const refreshAfterDecision = useCallback(async () => {
    await Promise.all([
      load({ silent: true }),
      refreshDecisionDetails(),
    ]);
    onReload?.();
  }, [load, onReload, refreshDecisionDetails]);

  // The dock mirrors the role's full chronological thread, including the HITL
  // decisions that need recruiter action.
  const items = useMemo(
    () => visibleTimeline.filter(
      (it) => it.kind === 'message' || it.kind === 'needs_input' || it.kind === 'decision',
    ),
    [visibleTimeline],
  );
  const openQuestions = useMemo(
    () => items.filter((item) => item.kind === 'needs_input' && item.status === 'open'),
    [items],
  );
  const openQuestionPositions = useMemo(
    () => new Map(openQuestions.map((item, index) => [item.needs_input_id ?? item.id, index + 1])),
    [openQuestions],
  );
  const jumpToOldestQuestion = useCallback(() => {
    const target = scrollRef.current?.querySelector('.tk-agent-prompt[data-status="open"]');
    if (!target) return;
    target.scrollIntoView({
      behavior: motionSafeScrollBehavior('smooth'),
      block: 'center',
    });
    target.focus({ preventScroll: true });
  }, []);

  const {
    hasNewAgentUpdate,
    jumpToLatest,
  } = useAgentUpdateAwareness({
    items,
    ready: !isBulk && loadedRoleId === roleId,
    scopeKey: `${isBulk ? 'bulk' : 'role'}:${roleId || ''}`,
    scrollRef,
  });

  // A constraint edit's re-screen is "in flight" when the latest agent message
  // is a constraint change that kicked a re-screen, with no follow-up message
  // after it yet. While so, poll quietly so the proactive "re-screen complete"
  // impact message lands without a manual refresh.
  let lastAgentIdx = -1;
  let lastRescreenIdx = -1;
  items.forEach((it, i) => {
    if (it.kind === 'message' && it.author === 'agent') {
      lastAgentIdx = i;
      if ((it.actions || []).some((c) => c.type === 'constraint_change' && (c.rescreening_count || 0) > 0)) {
        lastRescreenIdx = i;
      }
    }
  });
  const rescreenPending = lastRescreenIdx >= 0 && lastRescreenIdx === lastAgentIdx;

  // Poll while a turn is in flight (fast, so the reply feels prompt) or while a
  // re-screen's proactive follow-up is pending (slower). The reply landing flips
  // agentWorking off via load(), which stops the poll.
  // Poll fast while a turn is fresh; once past the 6-minute cap the turn is
  // "taking longer than expected" — we don't give up (a worker turn can still
  // land), but we stop the fast poll, mark it stalled (which unlocks the
  // composer and shows a notice), and keep polling slowly so a late reply still
  // arrives. A successful load() clears `stalled` and resumes normal state.
  const livePoll = agentWorking || rescreenPending;
  useEffect(() => {
    if (!livePoll) return undefined;
    const fast = agentWorking ? 2500 : 5000;
    let poll = window.setInterval(() => { void load({ silent: true }); }, fast);
    const stop = window.setTimeout(() => {
      window.clearInterval(poll);
      setStalled(true);
      // Keep a slow heartbeat so a late reply still lands, without hammering.
      poll = window.setInterval(() => { void load({ silent: true }); }, 20000);
    }, 6 * 60 * 1000);
    return () => { window.clearInterval(poll); window.clearTimeout(stop); };
  }, [livePoll, agentWorking, load]);

  // Decisions can arrive from autonomous cycles or another teammate even
  // when this dock has no turn in flight. A low-frequency visible-tab refresh
  // keeps the open timeline current; live work uses the faster poll above.
  useEffect(() => {
    if (!roleId || isBulk || livePoll) return undefined;
    const refresh = () => {
      if (typeof document === 'undefined' || document.visibilityState === 'visible') {
        void load({ silent: true });
      }
    };
    const poll = window.setInterval(refresh, 30000);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      window.clearInterval(poll);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [roleId, isBulk, livePoll, load]);

  // A stalled turn re-checks once when the recruiter returns to the tab, so a
  // reply that landed while backgrounded surfaces without waiting for the poll.
  useEffect(() => {
    if (!stalled) return undefined;
    const onFocus = () => { if (document.visibilityState === 'visible') void load({ silent: true }); };
    document.addEventListener('visibilitychange', onFocus);
    return () => document.removeEventListener('visibilitychange', onFocus);
  }, [stalled, load]);

  // Notify when the agent's reply lands while you're not looking at this thread
  // (tab hidden). Only fires on a real same-thread working→idle transition, so
  // switching agents can't trigger a false "replied". Replies to OTHER agents
  // are surfaced by the Home rail's unread poll instead.
  const workSnapRef = useRef({ working: false, role: roleId });
  useEffect(() => {
    const prev = workSnapRef.current;
    if (prev.role === roleId && prev.working && !agentWorking
        && typeof document !== 'undefined' && document.visibilityState !== 'visible') {
      showToast?.(`${roleName || 'The agent'} replied`, 'success');
    }
    workSnapRef.current = { working: agentWorking, role: roleId };
  }, [agentWorking, roleId, roleName, showToast]);

  return (
    <ChatSurface as="aside" className="ac-dock" density="compact" tone="agent">
      <div className="ac-dock-head">
        {isBulk ? <Users size={15} /> : <MessageSquare size={15} />}
        {isBulk ? (
          <>
            <span>Messaging {bulkSelectedRoles.length} agent{bulkSelectedRoles.length === 1 ? '' : 's'}</span>
            {onClearBulk && (
              <button className="ac-dock-collapse" title="Cancel" onClick={onClearBulk}>
                <X size={16} />
              </button>
            )}
          </>
        ) : (
          <>
            <span>Ask the agent</span>
            {roleName && <span className="ac-dock-role">{roleName}</span>}
            {openQuestions.length > 0 ? (
              <button
                type="button"
                className="tk-agent-question-shortcut"
                aria-label={`${openQuestions.length} ${openQuestions.length === 1 ? 'question needs' : 'questions need'} your input`}
                onClick={jumpToOldestQuestion}
              >
                <CircleHelp size={13} />
                <MotionAttentionBadge
                  value={openQuestions.length}
                  className="tk-agent-question-shortcut-count"
                />
                <span className="tk-agent-question-shortcut-label">need input</span>
              </button>
            ) : null}
            {onCollapse && (
              <button className="ac-dock-collapse" title="Collapse" onClick={onCollapse}>
                <PanelRightClose size={16} />
              </button>
            )}
          </>
        )}
      </div>

      <div className="ac-stream" id={timelineRegionId} ref={scrollRef}>
        {isBulk ? (
          <div className="ac-bulk-panel">
            <div className="ac-bulk-title">One message → {bulkSelectedRoles.length} agents</div>
            <p className="ac-bulk-note">
              Runs on each role's own agent, in its own thread — the audit stays per role. Replies land in
              each thread; re-screens still ask before spending, role by role.
            </p>
            <div className="ac-bulk-roles">
              {bulkSelectedRoles.map((r) => (
                <span key={r.role_id} className="ac-bulk-role-chip">{r.role_name}</span>
              ))}
            </div>
          </div>
        ) : loading && items.length === 0 ? (
          <div className="ac-empty">Loading the conversation…</div>
        ) : items.length === 0 && !sending && !agentWorking ? (
          <ChatEmptyState
            compact
            title={<>What should this agent do<em>?</em></>}
            sub={<>Ask about <b>{roleName || 'this role'}</b>’s pool, or tell the agent to change something — it acts and shows the impact.</>}
            suggestions={agentEnabled === false ? OFF_SUGGESTIONS : ON_SUGGESTIONS}
            onPick={(t) => submitComposer(t)}
          />
        ) : (
          <RoleAgentTimeline
            items={items}
            className="ac-timeline"
            roleId={roleId}
            roleName={roleName}
            openQuestionPositions={openQuestionPositions}
            openQuestionCount={openQuestions.length}
            onAnswer={answer}
            onDismiss={dismiss}
            onPrompt={prefillPrompt}
            onReply={beginReply}
            decisionDetails={decisionDetails}
            decisionDetailsLoading={decisionDetailsLoading}
            decisionDetailsError={decisionDetailsError}
            onRetryDecisionDetails={refreshDecisionDetails}
            onDecisionChanged={refreshAfterDecision}
            renderAction={(card) => (
              card.type === 'candidate_evidence' ? (
                <CandidateEvidenceCard data={card} />
              ) : card.type === 'draft_task_review' ? (
                <DraftTaskCard
                  card={card}
                  onApprove={approveDraft}
                  onRevise={reviseDraft}
                  busy={sending}
                />
              ) : (
                <ImpactCard
                  card={card}
                  onApply={(threshold) => send(`Set the score cut-off to ${threshold}.`)}
                  onPrompt={prefillPrompt}
                  busy={sending}
                />
              )
            )}
          />
        )}
        {(sending || agentWorking) && !stalled && (
          <ChatMessage role="assistant">
            <ThinkingDots label="Working…" />
          </ChatMessage>
        )}
        {stalled && !sending && (
          <div className="tk-agent-working">
            <AgentLoop kind="pulse" className="tk-agent-working-pulse" /> This is taking longer than expected — still running. You can send another message, or check back shortly.
          </div>
        )}
        {rescreenPending && !sending && (
          <div className="tk-agent-working">
            <AgentLoop kind="pulse" className="tk-agent-working-pulse" /> Re-screening candidates… I’ll post the impact here when it lands.
          </div>
        )}
      </div>

      <div className="ac-dock-composer">
        <NewMessageNotice
          visible={hasNewAgentUpdate}
          onClick={jumpToLatest}
          controls={timelineRegionId}
          className="ac-new-update"
        />
        {composerAnnouncement ? (
          <span className="tk-composer-status" role="status" aria-live="polite" aria-atomic="true">
            {composerAnnouncement}
          </span>
        ) : null}
        <ChatComposer
          ref={composerRef}
          value={input}
          onChange={setInput}
          onSubmit={replying ? submitReply : submitComposer}
          replyTo={replyTo}
          onCancelReply={cancelReply}
          placeholder={
            isBulk
              ? `Message ${bulkSelectedRoles.length} agents at once…`
              : (agentWorking && !stalled)
                ? 'The agent is working on your last message…'
                // Matches the home-preview composer ("Message the {role} agent…");
                // falls back to a generic prompt when no role name is loaded.
                : roleName
                  ? `Message the ${roleName} agent…`
                  : "Message this role's agent…"
          }
          busy={replySubmitting || ((sending || (agentWorking && !stalled)) && !isBulk)}
        />
      </div>
    </ChatSurface>
  );
}
