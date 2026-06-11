// Agents tab of the Search page — converse with one role's autonomous agent.
// This is the same conversation the Home dock drives: both read and write the
// shared /agent-chat/conversations/:roleId/* thread, so a message sent here
// shows up there (and vice-versa) the next time either surface loads. We keep
// this panel self-contained (its own timeline fetch + send) rather than lifting
// the dock's internals so the Home page stays untouched.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { PanelLeft, Sparkles } from 'lucide-react';

import { agentChat } from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import { ChatComposer, ChatEmptyState, ChatMessage, ThinkingDots } from '../../shared/chat';
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
    setTimeline([]);
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
      setTimeline((t) => [
        ...t,
        { kind: 'message', id: `local-${t.length}`, author: 'recruiter', text: msg, created_at: new Date().toISOString() },
      ]);
      try {
        const { data } = await agentChat.sendMessage(roleId, msg);
        setTimeline(data.timeline || []);
        onAfterSend?.();
      } catch (err) {
        showToast?.(err?.response?.data?.detail || 'The agent couldn’t complete that. Try again.', 'error');
        load();
      } finally {
        setSending(false);
      }
    },
    [roleId, sending, onAfterSend, showToast, load]
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

  useEffect(() => {
    if (!rescreenPending) return undefined;
    const poll = window.setInterval(() => { void load({ silent: true }); }, 5000);
    const stop = window.setTimeout(() => window.clearInterval(poll), 6 * 60 * 1000);
    return () => { window.clearInterval(poll); window.clearTimeout(stop); };
  }, [rescreenPending, load]);

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
          <div className="cp-head-ttl">Agents<span className="sub">Agent</span></div>
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
        <div className="cp-head-ttl">
          {roleName || 'Agent'}
          <span className="sub">Agent</span>
        </div>
        <div className="cp-head-grow" />
        <span className={`cp-head-pill ${agentEnabled ? 'cp-head-pill-on' : ''}`}>
          <span className="cp-pill-glyph"><Sparkles size={11} /></span>
          {agentEnabled ? 'Agent on' : 'Agent off'}
        </span>
      </header>
      <div className="cp-scroll" ref={scrollRef}>
        {loading && items.length === 0 ? (
          <div className="cp-thread">
            <ChatMessage role="assistant"><ThinkingDots label="Loading the conversation…" /></ChatMessage>
          </div>
        ) : items.length === 0 && !sending ? (
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
                  text={it.text}
                >
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
            {sending && (
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
        )}
      </div>
      <div className="cp-composer-wrap">
        <ChatComposer
          value={input}
          onChange={setInput}
          onSubmit={send}
          placeholder="Ask about this role’s pool, or tell the agent to change something…"
          busy={sending}
        />
      </div>
    </div>
  );
};

export default AgentConversation;
export { AgentConversation };
