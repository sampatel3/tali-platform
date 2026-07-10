// The chat dock (Option C) — converse with one role's agent. Fetches the
// merged timeline, renders chat + the agent's questions + impact cards, and
// posts messages that run the agent turn. Decisions live in the main feed, so
// the dock filters them out and stays focused on steering.

import { useCallback, useEffect, useRef, useState } from 'react';
import { MessageSquare, PanelRightClose, Users, X } from 'lucide-react';

import { agentChat } from '../../../shared/api';
import { useToast } from '../../../context/ToastContext';
import { ChatComposer, ChatEmptyState, ChatMarkdown, ChatMessage, ThinkingDots } from '../../../shared/chat';
import { DraftTaskCard, ImpactCard, NeedsInputCard, PendingRejectSweepCard } from './cards.jsx';
import CandidateEvidenceCard from '../../chat/CandidateEvidenceCard';

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
  const scrollRef = useRef(null);
  // Guards async results against an agent switch: a slow turn or fetch that
  // resolves after you've moved to another agent must not clobber the new
  // thread (the message itself is safe — it's already persisted server-side).
  const activeRoleRef = useRef(roleId);
  useEffect(() => { activeRoleRef.current = roleId; }, [roleId]);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  const load = useCallback(async (opts = {}) => {
    if (!roleId) return;
    const forRole = roleId;
    if (!opts.silent) setLoading(true);
    try {
      const { data } = await agentChat.getTimeline(roleId);
      if (activeRoleRef.current !== forRole) return; // switched away mid-fetch
      setTimeline(data.timeline || []);
      setAgentWorking(Boolean(data.agent_working));
    } catch {
      if (!opts.silent && activeRoleRef.current === forRole) setTimeline([]);
    } finally {
      if (!opts.silent && activeRoleRef.current === forRole) setLoading(false);
    }
  }, [roleId]);

  useEffect(() => {
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
    [roleId, sending, agentWorking, onReload, showToast, load]
  );

  const answer = useCallback(
    async (needsInputId, response) => {
      try {
        await agentChat.answerNeedsInput(needsInputId, response);
        load();
        onReload?.();
      } catch {
        showToast?.('Couldn’t record that answer.', 'error');
      }
    },
    [load, onReload, showToast]
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

  const applyPendingRejects = useCallback(async () => {
    if (!roleId || sending) return;
    setSending(true);
    try {
      const { data } = await agentChat.applyPendingRejects(roleId);
      setTimeline(data.timeline || []);
      // The pending queue just went in-flight — refresh the feed counts.
      onReload?.();
    } catch (err) {
      showToast?.(err?.response?.data?.detail || 'Couldn’t apply that to the pending queue.', 'error');
      load();
    } finally {
      setSending(false);
    }
  }, [roleId, sending, onReload, showToast, load]);

  const dismissPendingRejects = useCallback(async () => {
    if (!roleId || sending) return;
    setSending(true);
    try {
      const { data } = await agentChat.dismissPendingRejects(roleId);
      setTimeline(data.timeline || []);
    } catch (err) {
      showToast?.(err?.response?.data?.detail || 'Couldn’t dismiss that.', 'error');
      load();
    } finally {
      setSending(false);
    }
  }, [roleId, sending, showToast, load]);

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

  // Option C: show chat + questions only; decisions are in the feed.
  const items = timeline.filter((it) => it.kind === 'message' || it.kind === 'needs_input');

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
  const livePoll = agentWorking || rescreenPending;
  useEffect(() => {
    if (!livePoll) return undefined;
    const every = agentWorking ? 2500 : 5000;
    const poll = window.setInterval(() => { void load({ silent: true }); }, every);
    const stop = window.setTimeout(() => window.clearInterval(poll), 6 * 60 * 1000);
    return () => { window.clearInterval(poll); window.clearTimeout(stop); };
  }, [livePoll, agentWorking, load]);

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
    <aside className="ac-dock">
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
            {onCollapse && (
              <button className="ac-dock-collapse" title="Collapse" onClick={onCollapse}>
                <PanelRightClose size={16} />
              </button>
            )}
          </>
        )}
      </div>

      <div className="ac-stream" ref={scrollRef}>
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
          items.map((it) => {
            if (it.kind === 'needs_input') {
              return <NeedsInputCard key={it.id} item={it} onAnswer={answer} onDismiss={dismiss} />;
            }
            const isAgent = it.author === 'agent';
            const cards = (it.actions || []).map((card, i) =>
              card.type === 'candidate_evidence' ? (
                <CandidateEvidenceCard key={i} data={card} />
              ) : card.type === 'pending_reject_sweep' ? (
                <PendingRejectSweepCard
                  key={i}
                  card={card}
                  onApply={applyPendingRejects}
                  onDismiss={dismissPendingRejects}
                  busy={sending}
                />
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
            );
            // Agent replies carry a mono "Agent" attribution label above the
            // text (home-preview `.msg.bot .who`). We render the label + markdown
            // as children (no `text` prop) so the label sits above the bubble
            // body; the shared <ChatMarkdown> keeps the prose styling identical
            // to every other chat surface. User messages stay the plain ink pill.
            if (isAgent) {
              return (
                <ChatMessage key={it.id} role="assistant" time={it.created_at}>
                  <div className="ac-agent-say">
                    <span className="ac-who">Agent</span>
                    {it.text ? <ChatMarkdown>{it.text}</ChatMarkdown> : null}
                  </div>
                  {cards}
                </ChatMessage>
              );
            }
            return (
              <ChatMessage key={it.id} role="user" text={it.text} time={it.created_at} />
            );
          })
        )}
        {(sending || agentWorking) && (
          <ChatMessage role="assistant">
            <ThinkingDots label="Working…" />
          </ChatMessage>
        )}
        {rescreenPending && !sending && (
          <div className="ac-rescreen-live">
            <span className="ac-pulse" /> Re-screening candidates… I’ll post the impact here when it lands.
          </div>
        )}
      </div>

      <div className="ac-dock-composer">
        <ChatComposer
          value={input}
          onChange={setInput}
          onSubmit={submitComposer}
          placeholder={
            isBulk
              ? `Message ${bulkSelectedRoles.length} agents at once…`
              : agentWorking
                ? 'The agent is working on your last message…'
                // Matches the home-preview composer ("Message the {role} agent…");
                // falls back to a generic prompt when no role name is loaded.
                : roleName
                  ? `Message the ${roleName} agent…`
                  : "Message this role's agent…"
          }
          busy={(sending || agentWorking) && !isBulk}
        />
      </div>
    </aside>
  );
}
