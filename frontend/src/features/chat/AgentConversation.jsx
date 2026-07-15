// Agents tab of the Search page — converse with one role's autonomous agent.
// This is the same conversation the Home dock drives: both read and write the
// shared /agent-chat/conversations/:roleId/* thread, so a message sent here
// shows up there (and vice-versa) the next time either surface loads. We keep
// this panel self-contained (its own timeline fetch + send) rather than lifting
// the dock's internals so the Home page stays untouched.

import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { CircleHelp, MessageSquare, PanelLeft, Sparkles } from 'lucide-react';

import { agentChat } from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import {
  AgentPromptCard,
  ChatComposer,
  ChatEmptyState,
  ChatMarkdown,
  ChatMessage,
  NewMessageNotice,
  ThinkingDots,
  useAgentUpdateAwareness,
} from '../../shared/chat';
import { DraftTaskCard, ImpactCard } from '../home/agentchat/cards.jsx';
import CandidateEvidenceCard from './CandidateEvidenceCard';
import {
  AgentLoop,
  MotionAttentionBadge,
  MotionChatItem,
  MotionList,
  motionSafeScrollBehavior,
} from '../../shared/motion';
import { AgentDecisionTimelineCard } from '../../shared/decisions/AgentDecisionTimelineCard';
import { useRoleDecisionDetails } from '../../shared/decisions/useRoleDecisionDetails';

const ON_SUGGESTIONS = [
  'Who in the pool is based in MENA?',
  'Cap salary at AED 25k',
  'Drop the score cut-off to 65',
  'Show me the draft tasks',
];
const OFF_SUGGESTIONS = [
  'Turn the agent on at $50/month',
  'What would you screen for on this role?',
  'Show me who is waiting on a decision',
];
const READ_ACK_DELAY_MS = 1000;

const AgentConversation = ({
  roleId,
  roleName,
  agentEnabled = true,
  onAfterSend,
  // Mobile only: opens the conversation/agent list drawer.
  onOpenList,
}) => {
  const { showToast } = useToast() || { showToast: () => {} };
  const [timeline, setTimeline] = useState([]);
  const [loading, setLoading] = useState(true);
  // Set when a timeline fetch fails so we can show a quiet "couldn't
  // refresh" row instead of collapsing a real conversation into the
  // suggestion prompts. Cleared on the next successful load.
  const [loadError, setLoadError] = useState(false);
  const [sending, setSending] = useState(false);
  const [input, setInput] = useState('');
  // Durable "agent is working…" — driven by the server (the turn runs in a
  // worker), so it survives navigation / an agent switch and resumes on return.
  const [agentWorking, setAgentWorking] = useState(false);
  const [loadedRoleId, setLoadedRoleId] = useState(null);
  const scrollRef = useRef(null);
  const composerRef = useRef(null);
  const timelineRegionId = useId();
  const [composerAnnouncement, setComposerAnnouncement] = useState('');
  // Guards async results against an agent switch (see AgentChatDock).
  const activeRoleRef = useRef(roleId);
  useEffect(() => { activeRoleRef.current = roleId; }, [roleId]);

  const load = useCallback(async (opts = {}) => {
    if (!roleId) return;
    const forRole = roleId;
    if (!opts.silent) setLoading(true);
    try {
      const { data } = await agentChat.getTimeline(roleId);
      if (activeRoleRef.current !== forRole) return;
      const next = data.timeline || [];
      // Skip the state churn when a silent poll returns an unchanged
      // timeline — a fresh array reference would re-render and re-scroll
      // the whole thread every 2.5s even though nothing moved. The last
      // item can still grow in place during a turn (text streams in, action
      // cards attach), so the signature covers its text + action count too.
      const sig = (t) => {
        const last = t[t.length - 1];
        const decisions = t
          .filter((item) => item?.kind === 'decision')
          .map((item) => `${item.decision_id ?? item.id}:${item.status ?? ''}:${item.resolved_at ?? ''}`)
          .join(',');
        const recruiterInputs = t
          .filter((item) => item?.kind === 'needs_input')
          .map((item) => `${item.needs_input_id ?? item.id}:${item.status ?? ''}:${item.resolved_at ?? ''}:${JSON.stringify(item.response ?? null)}`)
          .join(',');
        return `${t.length}|${last?.id ?? ''}|${last?.text?.length ?? 0}|${last?.actions?.length ?? 0}|${decisions}|${recruiterInputs}`;
      };
      setTimeline((prev) => (sig(prev) === sig(next) ? prev : next));
      setAgentWorking(Boolean(data.agent_working));
      setLoadedRoleId(forRole);
      if (activeRoleRef.current === forRole) setLoadError(false);
    } catch {
      // Keep whatever we last had on screen — clearing to [] would drop a
      // real conversation back to the suggestion prompts on a transient
      // fetch blip. Flag the error so the thread can show a quiet retry row.
      if (activeRoleRef.current === forRole) setLoadError(true);
    } finally {
      if (!opts.silent && activeRoleRef.current === forRole) setLoading(false);
    }
  }, [roleId]);

  useEffect(() => {
    setTimeline([]);
    setAgentWorking(false);
    setLoadedRoleId(null);
    setLoadError(false);
    load();
  }, [load]);

  // Timeline refresh and read acknowledgement are intentionally separate. A
  // successful load must remain selected in a visible tab for a short dwell
  // before we consume the role's unread agent-message badge.
  useEffect(() => {
    if (!roleId || loadedRoleId !== roleId) return undefined;
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
  }, [loadedRoleId, roleId]);

  const send = useCallback(
    async (text) => {
      const msg = (text || '').trim();
      if (!msg || sending || agentWorking || !roleId) return;
      const forRole = roleId;
      setInput('');
      setSending(true);
      setTimeline((t) => [
        ...t,
        { kind: 'message', id: `local-${t.length}`, author: 'recruiter', text: msg, created_at: new Date().toISOString() },
      ]);
      try {
        // Persists the message + accepts the turn; the reply arrives via the
        // poll. Dropped if you switch agents mid-flight (it's safe server-side).
        const { data } = await agentChat.sendMessage(forRole, msg);
        if (activeRoleRef.current !== forRole) return;
        setTimeline(data.timeline || []);
        setAgentWorking(data.agent_working !== false);
        onAfterSend?.();
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
    [roleId, sending, agentWorking, onAfterSend, showToast, load]
  );

  const answer = useCallback(
    async (needsInputId, response, expectedVersion) => {
      try {
        const answerArgs = expectedVersion == null
          ? [needsInputId, response]
          : [needsInputId, response, expectedVersion];
        await agentChat.answerNeedsInput(...answerArgs);
        load();
        onAfterSend?.();
        return true;
      } catch (err) {
        showToast?.(
          err?.response?.data?.detail?.message || 'Couldn’t record that answer.',
          err?.response?.status === 409 ? 'info' : 'error',
        );
        load();
        return false;
      }
    },
    [load, onAfterSend, showToast]
  );

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
    async (taskId, expectedVersion) => {
      if (!roleId || sending) return;
      setSending(true);
      try {
        const { data } = await agentChat.approveDraftTask(roleId, taskId, expectedVersion);
        setTimeline(data.timeline || []);
        onAfterSend?.();
      } catch (err) {
        showToast?.(
          err?.response?.data?.detail?.message || err?.response?.data?.detail || 'Couldn’t approve that draft.',
          err?.response?.status === 409 ? 'info' : 'error',
        );
        load();
      } finally {
        setSending(false);
      }
    },
    [roleId, sending, onAfterSend, showToast, load]
  );

  const reviseDraft = useCallback(
    async (taskId, feedback, expectedVersion) => {
      if (!roleId || sending) return;
      setSending(true);
      try {
        const { data } = await agentChat.reviseDraftTask(roleId, taskId, {
          ...feedback,
          expectedVersion,
        });
        setTimeline(data.timeline || []);
        onAfterSend?.();
      } catch (err) {
        showToast?.(
          err?.response?.data?.detail?.message || err?.response?.data?.detail || 'Couldn’t revise that draft.',
          err?.response?.status === 409 ? 'info' : 'error',
        );
        load();
      } finally {
        setSending(false);
      }
    },
    [roleId, sending, onAfterSend, showToast, load]
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

  const {
    byId: decisionDetails,
    loading: decisionDetailsLoading,
    error: decisionDetailsError,
    refresh: refreshDecisionDetails,
  } = useRoleDecisionDetails(roleId, timeline);

  const refreshAfterDecision = useCallback(async () => {
    await Promise.all([
      load({ silent: true }),
      refreshDecisionDetails(),
    ]);
    onAfterSend?.();
  }, [load, onAfterSend, refreshDecisionDetails]);

  // The role thread is the complete chronological work surface: conversation,
  // open questions, and the agent's HITL decisions.
  const items = useMemo(
    () => timeline.filter(
      (it) => it.kind === 'message' || it.kind === 'needs_input' || it.kind === 'decision',
    ),
    [timeline]
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
    ready: Boolean(roleId) && loadedRoleId === roleId,
    scopeKey: roleId,
    scrollRef,
  });

  // Mirror the dock's "re-screen in flight" affordance so a constraint edit's
  // follow-up impact lands without a manual refresh.
  const rescreenPending = useMemo(() => {
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
    return lastRescreenIdx >= 0 && lastRescreenIdx === lastAgentIdx;
  }, [items]);

  // Poll while a turn is in flight (fast) or a re-screen follow-up is pending
  // (slower). load() flips agentWorking off when the reply lands → poll stops.
  const livePoll = agentWorking || rescreenPending;
  useEffect(() => {
    if (!livePoll) return undefined;
    const every = agentWorking ? 2500 : 5000;
    const poll = window.setInterval(() => { void load({ silent: true }); }, every);
    const stop = window.setTimeout(() => window.clearInterval(poll), 6 * 60 * 1000);
    return () => { window.clearInterval(poll); window.clearTimeout(stop); };
  }, [livePoll, agentWorking, load]);

  // Autonomous cycles and teammate actions can add decisions while no chat
  // turn is running. Keep an open transcript eventually consistent so those
  // cards appear without navigation; the faster in-flight poll above takes
  // over whenever work is active.
  useEffect(() => {
    if (!roleId || livePoll) return undefined;
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
  }, [roleId, livePoll, load]);

  // Toast when the reply lands while you're not looking at this thread (tab
  // hidden); only on a real same-thread working→idle transition.
  const workSnapRef = useRef({ working: false, role: roleId });
  useEffect(() => {
    const prev = workSnapRef.current;
    if (prev.role === roleId && prev.working && !agentWorking
        && typeof document !== 'undefined' && document.visibilityState !== 'visible') {
      showToast?.(`${roleName || 'The agent'} replied`, 'success');
    }
    workSnapRef.current = { working: agentWorking, role: roleId };
  }, [agentWorking, roleId, roleName, showToast]);

  // No role resolved yet (no live agents, or before the auto-select lands):
  // show a calm placeholder rather than a spinner that never resolves.
  if (!roleId) {
    return (
      <div className="cp-center">
        <header className="cp-head">
          {onOpenList ? (
            <button type="button" className="cp-mobile-menu" onClick={onOpenList} aria-label="Show agents">
              <PanelLeft size={18} />
            </button>
          ) : null}
          <span className="cp-head-lead"><MessageSquare size={15} /> Ask the agent</span>
        </header>
        <div className="cp-scroll">
          <ChatEmptyState
            title={<>Pick an agent to steer<em>.</em></>}
            sub="Each live role has its own agent. Choose one to see its thread, ask about the pool, or change how it screens — the same conversation you’d see on Home."
          />
        </div>
      </div>
    );
  }

  return (
    <div className="cp-center">
      <header className="cp-head">
        {onOpenList ? (
          <button
            type="button"
            className="cp-mobile-menu"
            onClick={onOpenList}
            aria-label="Show agents"
          >
            <PanelLeft size={18} />
          </button>
        ) : null}
        {/* Dock-style head (Home `.ac-dock-head`): a chat glyph + "Ask the
            agent" + the role pill, with the agent on/off state pushed to the
            right edge. */}
        <span className="cp-head-lead"><MessageSquare size={15} /> Ask the agent</span>
        {roleName ? <span className="cp-head-role">{roleName}</span> : null}
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
      </header>
      <div className="cp-scroll" id={timelineRegionId} ref={scrollRef}>
        {loading && items.length === 0 ? (
          <div className="cp-thread">
            <ChatMessage role="assistant"><ThinkingDots label="Loading the conversation…" /></ChatMessage>
          </div>
        ) : loadError && items.length === 0 ? (
          // Fetch failed with nothing to fall back on — offer a retry rather
          // than the empty-state prompts, which would misleadingly imply the
          // conversation is empty.
          <div className="cp-thread">
            <div className="cp-refresh-row">
              Couldn’t load the conversation.
              <button type="button" className="taali-text-btn cp-refresh-retry" onClick={() => load()}>
                Try again
              </button>
            </div>
          </div>
        ) : items.length === 0 && !sending && !agentWorking ? (
          <ChatEmptyState
            title={<>What should this agent do<em>?</em></>}
            sub={<>Ask about <b>{roleName || 'this role'}</b>’s pool, or tell the agent to change something — it acts and shows the impact here.</>}
            suggestions={agentEnabled === false ? OFF_SUGGESTIONS : ON_SUGGESTIONS}
            onPick={(t) => send(t)}
          />
        ) : (
          <MotionList className="cp-thread" aria-label="Agent conversation" layout={false}>
            {loadError ? (
              // A refresh blipped but we kept the last good thread on screen.
              <MotionChatItem key="refresh-error" className="tk-motion-row">
                <div className="cp-refresh-row">Couldn’t refresh — retrying.</div>
              </MotionChatItem>
            ) : null}
            {items.map((it) => {
              let content;
              if (it.kind === 'needs_input') {
                content = (
                  <AgentPromptCard
                    item={it}
                    onAnswer={answer}
                    onDismiss={dismiss}
                    onPrompt={prefillPrompt}
                    position={openQuestionPositions.get(it.needs_input_id ?? it.id)}
                    total={openQuestions.length}
                  />
                );
              } else if (it.kind === 'decision') {
                const decisionId = Number(it.decision_id);
                content = (
                  <AgentDecisionTimelineCard
                    item={it}
                    detail={decisionDetails[decisionId]}
                    roleId={roleId}
                    roleName={roleName}
                    detailsLoading={decisionDetailsLoading}
                    detailsError={decisionDetailsError}
                    onRetryDetails={refreshDecisionDetails}
                    onChanged={refreshAfterDecision}
                  />
                );
              } else {
                const suppressStructuredCopy = (it.message_kind === 'proactive'
                  && (it.actions || []).some((card) => card.type === 'helper_prompt'))
                  || (it.message_kind === 'event'
                    && (it.actions || []).some((card) => card.type === 'agent_event'));
                content = (
                  <ChatMessage
                    role={it.author === 'agent' ? 'assistant' : 'user'}
                    text={it.author === 'agent' ? undefined : it.text}
                    time={it.created_at}
                  >
                    {/* Agent replies carry the mono "AGENT" kicker above the prose.
                        User messages stay the plain ink pill. */}
                    {it.author === 'agent' ? (
                      <div className="cp-agent-say">
                        <span className="cp-who">Agent</span>
                        {it.text && !suppressStructuredCopy ? <ChatMarkdown>{it.text}</ChatMarkdown> : null}
                      </div>
                    ) : null}
                    {(it.actions || []).map((card, i) =>
                      card.type === 'candidate_evidence' ? (
                        <CandidateEvidenceCard key={i} data={card} />
                      ) : card.type === 'draft_task_review' ? (
                        <DraftTaskCard
                          key={i}
                          card={card}
                          onApprove={approveDraft}
                          onRevise={reviseDraft}
                          busy={sending}
                        />
                      ) : (
                        <ImpactCard
                          key={i}
                          card={card}
                          onApply={(t) => send(`Set the score cut-off to ${t}.`)}
                          onPrompt={prefillPrompt}
                          busy={sending}
                        />
                      )
                    )}
                  </ChatMessage>
                );
              }
              return (
                <MotionChatItem key={it.id} className="tk-motion-row">
                  {content}
                </MotionChatItem>
              );
            })}
            {(sending || agentWorking) && (
              <MotionChatItem key="agent-working" className="tk-motion-row">
                <ChatMessage role="assistant">
                  <ThinkingDots label="Working…" />
                </ChatMessage>
              </MotionChatItem>
            )}
            {rescreenPending && !sending && !agentWorking && (
              <MotionChatItem key="agent-rescreening" className="tk-motion-row">
                <div className="ac-rescreen-live">
                  <AgentLoop kind="pulse" className="ac-pulse" /> Re-screening candidates… I’ll post the impact here when it lands.
                </div>
              </MotionChatItem>
            )}
          </MotionList>
        )}
      </div>
      <div className="cp-composer-wrap">
        <NewMessageNotice
          visible={hasNewAgentUpdate}
          onClick={jumpToLatest}
          controls={timelineRegionId}
          className="cp-new-update"
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
          onSubmit={send}
          placeholder={
            agentWorking
              ? 'The agent is working on your last message…'
              : 'Ask about this role’s pool, or tell the agent to change something…'
          }
          busy={sending || agentWorking}
        />
      </div>
    </div>
  );
};

export default AgentConversation;
export { AgentConversation };
