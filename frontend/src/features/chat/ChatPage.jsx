import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import './chat.css';
import EmptyState from './EmptyState';
import Composer from './Composer';
import Thread from './Thread';
import Sidebar from './Sidebar';
import ConfirmDialog from './ConfirmDialog';
import useChatStream from './useChatStream';
import { conversationsApi } from './api';

// Backend persists messages with Anthropic-shaped content blocks. The
// chat hook works with a slightly flatter shape (parts: text/tool_call).
// This converter lets us hydrate a saved conversation and pick up where
// the user left off.
const hydrateMessage = (m) => {
  const parts = [];
  const blocks = Array.isArray(m.content) ? m.content : [];
  // Synthetic "user" rows that only contain tool_result blocks aren't
  // meaningful to show — they're echoes of the previous tool dispatch.
  // We attach those back to the matching tool_call instead by matching
  // tool_use_id, in a second pass below.
  for (const b of blocks) {
    if (b.type === 'text' && b.text) parts.push({ type: 'text', text: b.text });
    if (b.type === 'tool_use') {
      parts.push({
        type: 'tool_call',
        toolCallId: b.id,
        toolName: b.name,
        args: b.input || {},
        status: 'complete',
      });
    }
  }
  return {
    id: `m_${m.id}`,
    role: m.role === 'assistant' ? 'assistant' : 'user',
    parts,
    _isToolResultEcho: blocks.length > 0 && blocks.every((b) => b.type === 'tool_result'),
    _toolResults: blocks.filter((b) => b.type === 'tool_result'),
  };
};

const stitchToolResults = (rows) => {
  // Walk through rows; whenever we see a "user" row that's just
  // tool_result echoes, dissolve it into the matching tool_call parts
  // of the previous assistant row.
  const out = [];
  for (const m of rows) {
    if (m._isToolResultEcho && out.length) {
      const prev = out[out.length - 1];
      const merged = {
        ...prev,
        parts: prev.parts.map((p) => {
          if (p.type !== 'tool_call') return p;
          const r = m._toolResults.find((tr) => tr.tool_use_id === p.toolCallId);
          if (!r) return p;
          let parsed = r.content;
          try {
            parsed = JSON.parse(r.content);
          } catch {
            /* keep as string */
          }
          return { ...p, result: parsed, status: r.is_error ? 'error' : 'complete' };
        }),
      };
      out[out.length - 1] = merged;
      continue;
    }
    out.push(m);
  }
  return out
    .filter((m) => !(m.role === 'user' && !m.parts.length))
    .map(({ _isToolResultEcho, _toolResults, ...rest }) => rest);
};

const ChatPage = ({ onNavigate = null, NavComponent = null } = {}) => {
  const navigate = useNavigate();
  const { conversationId: routeId } = useParams();
  const conversationId = routeId ? Number(routeId) : null;

  const [conversations, setConversations] = useState([]);
  const [composer, setComposer] = useState('');
  const [pendingDeleteId, setPendingDeleteId] = useState(null);

  // Conversations created by the current send() flow. Hydrating their
  // history from the API would race with the in-flight stream — the
  // assistant placeholder + error state already live in useChatStream's
  // local state and the API doesn't have the assistant turn until the
  // stream finishes. We track these here so the hydration effect can
  // skip them.
  const locallyCreated = useRef(new Set());

  const onConversationId = useCallback(
    (id) => {
      locallyCreated.current.add(id);
      if (!conversationId) navigate(`/chat/${id}`, { replace: true });
    },
    [conversationId, navigate],
  );

  const { messages, isStreaming, error, send, stop, setHistory, reset } =
    useChatStream({ conversationId, onConversationId });

  const refreshConversations = useCallback(async () => {
    try {
      const list = await conversationsApi.list();
      setConversations(Array.isArray(list) ? list : []);
    } catch {
      setConversations([]);
    }
  }, []);

  useEffect(() => {
    refreshConversations();
  }, [refreshConversations]);

  // When the route id changes, hydrate that conversation — UNLESS this
  // conversation was just created by the current send() flow, in which
  // case the chat hook's local state is the source of truth (it has the
  // streaming assistant placeholder; the API only has the user turn).
  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      if (!conversationId) {
        reset();
        return;
      }
      if (locallyCreated.current.has(conversationId)) return;
      try {
        const data = await conversationsApi.get(conversationId);
        if (cancelled) return;
        const hydrated = stitchToolResults(
          (data.messages || []).map(hydrateMessage),
        );
        setHistory(hydrated);
      } catch {
        /* 404 → leave empty */
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [conversationId, reset, setHistory]);

  // After a streaming turn ends, refresh the sidebar so the new
  // conversation (or updated_at on the existing one) shows up.
  useEffect(() => {
    if (!isStreaming && messages.length) {
      refreshConversations();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isStreaming]);

  const onNew = useCallback(() => {
    navigate('/chat');
    setComposer('');
  }, [navigate]);

  const onSelect = useCallback(
    (id) => {
      navigate(`/chat/${id}`);
    },
    [navigate],
  );

  // Two-step delete: ``onDelete`` opens an in-app confirm dialog,
  // ``confirmDelete`` performs the actual remove. Replaces the prior
  // ``window.confirm`` so the dialog matches the design tokens and
  // doesn't render the browser's native chrome.
  const onDelete = useCallback((id) => {
    setPendingDeleteId(id);
  }, []);

  const confirmDelete = useCallback(async () => {
    const id = pendingDeleteId;
    if (id == null) return;
    setPendingDeleteId(null);
    await conversationsApi.remove(id);
    if (id === conversationId) navigate('/chat');
    refreshConversations();
  }, [pendingDeleteId, conversationId, navigate, refreshConversations]);

  const cancelDelete = useCallback(() => {
    setPendingDeleteId(null);
  }, []);

  const pendingDeleteTitle = useMemo(() => {
    const found = conversations.find((c) => c.id === pendingDeleteId);
    return found?.title || null;
  }, [conversations, pendingDeleteId]);

  const submit = useCallback(
    (text) => {
      send(text);
      setComposer('');
    },
    [send],
  );

  const heading = useMemo(() => {
    if (!conversationId) return 'New conversation';
    const found = conversations.find((c) => c.id === conversationId);
    return found?.title || `Conversation ${conversationId}`;
  }, [conversationId, conversations]);

  return (
    <>
      {NavComponent ? <NavComponent currentPage="chat" onNavigate={onNavigate} /> : null}
      <div className="cp-root">
      <Sidebar
        conversations={conversations}
        activeId={conversationId}
        onNew={onNew}
        onSelect={onSelect}
        onDelete={onDelete}
      />
      <div className="cp-center">
        <header className="cp-head">
          <div className="cp-head-ttl">
            {heading}
            <span className="sub">Taali</span>
          </div>
          <div className="cp-head-grow" />
          <span className="cp-head-pill">
            <span className="cp-pill-glyph">▮</span>
            MCP · 9 tools
          </span>
        </header>
        <div className="cp-scroll">
          {messages.length === 0 ? (
            <EmptyState onPick={(t) => submit(t)} />
          ) : (
            <Thread
              messages={messages}
              isStreaming={isStreaming}
              error={error}
            />
          )}
        </div>
        <div className="cp-composer-wrap">
          <Composer
            value={composer}
            onChange={setComposer}
            onSubmit={submit}
            isStreaming={isStreaming}
            onStop={stop}
          />
        </div>
      </div>
      <ConfirmDialog
        open={pendingDeleteId != null}
        title="Delete conversation?"
        detail={
          pendingDeleteTitle
            ? `“${pendingDeleteTitle}” will be removed from your sidebar. This can't be undone.`
            : "This conversation will be removed from your sidebar. This can't be undone."
        }
        confirmLabel="Delete"
        destructive
        onConfirm={confirmDelete}
        onCancel={cancelDelete}
      />
    </div>
    </>
  );
};

export default ChatPage;
export { ChatPage };
