import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { PanelLeft } from 'lucide-react';
import './chat.css';
import EmptyState from './EmptyState';
import { ChatComposer, ChatMessage, ThinkingDots } from '../../shared/chat';
import Thread from './Thread';
import Sidebar from './Sidebar';
import ConfirmDialog from './ConfirmDialog';
import AgentConversation from './AgentConversation';
import useChatStream from './useChatStream';
import { conversationsApi } from './api';
import { agentChat } from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import { hydrateMessage, stitchToolResults } from './conversationHistory';
import { useDocumentVisibility } from '../../shared/motion';

const HISTORY_PAGE_SIZE = 60;

const ChatPage = ({ onNavigate = null, NavComponent = null, mode = 'ask' } = {}) => {
  const navigate = useNavigate();
  const params = useParams();
  const isAgents = mode === 'agents';
  const documentVisible = useDocumentVisibility();
  const conversationId = !isAgents && params.conversationId ? Number(params.conversationId) : null;
  const agentRoleId = isAgents && params.roleId ? Number(params.roleId) : null;
  const [searchParams, setSearchParams] = useSearchParams();
  const { showToast } = useToast() || { showToast: () => {} };

  const [conversations, setConversations] = useState([]);
  const [composer, setComposer] = useState('');
  const [pendingDeleteId, setPendingDeleteId] = useState(null);
  // Hydration state for the center pane while we fetch a conversation's
  // history: ``hydrating`` shows the loading dots, ``hydrateError`` shows a
  // small failure row. Both are cleared once a fetch resolves.
  const [hydrating, setHydrating] = useState(false);
  const [hydrateError, setHydrateError] = useState(false);
  const [historyPage, setHistoryPage] = useState({ hasMore: false, before: null });
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState(false);
  // Set when the sidebar list fetch fails so we keep the previous list on
  // screen (and show a quiet retry row) rather than collapsing to the
  // "no conversations yet" empty state for a user who has some.
  const [conversationsError, setConversationsError] = useState(false);
  // Bumped by the hydrate-error "Try again" button to re-run the hydration
  // effect while the route id stays the same.
  const [hydrateNonce, setHydrateNonce] = useState(0);

  // Agents tab: the per-role agent list (same source the Home dock polls).
  // Kept here so the sidebar can list them and the center can resolve the
  // active agent's name/state from the route's role id.
  const [agents, setAgents] = useState([]);
  // Mobile: the conversation/agent list is an off-canvas drawer.
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  // On error, keep the last agent list on screen — resetting to [] would
  // flash the empty two-pane shell (and yank the auto-select) on a transient
  // blip. The 30s interval retries on its own.
  const refreshAgents = useCallback(async () => {
    try {
      const { data } = await agentChat.listConversations();
      setAgents(Array.isArray(data?.agents) ? data.agents : []);
    } catch {
      /* keep previous agents */
    }
  }, []);

  useEffect(() => {
    if (!isAgents || !documentVisible) return undefined;
    void refreshAgents();
    const id = window.setInterval(() => {
      if (typeof document === 'undefined' || document.visibilityState !== 'hidden') {
        void refreshAgents();
      }
    }, 30_000);
    return () => window.clearInterval(id);
  }, [documentVisible, isAgents, refreshAgents]);

  // Land on the highest-attention agent when the Agents tab opens with no
  // role selected, so the surface is never an empty two-pane shell.
  useEffect(() => {
    if (!isAgents || agentRoleId || !agents.length) return;
    const ranked = [...agents].sort(
      (a, b) =>
        ((b.unread_messages || 0) + (b.open_questions || 0)) -
        ((a.unread_messages || 0) + (a.open_questions || 0)),
    );
    navigate(`/chat/agents/${ranked[0].role_id}`, { replace: true });
  }, [isAgents, agentRoleId, agents, navigate]);

  const activeAgent = useMemo(
    () => agents.find((a) => a.role_id === agentRoleId) || null,
    [agents, agentRoleId],
  );

  const agentAttention = useMemo(
    () => agents.reduce((sum, a) => sum + (a.unread_messages || 0) + (a.open_questions || 0), 0),
    [agents],
  );

  const onModeChange = useCallback(
    (nextMode) => {
      setMobileNavOpen(false);
      navigate(nextMode === 'agents' ? '/chat/agents' : '/chat');
    },
    [navigate],
  );

  const onSelectAgent = useCallback(
    (roleId) => {
      setMobileNavOpen(false);
      navigate(`/chat/agents/${roleId}`);
    },
    [navigate],
  );

  // The global search bar hands off to /chat with ?q=<query>. Seed the
  // composer once so the user lands on the chat with their phrase already
  // typed; we drop the param right after so a refresh won't re-seed.
  useEffect(() => {
    const seed = searchParams.get('q');
    if (!seed) return;
    setComposer(seed);
    const next = new URLSearchParams(searchParams);
    next.delete('q');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

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

  const {
    messages,
    isStreaming,
    error,
    send,
    stop,
    setHistory,
    prependHistory,
    reset,
    clearError,
  } =
    useChatStream({ conversationId, onConversationId });

  const activeConversationIdRef = useRef(conversationId);
  activeConversationIdRef.current = conversationId;
  const historyGenerationRef = useRef(0);

  const refreshConversations = useCallback(async () => {
    try {
      const list = await conversationsApi.list();
      setConversations(Array.isArray(list) ? list : []);
      setConversationsError(false);
    } catch {
      // Keep the previous list on screen and flag the error — clearing to []
      // would show "no conversations yet" to a user who has some.
      setConversationsError(true);
    }
  }, []);

  useEffect(() => {
    refreshConversations();
  }, [refreshConversations]);

  // When the route id changes, hydrate that conversation — UNLESS this
  // conversation was just created by the current send() flow, in which
  // case the chat hook's local state is the source of truth (it has the
  // streaming assistant placeholder; the API only has the user turn).
  //
  // We reset() first so a *different* conversation never renders the prior
  // thread's messages under its heading — either while the fetch is in
  // flight or, on a 404, permanently. That also clears any error banner
  // left over from the conversation we're leaving.
  useEffect(() => {
    const generation = historyGenerationRef.current + 1;
    historyGenerationRef.current = generation;
    let cancelled = false;
    const run = async () => {
      if (!conversationId) {
        reset();
        setHydrating(false);
        setHydrateError(false);
        setHistoryPage({ hasMore: false, before: null });
        setLoadingOlder(false);
        setOlderError(false);
        return;
      }
      // Just created by send(): its history lives in the hook already; don't
      // wipe the streaming placeholder or refetch.
      if (locallyCreated.current.has(conversationId)) {
        setHydrating(false);
        setHydrateError(false);
        setHistoryPage({ hasMore: false, before: null });
        setLoadingOlder(false);
        setOlderError(false);
        return;
      }
      reset();
      setHydrateError(false);
      setHistoryPage({ hasMore: false, before: null });
      setLoadingOlder(false);
      setOlderError(false);
      setHydrating(true);
      try {
        const data = await conversationsApi.get(conversationId, { limit: HISTORY_PAGE_SIZE });
        if (cancelled) return;
        const hydrated = stitchToolResults(
          (data.messages || []).map(hydrateMessage),
        );
        setHistory(hydrated);
        setHistoryPage({
          hasMore: Boolean(data.has_more && data.next_before != null),
          before: data.next_before ?? null,
        });
      } catch {
        if (!cancelled) setHydrateError(true);
      } finally {
        if (!cancelled) setHydrating(false);
      }
    };
    run();
    return () => {
      cancelled = true;
      if (historyGenerationRef.current === generation) {
        historyGenerationRef.current += 1;
      }
    };
  }, [conversationId, reset, setHistory, hydrateNonce]);

  const loadOlder = useCallback(async () => {
    const id = conversationId;
    const before = historyPage.before;
    if (id == null || before == null || loadingOlder) return;
    const generation = historyGenerationRef.current;
    const requestIsCurrent = () =>
      activeConversationIdRef.current === id &&
      historyGenerationRef.current === generation;
    setLoadingOlder(true);
    setOlderError(false);
    try {
      const data = await conversationsApi.get(id, {
        before,
        limit: HISTORY_PAGE_SIZE,
      });
      // Route changes reset the thread. The generation also protects a quick
      // leave-and-return to the same id from accepting the first visit's late
      // response.
      if (!requestIsCurrent()) return;
      prependHistory(
        (data.messages || []).map(hydrateMessage),
        stitchToolResults,
      );
      setHistoryPage({
        hasMore: Boolean(data.has_more && data.next_before != null),
        before: data.next_before ?? null,
      });
    } catch {
      if (requestIsCurrent()) setOlderError(true);
    } finally {
      if (requestIsCurrent()) setLoadingOlder(false);
    }
  }, [conversationId, historyPage.before, loadingOlder, prependHistory]);

  // After a streaming turn ends, refresh the sidebar so the new
  // conversation (or updated_at on the existing one) shows up, and drop the
  // conversation from ``locallyCreated`` — the API now has the full turn, so
  // re-opening it later must hydrate normally instead of being skipped.
  const previousStreamingRef = useRef(isStreaming);
  useEffect(() => {
    const justFinished = previousStreamingRef.current && !isStreaming;
    previousStreamingRef.current = isStreaming;
    if (!justFinished || messages.length === 0) return;
    if (conversationId != null) locallyCreated.current.delete(conversationId);
    void refreshConversations();
  }, [conversationId, isStreaming, messages.length, refreshConversations]);

  const onNew = useCallback(() => {
    setMobileNavOpen(false);
    navigate('/chat');
    setComposer('');
  }, [navigate]);

  const onSelect = useCallback(
    (id) => {
      setMobileNavOpen(false);
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
    try {
      await conversationsApi.remove(id);
    } catch {
      // A failed remove used to close the dialog silently and leave the row.
      // Tell the user and keep the row where it is.
      showToast?.('Couldn’t delete that conversation. Try again.', 'error');
      return;
    }
    if (id === conversationId) navigate('/chat');
    refreshConversations();
  }, [pendingDeleteId, conversationId, navigate, refreshConversations, showToast]);

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

  // "Try again" on the error card: re-send the last user message. The failed
  // turn left the user's text committed to the thread and cleared from the
  // composer, so there's nothing for them to press Enter on otherwise.
  const retryLastTurn = useCallback(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (m.role !== 'user') continue;
      const text = m.parts.find((p) => p.type === 'text')?.text;
      if (text) {
        clearError();
        send(text);
      }
      return;
    }
  }, [messages, send, clearError]);

  const heading = useMemo(() => {
    if (!conversationId) return 'New conversation';
    const found = conversations.find((c) => c.id === conversationId);
    // Neutral fallback — never leak the internal numeric id into the UI.
    return found?.title || 'Conversation';
  }, [conversationId, conversations]);

  return (
    <>
      {NavComponent ? <NavComponent currentPage="chat" onNavigate={onNavigate} /> : null}
      <div className={`cp-root ${mobileNavOpen ? 'cp-nav-open' : ''}`}>
      <Sidebar
        mode={isAgents ? 'agents' : 'ask'}
        onModeChange={onModeChange}
        conversations={conversations}
        activeId={conversationId}
        onNew={onNew}
        onSelect={onSelect}
        onDelete={onDelete}
        conversationsError={conversationsError}
        agents={agents}
        activeRoleId={agentRoleId}
        onSelectAgent={onSelectAgent}
        agentAttention={agentAttention}
      />
      {/* Mobile: tapping the scrim closes the list drawer. */}
      <button
        type="button"
        className="cp-nav-scrim"
        aria-label="Close list"
        tabIndex={mobileNavOpen ? 0 : -1}
        onClick={() => setMobileNavOpen(false)}
      />
      {isAgents ? (
        <AgentConversation
          key={agentRoleId || 'none'}
          roleId={agentRoleId}
          roleName={activeAgent?.role_name}
          agentEnabled={activeAgent ? activeAgent.agent_enabled : true}
          onAfterSend={refreshAgents}
          onOpenList={() => setMobileNavOpen(true)}
        />
      ) : (
      <div className="cp-center">
        <header className="cp-head">
          <button
            type="button"
            className="cp-mobile-menu"
            onClick={() => setMobileNavOpen(true)}
            aria-label="Show conversations"
          >
            <PanelLeft size={18} />
          </button>
          <div className="cp-head-titles">
            <div className="cp-head-ttl">{heading}</div>
            <div className="cp-head-sub">Taali</div>
          </div>
          <div className="cp-head-grow" />
          <span className="cp-head-pill">
            <span className="cp-pill-glyph" aria-hidden="true" />
            Connected to your pipeline
          </span>
        </header>
        <div className="cp-scroll">
          {hydrating && messages.length === 0 ? (
            <div className="cp-thread">
              <ChatMessage role="assistant">
                <ThinkingDots label="Loading the conversation…" />
              </ChatMessage>
            </div>
          ) : hydrateError && messages.length === 0 ? (
            <div className="cp-thread">
              <div className="cp-refresh-row">
                Couldn’t load this conversation.
                <button
                  type="button"
                  className="taali-text-btn cp-refresh-retry"
                  onClick={() => setHydrateNonce((n) => n + 1)}
                >
                  Try again
                </button>
              </div>
            </div>
          ) : messages.length === 0 ? (
            <EmptyState onPick={(t) => submit(t)} />
          ) : (
            <Thread
              messages={messages}
              isStreaming={isStreaming}
              error={error}
              onRetry={retryLastTurn}
              hasOlder={historyPage.hasMore}
              loadingOlder={loadingOlder}
              olderError={olderError}
              onLoadOlder={loadOlder}
            />
          )}
        </div>
        <div className="cp-composer-wrap">
          <ChatComposer
            value={composer}
            onChange={setComposer}
            onSubmit={submit}
            placeholder="Ask anything about your candidates…"
            busy={isStreaming}
            streaming={isStreaming}
            onStop={stop}
          />
          {/* search-preview composer foot tail — Search-specific (grounded
              evidence is the chat's promise), so it lives here rather than in
              the shared composer used by other surfaces. */}
          <div className="cp-composer-note">
            Every claim links back to the candidate’s CV.
          </div>
        </div>
      </div>
      )}
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
