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
import { KpiStrip } from '../../shared/ui/KpiStrip';
import { HomeNow } from './HomeNow';
import { HomeMonitoring } from './HomeMonitoring';
import { HomePlatformUpdates } from './HomePlatformUpdates';
import { AgentSidebar } from './agentchat/AgentSidebar';
import { AgentChatDock } from './agentchat/AgentChatDock';
import './agentchat/agentchat.css';

const ORG_STATUS_POLL_MS = 30_000;

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
  }), [searchParams]);

  const setFilters = useCallback((updater) => {
    setSearchParams((prev) => {
      const current = {
        role: prev.get('role') || null,
        type: prev.get('type') || null,
        status: prev.get('status') || 'pending',
        q: prev.get('q') || null,
        pending: prev.get('pending') || null,
      };
      const next = typeof updater === 'function'
        ? updater({
          role_id: current.role,
          type: current.type,
          status: current.status,
          q: current.q,
        })
        : updater;
      const out = new URLSearchParams();
      if (next.role_id) out.set('role', String(next.role_id));
      if (next.type) out.set('type', String(next.type));
      if (next.status && next.status !== 'pending') out.set('status', String(next.status));
      if (next.q) out.set('q', String(next.q));
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
  const [rolesBreakdown, setRolesBreakdown] = useState([]);
  const [feedback, setFeedback] = useState([]);
  const [outcomes, setOutcomes] = useState([]);
  const [loadingDecisions, setLoadingDecisions] = useState(true);
  const [loadingRoles, setLoadingRoles] = useState(true);
  const [loadingSignal, setLoadingSignal] = useState(true);

  // Agent chat (Option C): active agents for the left rail + the dock's target
  // role. Progressive enhancement — if the /agent-chat endpoints aren't
  // reachable, `agents` stays empty and the home renders exactly as before.
  const [agents, setAgents] = useState([]);
  const [activeRoleId, setActiveRoleId] = useState(null);
  const [dockCollapsed, setDockCollapsed] = useState(false);
  // Bulk messaging: select several roles, send one message that fans out to
  // each role's own thread (separate audit). bulkSelected holds role_ids.
  const [bulkMode, setBulkMode] = useState(false);
  const [bulkSelected, setBulkSelected] = useState(() => new Set());

  // Track in-flight reloads so rapid clicks don't pile up requests.
  const reloadCounter = useRef(0);

  const loadDecisions = useCallback(async () => {
    setLoadingDecisions(true);
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

  const loadSignal = useCallback(async () => {
    setLoadingSignal(true);
    try {
      const [fbRes, outRes] = await Promise.all([
        agentApi.listFeedback({ limit: 20 }),
        agentApi.realisedOutcomes({ limit: 20 }),
      ]);
      setFeedback(Array.isArray(fbRes?.data) ? fbRes.data : []);
      setOutcomes(Array.isArray(outRes?.data) ? outRes.data : []);
    } catch {
      setFeedback([]);
      setOutcomes([]);
    } finally {
      setLoadingSignal(false);
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
  useEffect(() => { void loadSignal(); }, [loadSignal]);

  const reloadAll = useCallback(async () => {
    await Promise.all([loadDecisions(), loadRoles(), loadSignal()]);
    try {
      const res = await agentApi.orgStatus();
      setOrgStatus(res?.data || null);
    } catch { /* silent */ }
  }, [loadDecisions, loadRoles, loadSignal]);

  // Poll the active-agents list for the left rail + notification badges.
  // Self-contained: a missing/erroring endpoint degrades to the plain home
  // (no rail/dock), so this can't break the central page.
  const loadAgents = useCallback(async () => {
    try {
      const { data } = await agentChat.listConversations();
      const list = Array.isArray(data?.agents) ? data.agents : [];
      setAgents(list);
      setActiveRoleId((cur) => {
        if (cur && list.some((a) => a.role_id === cur)) return cur;
        const ranked = [...list].sort((a, b) => (b.attention || 0) - (a.attention || 0));
        return ranked.length ? ranked[0].role_id : null;
      });
    } catch {
      setAgents([]);
    }
  }, []);

  useEffect(() => {
    void loadAgents();
    const id = window.setInterval(() => { void loadAgents(); }, ORG_STATUS_POLL_MS);
    return () => window.clearInterval(id);
  }, [loadAgents]);

  // Selecting an agent focuses both the chat dock and the decision feed on
  // that role so the two surfaces stay in sync.
  const handleSelectAgent = useCallback((roleId) => {
    setActiveRoleId(roleId);
    setDockCollapsed(false);
    setFilters((f) => ({ ...f, role_id: roleId }));
  }, [setFilters]);

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
      <div className={`ac-shell ${dockCollapsed ? 'ac-dock-collapsed' : ''}`}>
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
        {/* One compact KPI row for the Decision Hub — decision-focused, no
            duplication of the hero chips or the pipeline strip below:
              Awaiting you · Decisions today · Org budget·MTD · Override 7d.
            "In pipeline" / "Active roles" were dropped — the funnel strip and
            the kicker already carry them. "Awaiting you" reads the same
            pendingDecisions the hero chips sum to. Shared <KpiStrip> tile so
            this matches the jobs-list strip exactly. */}
        <KpiStrip
          columns={4}
          tiles={[
            {
              key: 'awaiting',
              label: 'Awaiting you',
              value: formatCount(pendingDecisions),
              emph: pendingDecisions > 0,
              sub: orgNotYetDecided > 0
                ? `${formatCount(orgNotYetDecided)} decision pending`
                : (pendingDecisions > 0 ? 'all flagged' : 'queue clear'),
            },
            {
              key: 'today',
              label: 'Decisions today',
              value: formatCount(kpis.today),
              sub: kpis.auto_applied_today > 0 ? `${formatCount(kpis.auto_applied_today)} auto-applied` : 'none auto-applied',
            },
            {
              key: 'budget',
              label: 'Org budget · MTD',
              value: orgBudget.value,
              unit: orgBudget.unit,
              bar: kpis.org_budget_cap_cents > 0 ? orgBudget : null,
              sub: orgBudget.sub,
            },
            {
              key: 'override',
              label: 'Override rate · 7d',
              value: `${kpis.override_rate_pct.toFixed(0)}%`,
              sub: kpis.teach_rate_pct > 0 ? `${kpis.teach_rate_pct.toFixed(0)}% taught` : 'no teach signal yet',
            },
          ]}
        />

        <HomeNow
          decisions={decisions}
          pendingOrdered={pendingOrdered}
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

        {/* One consolidated analytics surface — Activity / Outcomes / Quality
            / History, scoped by a shared role + window. Replaces the former
            By-role, Signal, History & analytics, and backlog-trend sections. */}
        <HomeMonitoring
          rolesBreakdown={rolesBreakdown}
          feedback={feedback}
          outcomes={outcomes}
          loadingSignal={loadingSignal}
          reload={reloadAll}
          onNavigate={onNavigate}
          onSelect={(id) => {
            setSelectedId(id);
            // History rows are always resolved (non-pending). If the NOW
            // feed is on its default pending view, widen it to 'all' so the
            // selected row is visible there after we scroll up.
            if (filters.status === 'pending') {
              setFilters((f) => ({ ...f, status: 'all' }));
            }
            window.scrollTo({ top: 0, behavior: 'smooth' });
          }}
        />

        <HomePlatformUpdates />
      </div>
        </div>
        {dockCollapsed ? (
          <div className="ac-dock-handle">
            <button className="ac-reopen" onClick={() => setDockCollapsed(false)}>
              <MessageSquare size={15} /> Ask the agent
              {totalAttention > 0 && <span className="ac-badge-count">{totalAttention}</span>}
            </button>
          </div>
        ) : (
          <AgentChatDock
            roleId={activeRoleId}
            roleName={activeAgent?.role_name}
            agentEnabled={activeAgent ? activeAgent.agent_enabled : true}
            onReload={reloadAll}
            onCollapse={() => setDockCollapsed(true)}
            bulkSelectedRoles={bulkSelectedRoles}
            onSendBulk={sendBulk}
            onClearBulk={clearBulk}
          />
        )}
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
