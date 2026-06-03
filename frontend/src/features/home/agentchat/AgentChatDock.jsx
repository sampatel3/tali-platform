// The chat dock (Option C) — converse with one role's agent. Fetches the
// merged timeline, renders chat + the agent's questions + impact cards, and
// posts messages that run the agent turn. Decisions live in the main feed, so
// the dock filters them out and stays focused on steering.

import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUp, MessageSquare, PanelRightClose } from 'lucide-react';

import { agentChat } from '../../../shared/api';
import { useToast } from '../../../context/ToastContext';
import { Avatar, ChatBubble, ImpactCard, NeedsInputCard, ThinkingBubble } from './cards.jsx';

const HINTS = [
  'cap salary at AED 25k',
  'what if I drop the cut-off to 60?',
  'bring 5 more through',
];

export function AgentChatDock({ roleId, roleName, onReload, onCollapse }) {
  const { showToast } = useToast() || { showToast: () => {} };
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

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send(input);
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
        <MessageSquare size={15} />
        <span>Ask the agent</span>
        {roleName && <span className="ac-dock-role">{roleName}</span>}
        {onCollapse && (
          <button className="ac-dock-collapse" title="Collapse" onClick={onCollapse}>
            <PanelRightClose size={16} />
          </button>
        )}
      </div>

      <div className="ac-stream" ref={scrollRef}>
        {loading && items.length === 0 ? (
          <div className="ac-empty">Loading the conversation…</div>
        ) : items.length === 0 && !sending ? (
          <div className="ac-empty">
            <Avatar kind="agent" size={34} />
            <p style={{ marginTop: 10 }}>
              Tell the agent what to change on <b>{roleName || 'this role'}</b> — adjust a salary cap,
              move the score cut-off, or ask what a change would do.
            </p>
          </div>
        ) : (
          items.map((it) =>
            it.kind === 'needs_input' ? (
              <NeedsInputCard key={it.id} item={it} onAnswer={answer} onDismiss={dismiss} />
            ) : (
              <ChatBubble key={it.id} item={it}>
                {(it.actions || []).map((card, i) => (
                  <ImpactCard key={i} card={card} onApply={(t) => send(`Set the score cut-off to ${t}.`)} busy={sending} />
                ))}
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
            placeholder="Adjust this role…"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={sending}
          />
          <button className="ac-send" disabled={sending || !input.trim()} onClick={() => send(input)}>
            <ArrowUp size={15} />
          </button>
        </div>
        <div className="ac-hints">
          {HINTS.map((h) => (
            <button key={h} className="ac-hint-chip" disabled={sending} onClick={() => send(h)}>
              {h}
            </button>
          ))}
        </div>
      </div>
    </aside>
  );
}
