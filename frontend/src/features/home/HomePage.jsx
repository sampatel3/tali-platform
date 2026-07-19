// /home — agent-first landing. See docs/HOME_HUB_DESIGN.md for the full
// design. This file is the orchestrator: fetches data, wires URL-backed
// filters, composes the section components.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { agent as agentApi, agentChat } from '../../shared/api';
import { readCache, writeCache } from '../../shared/api/resourceCache';
import { AgentHeader, buildAgentPropFromStatus } from '../../shared/layout/AgentHeader';
import { useAgentStatusOrg } from '../../shared/layout/AgentBar';
import { Reveal } from '../../shared/motion';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

import './home.css';
import { formatCount, budgetTile, decisionPendingFromCounts } from '../../shared/metrics';
import { HomeNow } from './HomeNow';
import { HomeAnalyticsSummary } from './HomeAnalyticsSummary';
import { HomeAgentWorkspace } from './HomeAgentWorkspace';
import { useWorkspaceAgentControl } from './useWorkspaceAgentControl';
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

// Stale-while-revalidate cache keys (module-level, survive navigation). The
// decisions key is scoped to the same role/type/status the loader fetches; ad-hoc
// search queries (filters.q) aren't cached, so they return null.
const decisionsCacheKey = (filters) => (filters.q ? null : `home:decisions:${JSON.stringify({
  role: filters.role_id || null,
  type: filters.type || null,
  status: filters.status || 'pending',
})}`);
// "Needs re-eval" count is computed per role/type scope, so its cache key must
// carry that scope or a scoped count would leak across filters.
const staleCacheKey = (filters) => `home:stale:${filters.role_id || 'all'}:${filters.type || 'all'}`;

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
  const canControlWorkspaceAgent = String(user?.role || '') === 'owner';
  const { showToast } = useToast() || { showToast: () => {} };
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    payload: sharedOrgStatus,
    refetch: refetchOrgStatus,
  } = useAgentStatusOrg(Boolean(user));

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

  // Seed org-wide numbers from the SWR cache so returning to /home (React Router
  // re-mounts this page on every tab switch) paints the last-known values
  // instantly instead of flashing 0/empty while the polls round-trip. The polls
  // below refresh the cache; React setState no-ops when the value is unchanged,
  // so the displayed number only moves on a real change.
  const [orgStatus, setOrgStatus] = useState(() => readCache('home:org-status')?.data ?? null);
  const [decisions, setDecisions] = useState(() => readCache(decisionsCacheKey(filters))?.data?.feed ?? []);
  const [pendingOrdered, setPendingOrdered] = useState(() => readCache(decisionsCacheKey(filters))?.data?.pending ?? []);
  // True "Needs re-eval" total for the current scope, computed server-side over
  // the whole queue (the per-row is_stale on the list only covers the capped
  // page, so a deep backlog under-counts client-side). Refreshed on real loads,
  // not the silent poll.
  const [staleCount, setStaleCount] = useState(() => readCache(staleCacheKey(filters))?.data ?? 0);
  const [rolesBreakdown, setRolesBreakdown] = useState(() => readCache('home:roles-breakdown')?.data ?? []);
  // Skip the loading state when the cache already has data to paint, so the
  // queue and funnel don't flash their spinners on a warm re-mount.
  const [loadingDecisions, setLoadingDecisions] = useState(() => !readCache(decisionsCacheKey(filters)));
  const [loadingRoles, setLoadingRoles] = useState(() => !readCache('home:roles-breakdown'));

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
    const cacheKey = decisionsCacheKey(filters);
    const cached = cacheKey ? readCache(cacheKey) : null;
    // Paint cached rows for this filter immediately (stale-while-revalidate);
    // only show the spinner when there's nothing cached to show yet.
    if (!silent && cached?.data) {
      setPendingOrdered(cached.data.pending);
      setDecisions(cached.data.feed);
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
        const byTime = new Date(a.created_at) - new Date(b.created_at);
        if (byTime) return byTime;
        // Bulk-scored rows share created_at; fall back to the unique id so the
        // final order is fully deterministic instead of leaking backend order.
        return (Number(a.id) || 0) - (Number(b.id) || 0);
      });
      setPendingOrdered(pending);
      setDecisions(feedRows);
      if (cacheKey) writeCache(cacheKey, { pending, feed: feedRows });
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
      const next = Array.isArray(res?.data) ? res.data : [];
      setRolesBreakdown(next);
      writeCache('home:roles-breakdown', next);
    } catch {
      setRolesBreakdown([]);
    } finally {
      setLoadingRoles(false);
    }
  }, []);

  // Shell and Home share one org-status store/poller. Mirror its full payload
  // into the Hub's warm cache while this page owns only roles-breakdown.
  useEffect(() => {
    if (sharedOrgStatus == null) return;
    setOrgStatus(sharedOrgStatus);
    writeCache('home:org-status', sharedOrgStatus);
  }, [sharedOrgStatus]);

  // Keep the "By role" pending column live without creating a second owner
  // for /agent/org-status.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const rolesRes = await agentApi.rolesBreakdown();
        if (cancelled) return;
        const nextRoles = Array.isArray(rolesRes?.data) ? rolesRes.data : [];
        setRolesBreakdown(nextRoles);
        writeCache('home:roles-breakdown', nextRoles);
      } catch { /* silent */ } finally {
        // The dedicated loadRoles() useEffect was retired in favour of this
        // poll; clear loadingRoles here so the "By role" section doesn't
        // stay stuck on "Loading…" forever.
        if (!cancelled) setLoadingRoles(false);
      }
    };
    void tick();
    const id = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      void tick();
    }, ORG_STATUS_POLL_MS);
    const onVisibility = () => {
      if (typeof document !== 'undefined' && !document.hidden) void tick();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      document.removeEventListener('visibilitychange', onVisibility);
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
    // Repaint the last-known scoped count instantly on a warm re-mount, then
    // revalidate below. Keyed by scope so a role/type count never leaks across
    // filters.
    const key = staleCacheKey({ role_id: filters.role_id, type: filters.type });
    const seeded = readCache(key);
    if (seeded?.data != null) setStaleCount(seeded.data);
    agentApi.needsReevalCount({
      role_id: filters.role_id || undefined,
      type: filters.type || undefined,
    }).then((res) => {
      const n = Number(res?.data?.count) || 0;
      if (!cancelled) setStaleCount(n);
      writeCache(key, n);
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
    // `focus` and `visibilitychange` both fire on a tab return, which used to
    // issue two near-simultaneous fetches; a small guard collapses back-to-back
    // triggers into one so the tab return costs a single round-trip.
    let lastRun = 0;
    const refresh = () => {
      if (document.visibilityState !== 'visible') return;
      const now = Date.now();
      if (now - lastRun < 1000) return;
      lastRun = now;
      void loadDecisionsRef.current({ silent: true });
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
    await Promise.all([loadDecisions(), loadRoles(), refetchOrgStatus()]);
  }, [loadDecisions, loadRoles, refetchOrgStatus]);

  const {
    action: orgAgentAction,
    pause: handlePauseAllAgents,
    resume: handleResumeAllAgents,
  } = useWorkspaceAgentControl({
    loadDecisions,
    loadRoles,
    refetchOrgStatus,
    showToast,
    workspaceControlVersion: orgStatus?.workspace_control_version,
  });

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
    const id = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      void loadAgents();
    }, AGENTS_POLL_MS);
    const onVisibility = () => {
      if (typeof document !== 'undefined' && !document.hidden) void loadAgents();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.clearInterval(id);
      document.removeEventListener('visibilitychange', onVisibility);
    };
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
  const kpis = useMemo(() => orgStatus || ({
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
  }), [decisions, orgStatus, pendingOrdered.length]);

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

  // Workspace controls are aggregate role actions. Running and paused-role
  // counts keep both actions available when the workspace is in a mixed state.
  const agentRunningCount = Number(kpis.active_role_count || 0);
  const agentPausedCount = Number(kpis.paused_role_count || 0);
  const headerAgent = useMemo(() => {
    const running = agentRunningCount;
    const paused = agentPausedCount;
    const enabled = running + paused;
    const lastAt = kpis.last_decision_at ? new Date(kpis.last_decision_at).getTime() : null;
    const ago = Number.isFinite(lastAt)
      ? (() => {
          const diff = Math.max(0, Date.now() - lastAt);
          if (diff < 60_000) return 'just now';
          if (diff < 3_600_000) return `${Math.round(diff / 60_000)}m ago`;
          if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)}h ago`;
          return `${Math.round(diff / 86_400_000)}d ago`;
        })()
      : null;
    const fallbackTick = ago
      ? `Monitoring ${enabled} role${enabled === 1 ? '' : 's'} · last decision ${ago}`
      : `Monitoring ${enabled} role${enabled === 1 ? '' : 's'}`;
    const built = buildAgentPropFromStatus(kpis, {
      isEnabled: enabled > 0,
      controlScope: 'workspace',
      fallbackTick,
    });
    return built ? { ...built, controlAction: orgAgentAction, pending: pendingDecisions } : built;
  }, [agentRunningCount, agentPausedCount, pendingDecisions, kpis, orgAgentAction]);

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="home" onNavigate={onNavigate} /> : null}
      {/* App-shell: the hero + the 3-col shell fill the viewport below the nav so
          the chat dock's composer is visible on load (columns scroll internally). */}
      <div className="home-app">
      {/* Full-width page header — consistent with every other page (spans the
          whole width, above the agent rail + chat dock). */}
      <Reveal className="home-header-reveal" y={8} style={{ flexShrink: 0 }}>
        <AgentHeader
          // Home carries the same breadcrumb strip as every other page (Jobs,
          // detail pages) so the header band occupies the SAME vertical space and
          // the two line up. The lone "Home" crumb is non-clickable text.
          breadcrumbs={[{ label: 'Home' }]}
          kicker={`HUB · ${formatCount(pendingDecisions)} AWAITING YOU · ${formatCount(kpis.active_role_count)} ACTIVE ROLE${kpis.active_role_count === 1 ? '' : 'S'}`}
          title={greetingFor(user)}
          subtitle="Approve, override, or teach the agent's calls — this is where you keep the loop honest."
          // These are bulk edits of role controls, not a workspace execution
          // overlay. Already-paused roles keep their existing hold on Pause.
          agent={headerAgent}
          onPauseAgent={canControlWorkspaceAgent ? handlePauseAllAgents : undefined}
          onResumeAgent={canControlWorkspaceAgent ? handleResumeAllAgents : undefined}
          pauseLabel="Pause all agents"
          resumeLabel="Resume all agents"
          pauseAllCount={headerAgent?.runningRoleCount ?? 0}
          resumeAllCount={headerAgent?.localPausedRoleCount ?? 0}
          controlsDisabledReason={!canControlWorkspaceAgent
            ? 'Only workspace owners can pause running agents or resume eligible paused agents.'
            : null}
          offStateMessage="Open a role and turn on agent mode there — each role has its own monthly cap."
        />
      </Reveal>
      <HomeAgentWorkspace
        activeAgent={activeAgent}
        activeRoleId={activeRoleId}
        agents={agentsWithBudget}
        bulkMode={bulkMode}
        bulkSelected={bulkSelected}
        bulkSelectedRoles={bulkSelectedRoles}
        chatHidden={chatHidden}
        onClearBulk={clearBulk}
        onHideChat={() => setChatHidden(true)}
        onNavigate={onNavigate}
        onReload={reloadAll}
        onSelectAgent={handleSelectAgent}
        onSendBulk={sendBulk}
        onShowChat={() => setChatHidden(false)}
        onToggleBulkMode={toggleBulkMode}
        onToggleSelected={toggleRoleSelected}
        totalAttention={totalAttention}
      >
        <div className="home-body">
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
          <HomeAnalyticsSummary kpis={kpis} orgBudget={orgBudget} onNavigate={onNavigate} />
        </div>
      </HomeAgentWorkspace>
      </div>
    </div>
  );
};

export default HomePage;
