// /home — agent-first landing. See docs/HOME_HUB_DESIGN.md for the full
// design. This file is the orchestrator: fetches data, wires URL-backed
// filters, composes the section components.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { MessageSquare } from 'lucide-react';

import { agent as agentApi, agentChat } from '../../shared/api';
import { AgentHeader } from '../../shared/layout/AgentHeader';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

import './home.css';
import { formatCount, budgetTile, decisionPendingFromCounts } from '../../shared/metrics';
import { HomeNow } from './HomeNow';
import { HomeAnalyticsSummary } from './HomeAnalyticsSummary';
import { AgentSidebar } from './agentchat/AgentSidebar';
import { AgentChatDock } from './agentchat/AgentChatDock';
import './agentchat/agentchat.css';

const ORG_STATUS_POLL_MS = 30_000;
// Keep the decision cards live. The org-status poll above refreshes the badges
// but NOT the list, so a decision that resolves in the background (processing →
// approved/sent, or a brand-new agent decision) used to linger as stale until
// the recruiter manually acted or switched filters. A short, silent,
// visibility-gated refetch keeps the queue honest without a spinner flash.
const DECISIONS_POLL_MS = 15_000;
// Active-agents poll — also drives the "an agent replied" notification for
// threads you're not currently viewing, so keep it reasonably brisk.
const AGENTS_POLL_MS = 15_000;

// Map a HomeNow filter shape -> the params the existing /agent-decisions
// endpoint expects. Status='pending' is special: the backend hides
// snoozed rows automatically.
const filtersToParams = (filters) => {
  const params = { limit: 100 };
  if (filters.role_id) params.role_id = filters.role_id;
  if (filters.type) params.type = filters.type;
  if (filters.q) params.q = filters.q;
  if (filters.status === 'all') {
    params.status = 'all';
  } else if (filters.status === 'stale') {
    // 'stale' ("Needs re-eval") is a client-side lens over the pending queue,
    // not a backend status — fetch pending and let HomeNow filter to the
    // stale subset.
    params.status = 'pending';
  } else {
    params.status = filters.status || 'pending';
  }
  return params;
};

const greetingForHour = (date) => {
  const h = date.getHours();
  if (h < 5) return 'Good evening';
  if (h < 12) return 'Good morning';
  if (h < 18) return 'Good afternoon';
  return 'Good evening';
};

const greetingFor = (user) => {
  const name = String(user?.full_name || user?.name || '').trim().split(/\s+/)[0]
    || (user?.email ? String(user.email).split('@')[0] : '')
    || 'there';
  return `${greetingForHour(new Date())}, ${name}`;
};


export const HomePage = ({ onNavigate, NavComponent }) => {
  const { user } = useAuth() || {};
  const { showToast } = useToast() || { showToast: () => {} };
  const [searchParams, setSearchParams] = useSearchParams();

  // Filters live in the URL so refresh / share preserves them.
  const filters = useMemo(() => ({
    role_id: searchParams.get('role') || null,
    type: searchParams.get('type') || null,
    status: searchParams.get('status') || 'pending',
    q: searchParams.get('q') || null,
    // 'invited' switches the queue to the Assessment-pending tracker. Persisted
    // like the other filters so the pill round-trips through the URL.
    view: searchParams.get('view') || null,
  }), [searchParams]);

  const setFilters = useCallback((updater) => {
    setSearchParams((prev) => {
      const current = {
        role: prev.get('role') || null,
        type: prev.get('type') || null,
        status: prev.get('status') || 'pending',
        q: prev.get('q') || null,
        view: prev.get('view') || null,
        pending: prev.get('pending') || null,
      };
      const next = typeof updater === 'function'
        ? updater({
          role_id: current.role,
          type: current.type,
          status: current.status,
          q: current.q,
          view: current.view,
        })
        : updater;
      const out = new URLSearchParams();
      if (next.role_id) out.set('role', String(next.role_id));
      if (next.type) out.set('type', String(next.type));
      if (next.status && next.status !== 'pending') out.set('status', String(next.status));
      if (next.q) out.set('q', String(next.q));
      if (next.view) out.set('view', String(next.view));
      if (current.pending) out.set('pending', current.pending);
      return out;
    }, { replace: true });
  }, [setSearchParams]);

  const selectedId = useMemo(() => {
    const raw = searchParams.get('pending');
    if (!raw) return null;
    const n = Number(String(raw).replace(/^D-/i, ''));
    return Number.isFinite(n) ? n : null;
  }, [searchParams]);

  const setSelectedId = useCallback((id) => {
    setSearchParams((prev) => {
      const out = new URLSearchParams(prev);
      if (id == null) out.delete('pending');
      else out.set('pending', String(id));
      return out;
    }, { replace: true });
  }, [setSearchParams]);

  const [orgStatus, setOrgStatus] = useState(null);
  const [decisions, setDecisions] = useState([]);
  const [pendingOrdered, setPendingOrdered] = useState([]);
  // True "Needs re-eval" total for the current scope, computed server-side over
  // the whole queue (the per-row is_stale on the list only covers the capped
  // page, so a deep backlog under-counts client-side). Refreshed on real loads,
  // not the silent poll.
  const [staleCount, setStaleCount] = useState(0);
  const [rolesBreakdown, setRolesBreakdown] = useState([]);
  const [loadingDecisions, setLoadingDecisions] = useState(true);
  const [loadingRoles, setLoadingRoles] = useState(true);

  // Agent chat (Option C): active agents for the left rail + the dock's target
  // role. Progressive enhancement — if the /agent-chat endpoints aren't
  // reachable, `agents` stays empty and the home renders exactly as before.
  const [agents, setAgents] = useState([]);
  const [activeRoleId, setActiveRoleId] = useState(null);
  // Bulk messaging: select several roles, send one message that fans out to
  // each role's own thread (separate audit). bulkSelected holds role_ids.
  const [bulkMode, setBulkMode] = useState(false);
  // Chat-dock visibility, DECOUPLED from role selection: you can hide the chat
  // while keeping the agent/role selected, and reopen it from the edge handle.
  // Selecting an agent always reveals its chat (reset below).
  const [chatHidden, setChatHidden] = useState(false);
  const [bulkSelected, setBulkSelected] = useState(() => new Set());

  // Track in-flight reloads so rapid clicks don't pile up requests.
  const reloadCounter = useRef(0);
  // Once the recruiter has picked (or cleared) an agent in the rail, the 30s
  // poll stops auto-focusing the top agent — so a deselect sticks.
  const userTouchedSelectionRef = useRef(false);
  // Stale-while-revalidate cache of the last rows seen per filter, so flipping
  // between filters you've already opened paints instantly instead of blanking
  // to a spinner while the (authenticated) refetch round-trips. Search queries
  // aren't cached (ad-hoc, unbounded keys); the map is capped to recent views.
  const decisionsCacheRef = useRef(new Map());
  // Reply notifications: remember each agent's unread count between polls so we
  // can toast when a NEW reply lands in a thread you're not currently viewing.
  // `null` until the first poll primes it (so pre-existing unread never toasts).
  const prevUnreadRef = useRef(null);
  const activeRoleIdRef = useRef(null);
  const showToastRef = useRef(showToast);
  useEffect(() => { showToastRef.current = showToast; }, [showToast]);
  // Keep the active role in a ref so the polling loader can skip notifying for
  // the thread you're already looking at, without re-creating the interval.
  useEffect(() => { activeRoleIdRef.current = activeRoleId; }, [activeRoleId]);

  // ``silent`` (background poll / focus refresh) skips the loading spinner so
  // the live list updates in place without a flash — the cards just reconcile
  // when fresh data lands.
  const loadDecisions = useCallback(async ({ silent = false } = {}) => {
    const cacheKey = filters.q ? null : JSON.stringify({
      role: filters.role_id || null,
      type: filters.type || null,
      status: filters.status || 'pending',
    });
    const cached = cacheKey ? decisionsCacheRef.current.get(cacheKey) : null;
    // Paint cached rows for this filter immediately (stale-while-revalidate);
    // only show the spinner when there's nothing cached to show yet.
    if (!silent && cached) {
      setPendingOrdered(cached.pending);
      setDecisions(cached.feed);
    }
    if (!silent && !cached) setLoadingDecisions(true);
    const ticket = ++reloadCounter.current;
    try {
      // Pending sidebar always shows status=pending but honors the same
      // role/type/search filters as the feed so the two columns describe
      // the same slice. When the user is on the default ('pending') view,
      // both fetches collapse into one — skip the duplicate.
      const pendingParams = {
        status: 'pending',
        role_id: filters.role_id || undefined,
        type: filters.type || undefined,
        q: filters.q || undefined,
        limit: 100,
      };
      const feedParams = filtersToParams(filters);
      const sameParams = feedParams.status === 'pending';
      const [pendingRes, feedRes] = sameParams
        ? await (async () => {
          const res = await agentApi.listDecisions(pendingParams);
          return [res, res];
        })()
        : await Promise.all([
          agentApi.listDecisions(pendingParams),
          agentApi.listDecisions(feedParams),
        ]);
      if (reloadCounter.current !== ticket) return;
      const pendingRows = Array.isArray(pendingRes?.data) ? pendingRes.data : [];
      const feedRows = Array.isArray(feedRes?.data) ? feedRes.data : [];
      // Pending sidebar: highest score first so the strongest candidates
      // surface at the top. Unscored rows sink to the bottom; ties fall
      // back to oldest-first.
      const scoreOf = (d) => (Number.isFinite(Number(d?.taali_score)) ? Number(d.taali_score) : -Infinity);
      const pending = [...pendingRows].sort((a, b) => {
        const byScore = scoreOf(b) - scoreOf(a);
        if (byScore !== 0) return byScore;
        return new Date(a.created_at) - new Date(b.created_at);
      });
      setPendingOrdered(pending);
      setDecisions(feedRows);
      if (cacheKey) {
        const cache = decisionsCacheRef.current;
        cache.set(cacheKey, { pending, feed: feedRows });
        // Keep only the most recent handful of filter views in memory.
        if (cache.size > 10) cache.delete(cache.keys().next().value);
      }
    } catch (err) {
      // 401/403 here means the AuthContext is about to redirect to
      // /login — no need to flash a "Failed to load" toast in the
      // half-second before the navigation lands.
      const status = err?.response?.status;
      if (reloadCounter.current === ticket && status !== 401 && status !== 403) {
        showToast?.(err?.response?.data?.detail || 'Failed to load decisions', 'error');
      }
    } finally {
      if (reloadCounter.current === ticket) setLoadingDecisions(false);
    }
  }, [filters, showToast]);

  const loadRoles = useCallback(async () => {
    setLoadingRoles(true);
    try {
      const res = await agentApi.rolesBreakdown();
      setRolesBreakdown(Array.isArray(res?.data) ? res.data : []);
    } catch {
      setRolesBreakdown([]);
    } finally {
      setLoadingRoles(false);
    }
  }, []);

  // Org-status + roles-breakdown poll — keeps the KPI strip, tab badge,
  // and the "By role" pending column in lockstep. Polling both together
  // avoids the stale per-role counts users saw when actions resolved
  // decisions outside this page's review queue.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const [statusRes, rolesRes] = await Promise.all([
          agentApi.orgStatus(),
          agentApi.rolesBreakdown(),
        ]);
        if (cancelled) return;
        setOrgStatus(statusRes?.data || null);
        setRolesBreakdown(Array.isArray(rolesRes?.data) ? rolesRes.data : []);
      } catch { /* silent */ } finally {
        // The dedicated loadRoles() useEffect was retired in favour of this
        // poll; clear loadingRoles here so the "By role" section doesn't
        // stay stuck on "Loading…" forever.
        if (!cancelled) setLoadingRoles(false);
      }
    };
    void tick();
    const id = window.setInterval(tick, ORG_STATUS_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => { void loadDecisions(); }, [loadDecisions]);

  // Accurate "Needs re-eval" total for the pill — scoped to role + type, over
  // the whole queue (the per-row is_stale on the list only covers the capped
  // page). Its OWN effect, decoupled from the decisions reload so the slower
  // count can't be superseded by a poll's reload ticket; refreshes when the
  // scope changes.
  useEffect(() => {
    let cancelled = false;
    agentApi.needsReevalCount({
      role_id: filters.role_id || undefined,
      type: filters.type || undefined,
    }).then((res) => {
      if (!cancelled) setStaleCount(Number(res?.data?.count) || 0);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [filters.role_id, filters.type]);

  // Silent background refresh of the decision list so in-flight rows resolve
  // on their own. A ref holds the latest loader so the interval isn't torn
  // down and rebuilt every time the filters change. Gated on tab visibility
  // (no point polling a backgrounded tab) and also fired the moment the tab
  // regains focus, so tabbing back shows current data immediately.
  const loadDecisionsRef = useRef(loadDecisions);
  useEffect(() => { loadDecisionsRef.current = loadDecisions; }, [loadDecisions]);
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === 'visible') {
        void loadDecisionsRef.current({ silent: true });
      }
    };
    const id = window.setInterval(refresh, DECISIONS_POLL_MS);
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      window.clearInterval(id);
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, []);

  const reloadAll = useCallback(async () => {
    await Promise.all([loadDecisions(), loadRoles()]);
    try {
      const res = await agentApi.orgStatus();
      setOrgStatus(res?.data || null);
    } catch { /* silent */ }
  }, [loadDecisions, loadRoles]);

  // Poll the active-agents list for the left rail + notification badges.
  // Self-contained: a missing/erroring endpoint degrades to the plain home
  // (no rail/dock), so this can't break the central page.
  const loadAgents = useCallback(async () => {
    try {
      const { data } = await agentChat.listConversations();
      const list = Array.isArray(data?.agents) ? data.agents : [];
      setAgents(list);
      // Notify when a new agent reply lands in a thread you're NOT viewing (the
      // active thread shows replies inline via the dock's own poll). First poll
      // only primes the baseline, so existing unread never toasts.
      const prevUnread = prevUnreadRef.current;
      if (prevUnread) {
        list.forEach((a) => {
          const before = prevUnread.get(a.role_id) || 0;
          if ((a.unread_messages || 0) > before && a.role_id !== activeRoleIdRef.current) {
            showToastRef.current?.(`${a.role_name} replied — open the thread`, 'success');
          }
        });
      }
      prevUnreadRef.current = new Map(list.map((a) => [a.role_id, a.unread_messages || 0]));
      setActiveRoleId((cur) => {
        // Keep a still-valid selection across polls; otherwise default to
        // "All roles" (null). The hub loads with NO agent chat open — the
        // recruiter opens one by clicking an agent in the rail.
        if (cur && list.some((a) => a.role_id === cur)) return cur;
        return null;
      });
    } catch {
      setAgents([]);
    }
  }, []);

  useEffect(() => {
    void loadAgents();
    const id = window.setInterval(() => { void loadAgents(); }, AGENTS_POLL_MS);
    return () => window.clearInterval(id);
  }, [loadAgents]);

  // Selecting an agent focuses both the chat dock and the decision feed on that
  // role. Clicking the already-selected agent toggles it OFF — back to all
  // roles / no agent focused — so there's always a way out of a scoped view.
  const handleSelectAgent = useCallback((roleId) => {
    userTouchedSelectionRef.current = true;
    // roleId == null is the explicit "All roles" reset from the rail; clicking
    // the already-selected agent also toggles back to all roles. Either way we
    // clear the scope without yanking the dock open.
    const deselect = roleId == null || activeRoleId === roleId;
    setActiveRoleId(deselect ? null : roleId);
    setFilters((f) => ({ ...f, role_id: deselect ? null : roleId }));
    if (!deselect) setChatHidden(false); // selecting an agent always reveals its chat
  }, [activeRoleId, setFilters]);

  const activeAgent = useMemo(
    () => agents.find((a) => a.role_id === activeRoleId) || null,
    [agents, activeRoleId]
  );

  // --- Bulk messaging ------------------------------------------------------
  const toggleBulkMode = useCallback(() => {
    setBulkMode((on) => {
      if (on) setBulkSelected(new Set()); // leaving select-mode clears the picks
      return !on;
    });
  }, []);

  const toggleRoleSelected = useCallback((roleId) => {
    setBulkSelected((prev) => {
      const next = new Set(prev);
      next.has(roleId) ? next.delete(roleId) : next.add(roleId);
      return next;
    });
  }, []);

  const clearBulk = useCallback(() => {
    setBulkMode(false);
    setBulkSelected(new Set());
  }, []);

  const sendBulk = useCallback(
    async (message) => {
      const ids = Array.from(bulkSelected);
      const text = (message || '').trim();
      if (!ids.length || !text) return;
      try {
        const { data } = await agentChat.bulkMessage(ids, text);
        const n = data?.accepted ?? ids.length;
        showToast?.(
          `Sent to ${n} agent${n === 1 ? '' : 's'} — replies will appear in each role's thread.`,
          'success'
        );
        clearBulk();
        // Replies land async; nudge the rail to refresh its unread badges.
        window.setTimeout(() => { void loadAgents(); }, 2000);
      } catch (err) {
        showToast?.(err?.response?.data?.detail || 'Couldn’t send to the selected agents.', 'error');
      }
    },
    [bulkSelected, showToast, clearBulk, loadAgents]
  );

  // Selected roles with their names, for the dock's confirm strip.
  const bulkSelectedRoles = useMemo(
    () => agents.filter((a) => bulkSelected.has(a.role_id)).map((a) => ({ role_id: a.role_id, role_name: a.role_name })),
    [agents, bulkSelected]
  );
  // "Awaiting you in the chat" — unread agent messages + open questions across
  // agents. Deliberately excludes the bulk pending-decision queue (that's the
  // feed's "Pending N"), so the dock badge reads as chat notifications.
  const totalAttention = useMemo(
    () => agents.reduce((sum, a) => sum + (a.unread_messages || 0) + (a.open_questions || 0), 0),
    [agents]
  );
  // Enrich each sidebar agent with its monthly budget (spent / cap) from the
  // roles-breakdown poll, so the rail can show a small budget bar per agent.
  const agentsWithBudget = useMemo(() => {
    const byRole = new Map((rolesBreakdown || []).map((r) => [r.role_id, r]));
    return agents.map((a) => {
      const r = byRole.get(a.role_id);
      return {
        ...a,
        budget_spent_cents: r ? Number(r.budget_cents || 0) : null,
        budget_cap_cents: r ? Number(r.cap_cents || 0) : null,
      };
    });
  }, [agents, rolesBreakdown]);

  // KPIs come from org-status; while it's loading, fall back to derived
  // counts so the strip never shows blanks on first paint.
  const kpis = orgStatus || {
    pending: pendingOrdered.length,
    pending_decisions: pendingOrdered.length,
    pending_questions: 0,
    pending_by_type: {},
    today: decisions.filter((d) => {
      const dt = d.created_at ? new Date(d.created_at) : null;
      if (!dt) return false;
      const start = new Date();
      start.setHours(0, 0, 0, 0);
      return dt >= start;
    }).length,
    auto_applied_today: 0,
    org_budget_spent_cents: 0,
    org_budget_cap_cents: 0,
    override_rate_pct: 0,
    teach_rate_pct: 0,
    paused_role_count: 0,
    active_role_count: 0,
    oldest_pending_age_seconds: null,
  };

  // Org budget tile (spent / cap + bar + projection) — same helper the Jobs
  // and role strips use so the format is identical everywhere.
  const orgBudget = useMemo(
    () => budgetTile(kpis.org_budget_spent_cents, kpis.org_budget_cap_cents),
    [kpis.org_budget_cap_cents, kpis.org_budget_spent_cents],
  );

  // "Awaiting you" = the agent's pending recommendations (HITL) — the queue the
  // recruiter must approve, override or teach. Drives the hero kicker + the
  // "Awaiting you" card, and matches the nav badge. A scored candidate the agent
  // hasn't ruled on yet is NOT here — it's "decision pending" (below).
  const pendingDecisions = Number(kpis.pending_decisions ?? kpis.pending ?? 0);
  // Not-yet-decided — candidates at a decision stage the agent hasn't ruled on
  // yet (the funnel's grey "decision pending" chips, summed across roles).
  // Shown as context under the "Awaiting you" card; the agent's to-do, not yours.
  const orgNotYetDecided = useMemo(() => {
    const list = Array.isArray(rolesBreakdown) ? rolesBreakdown : [];
    return list.reduce(
      (acc, r) => acc + decisionPendingFromCounts(r?.stage_counts, r?.pending_decisions_by_type),
      0,
    );
  }, [rolesBreakdown]);

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="home" onNavigate={onNavigate} /> : null}
      {/* App-shell: the hero + the 3-col shell fill the viewport below the nav so
          the chat dock's composer is visible on load (columns scroll internally). */}
      <div className="home-app">
      {/* Full-width page header — consistent with every other page (spans the
          whole width, above the agent rail + chat dock). */}
      <AgentHeader
        breadcrumbs={[{ label: 'Home' }]}
        kicker={`HUB · ${formatCount(pendingDecisions)} AWAITING YOU · ${formatCount(kpis.active_role_count)} ACTIVE ROLE${kpis.active_role_count === 1 ? '' : 'S'}`}
        title={greetingFor(user)}
        subtitle="Approve, override, or teach the agent's calls — this is where you keep the loop honest."
      />
      {/* The shell renders immediately (not gated on the async agents fetch),
          so the page lays out once — no flash of the pre-rail layout. */}
      <div className={`ac-shell ${(bulkMode || (activeRoleId != null && !chatHidden)) ? '' : 'ac-dock-collapsed'}`}>
        <AgentSidebar
          agents={agentsWithBudget}
          activeRoleId={activeRoleId}
          onSelect={handleSelectAgent}
          bulkMode={bulkMode}
          bulkSelected={bulkSelected}
          onToggleBulkMode={toggleBulkMode}
          onToggleSelected={toggleRoleSelected}
        />
        <div className="ac-main">

      <div className="home-body">
        {/* No top KPI strip — the hub leads with the funnel + the review queue
            (the preview dropped the KPI row; the hero kicker carries "awaiting
            you" and the full metrics live on the Analytics page). */}
        <HomeNow
          decisions={decisions}
          pendingOrdered={pendingOrdered}
          staleCount={staleCount}
          selectedId={selectedId}
          setSelectedId={setSelectedId}
          loading={loadingDecisions}
          filters={filters}
          setFilters={setFilters}
          rolesBreakdown={rolesBreakdown}
          reload={reloadAll}
          onNavigate={onNavigate}
          questionsInDock={true}
        />

        {/* High-level pulse + a link to the full Analytics page. The detailed
            console (outcomes, fleet, teaching, A/B, decision log) moved to
            /analytics — keeps the hub's review loop focused, and keeps the
            expensive reporting queries off every home load. */}
        <HomeAnalyticsSummary kpis={kpis} orgBudget={orgBudget} onNavigate={onNavigate} />
      </div>
        </div>
        {/* The chat dock opens when an agent is selected (or in bulk mode). Its
            collapse control HIDES the chat but keeps the agent selected — the
            slim edge handle below reopens it. Re-clicking the agent (or "All
            roles") clears the scope and closes everything. */}
        {(bulkMode || (activeRoleId != null && !chatHidden)) ? (
          <AgentChatDock
            roleId={activeRoleId}
            roleName={activeAgent?.role_name}
            agentEnabled={activeAgent ? activeAgent.agent_enabled : true}
            onReload={reloadAll}
            onCollapse={() => { if (bulkMode) { clearBulk(); } else { setChatHidden(true); } }}
            bulkSelectedRoles={bulkSelectedRoles}
            onSendBulk={sendBulk}
            onClearBulk={clearBulk}
          />
        ) : null}
        {/* Chat hidden but the agent stays selected — a slim edge handle brings
            it back without losing the role scope. */}
        {(activeRoleId != null && chatHidden && !bulkMode) ? (
          <button
            type="button"
            className="ac-reopen"
            onClick={() => setChatHidden(false)}
            title="Show agent chat"
          >
            <MessageSquare size={16} /> Agent chat
            {totalAttention > 0 && <span className="ac-badge-count">{totalAttention}</span>}
          </button>
        ) : null}
      </div>
      </div>
      {/* Mobile only: the side rail + dock don't fit a phone, so we route to
          the Chat page's Agents tab (the same threads, kept in sync) instead
          of cramming a floating dock over the feed. Hidden on desktop via CSS. */}
      <button
        type="button"
        className="ac-mobile-cta"
        onClick={() => onNavigate?.('chat-agents', { roleId: activeRoleId || undefined })}
      >
        <MessageSquare size={16} /> Chat with your agents
        {totalAttention > 0 && <span className="ac-badge-count">{totalAttention}</span>}
      </button>
    </div>
  );
};

export default HomePage;
