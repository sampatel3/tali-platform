// The chat dock (Option C) — converse with one role's agent. Fetches the
// merged timeline, renders chat + the agent's questions + impact cards, and
// posts messages that run the agent turn. Decisions live in the main feed, so
// the dock filters them out and stays focused on steering.

import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUp, MessageSquare, PanelRightClose, Users, X } from 'lucide-react';

import { agentChat } from '../../../shared/api';
import { useToast } from '../../../context/ToastContext';
import { ChatBubble, DraftTaskCard, ImpactCard, NeedsInputCard, ThinkingBubble } from './cards.jsx';

const HINTS = [
  'cap salary at AED 25k',
  'what if I drop the cut-off to 60?',
  'bring 5 more through',
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
  const scrollRef = useRef(null);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  const load = useCallback(async (opts = {}) => {
    if (!roleId) return;
    if (!opts.silent) setLoading(true);
    try {
      const { data } = await agentChat.getTimeline(roleId);
      setTimeline(data.timeline || []);
    } catch {
      if (!opts.silent) setTimeline([]);
    } finally {
      if (!opts.silent) setLoading(false);
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
      if (!msg || sending || !roleId) return;
      setInput('');
      setSending(true);
      // Optimistic: show the recruiter's message immediately.
      setTimeline((t) => [
        ...t,
        { kind: 'message', id: `local-${t.length}`, author: 'recruiter', text: msg, created_at: new Date().toISOString() },
      ]);
      try {
        const { data } = await agentChat.sendMessage(roleId, msg);
        setTimeline(data.timeline || []);
        // The turn may have changed constraints/thresholds → refresh the feed.
        onReload?.();
      } catch (err) {
        showToast?.(err?.response?.data?.detail || 'The agent couldn’t complete that. Try again.', 'error');
        load();
      } finally {
        setSending(false);
      }
    },
    [roleId, sending, onReload, showToast, load]
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

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submitComposer(input);
    }
  };

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

  useEffect(() => {
    if (!rescreenPending) return undefined;
    const poll = window.setInterval(() => { void load({ silent: true }); }, 5000);
    const stop = window.setTimeout(() => window.clearInterval(poll), 6 * 60 * 1000);
    return () => { window.clearInterval(poll); window.clearTimeout(stop); };
  }, [rescreenPending, load]);

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
        ) : items.length === 0 && !sending ? (
          <DockEmptyState roleName={roleName} agentEnabled={agentEnabled} onPick={(t) => submitComposer(t)} />
        ) : (
          items.map((it) =>
            it.kind === 'needs_input' ? (
              <NeedsInputCard key={it.id} item={it} onAnswer={answer} onDismiss={dismiss} />
            ) : (
              <ChatBubble key={it.id} item={it}>
                {(it.actions || []).map((card, i) =>
                  card.type === 'draft_task_review' ? (
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
              </ChatBubble>
            )
          )
        )}
        {sending && <ThinkingBubble />}
        {rescreenPending && !sending && (
          <div className="ac-rescreen-live">
            <span className="ac-pulse" /> Re-screening candidates… I’ll post the impact here when it lands.
          </div>
        )}
      </div>

      <div className="ac-composer">
        <div className="ac-composer-well">
          <textarea
            rows={1}
            value={input}
            placeholder={
              isBulk
                ? `Message ${bulkSelectedRoles.length} agents at once…`
                : "Ask about this role's pool, or tell the agent to change something"
            }
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={sending}
          />
          <button
            className="ac-send"
            disabled={sending || !input.trim()}
            onClick={() => submitComposer(input)}
          >
            <ArrowUp size={15} />
          </button>
        </div>
        {!isBulk && (
          <div className="ac-hints">
            {HINTS.map((h) => (
              <button key={h} className="ac-hint-chip" disabled={sending} onClick={() => submitComposer(h)}>
                {h}
              </button>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}

// Search-page-aligned empty state: a heading + role-scoped suggestion cards.
// Off roles get an "activate" suggestion (the agent's set_agent_state tool
// turns it on), so you can light up an agent straight from Home.
function DockEmptyState({ roleName, agentEnabled, onPick }) {
  const suggestions = agentEnabled === false
    ? [
        'Turn the agent on at $50/month',
        'What would you screen for on this role?',
        'Show me who is waiting on a decision',
      ]
    : [
        'Cap salary at AED 25k',
        'Who in the pool is based in MENA?',
        'Drop the score cut-off to 65',
        'Show me the draft tasks',
      ];
  return (
    <div className="ac-dock-empty">
      <div className="ac-dock-empty-glyph" aria-hidden="true">
        <MessageSquare size={20} />
      </div>
      <h3 className="ac-dock-empty-h">
        What should this agent do<em>?</em>
      </h3>
      <p className="ac-dock-empty-sub">
        Ask about <b>{roleName || 'this role'}</b>’s pool, or tell the agent to change something — it acts and shows the impact.
      </p>
      <div className="ac-dock-suggest">
        {suggestions.map((s) => (
          <button key={s} type="button" className="ac-dock-sugg" onClick={() => onPick(s)}>
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
