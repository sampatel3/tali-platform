// Agents tab of the Search page — converse with one role's autonomous agent.
// This is the same conversation the Home dock drives: both read and write the
// shared /agent-chat/conversations/:roleId/* thread, so a message sent here
// shows up there (and vice-versa) the next time either surface loads. We keep
// this panel self-contained (its own timeline fetch + send) rather than lifting
// the dock's internals so the Home page stays untouched.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { MessageSquare, PanelLeft, Sparkles } from 'lucide-react';

import { agentChat } from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import { ChatComposer, ChatEmptyState, ChatMarkdown, ChatMessage, ThinkingDots } from '../../shared/chat';
import { DraftTaskCard, ImpactCard, NeedsInputCard } from '../home/agentchat/cards.jsx';
import CandidateEvidenceCard from './CandidateEvidenceCard';

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
  const [sending, setSending] = useState(false);
  const [input, setInput] = useState('');
  // Durable "agent is working…" — driven by the server (the turn runs in a
  // worker), so it survives navigation / an agent switch and resumes on return.
  const [agentWorking, setAgentWorking] = useState(false);
  const scrollRef = useRef(null);
  // Guards async results against an agent switch (see AgentChatDock).
  const activeRoleRef = useRef(roleId);
  useEffect(() => { activeRoleRef.current = roleId; }, [roleId]);

  // Whether the user was at the bottom *before* the latest timeline update —
  // tracked on scroll so a recruiter reading back through evidence cards
  // mid-turn keeps their position instead of being yanked down on every
  // 2.5s poll tick, while someone at the bottom keeps following the turn.
  const pinnedRef = useRef(true);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return undefined;
    const onScroll = () => {
      pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  }, []);

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
        return `${t.length}|${last?.id ?? ''}|${last?.text?.length ?? 0}|${last?.actions?.length ?? 0}`;
      };
      setTimeline((prev) => (sig(prev) === sig(next) ? prev : next));
      setAgentWorking(Boolean(data.agent_working));
    } catch {
      if (!opts.silent && activeRoleRef.current === forRole) setTimeline([]);
    } finally {
      if (!opts.silent && activeRoleRef.current === forRole) setLoading(false);
    }
  }, [roleId]);

  useEffect(() => {
    setTimeline([]);
    setAgentWorking(false);
    load();
  }, [load]);

  useEffect(() => {
    scrollToBottom();
  }, [timeline, sending, scrollToBottom]);

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
    async (needsInputId, response) => {
      try {
        await agentChat.answerNeedsInput(needsInputId, response);
        load();
        onAfterSend?.();
      } catch {
        showToast?.('Couldn’t record that answer.', 'error');
      }
    },
    [load, onAfterSend, showToast]
  );

  const dismiss = useCallback(
    async (needsInputId) => {
      try {
        await agentChat.dismissNeedsInput(needsInputId);
        load();
      } catch {
        showToast?.('Couldn’t dismiss that.', 'error');
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
        onAfterSend?.();
      } catch (err) {
        showToast?.(err?.response?.data?.detail || 'Couldn’t approve that draft.', 'error');
      } finally {
        setSending(false);
      }
    },
    [roleId, sending, onAfterSend, showToast]
  );

  const reviseDraft = useCallback(
    async (taskId, feedback) => {
      if (!roleId || sending) return;
      setSending(true);
      try {
        const { data } = await agentChat.reviseDraftTask(roleId, taskId, feedback);
        setTimeline(data.timeline || []);
        onAfterSend?.();
      } catch (err) {
        showToast?.(err?.response?.data?.detail || 'Couldn’t revise that draft.', 'error');
        load();
      } finally {
        setSending(false);
      }
    },
    [roleId, sending, onAfterSend, showToast, load]
  );

  // Chat + questions only — decisions live on the Home feed, same as the dock.
  const items = useMemo(
    () => timeline.filter((it) => it.kind === 'message' || it.kind === 'needs_input'),
    [timeline]
  );

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
      </header>
      <div className="cp-scroll" ref={scrollRef}>
        {loading && items.length === 0 ? (
          <div className="cp-thread">
            <ChatMessage role="assistant"><ThinkingDots label="Loading the conversation…" /></ChatMessage>
          </div>
        ) : items.length === 0 && !sending && !agentWorking ? (
          <ChatEmptyState
            title={<>What should this agent do<em>?</em></>}
            sub={<>Ask about <b>{roleName || 'this role'}</b>’s pool, or tell the agent to change something — it acts and shows the impact here.</>}
            suggestions={agentEnabled === false ? OFF_SUGGESTIONS : ON_SUGGESTIONS}
            onPick={(t) => send(t)}
          />
        ) : (
          <div className="cp-thread">
            {items.map((it) =>
              it.kind === 'needs_input' ? (
                <NeedsInputCard key={it.id} item={it} onAnswer={answer} onDismiss={dismiss} />
              ) : (
                <ChatMessage
                  key={it.id}
                  role={it.author === 'agent' ? 'assistant' : 'user'}
                  text={it.author === 'agent' ? undefined : it.text}
                  time={it.created_at}
                >
                  {/* Agent replies carry the mono "AGENT" kicker above the prose
                      (home dock `.ac-agent-say` / `.ac-who`); the shared
                      <ChatMarkdown> keeps the prose identical to every other
                      chat surface. User messages stay the plain ink pill. */}
                  {it.author === 'agent' ? (
                    <div className="cp-agent-say">
                      <span className="cp-who">Agent</span>
                      {it.text ? <ChatMarkdown>{it.text}</ChatMarkdown> : null}
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
                      <ImpactCard key={i} card={card} onApply={(t) => send(`Set the score cut-off to ${t}.`)} busy={sending} />
                    )
                  )}
                </ChatMessage>
              )
            )}
            {(sending || agentWorking) && (
              <ChatMessage role="assistant">
                <ThinkingDots label="Working…" />
              </ChatMessage>
            )}
            {rescreenPending && !sending && !agentWorking && (
              <div className="ac-rescreen-live">
                <span className="ac-pulse" /> Re-screening candidates… I’ll post the impact here when it lands.
              </div>
            )}
          </div>
        )}
      </div>
      <div className="cp-composer-wrap">
        <ChatComposer
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
