// /home — agent-first landing. See docs/HOME_HUB_DESIGN.md for the full
// design. This file is the orchestrator: fetches data, wires URL-backed
// filters, composes the section components.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { agent as agentApi } from '../../shared/api';
import { AgentHeader } from '../../shared/layout/AgentHeader';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

import './home.css';
import { formatCount, budgetTile, inPipelineFromStageCounts } from '../../shared/metrics';
import { formatRelativeAge } from './atoms';
import { HomeNow } from './HomeNow';
import { HomeMonitoring } from './HomeMonitoring';
import { HomePlatformUpdates } from './HomePlatformUpdates';

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

// Compact pending-by-type breakdown shown inside the purple header — the
// glanceable version of what used to be a full body section. Dot colours
// mirror the TypeBadge vocabulary; send_assessment uses lavender so it reads
// on the purple slab. Escalate only shows when there's something to escalate.
const HEADER_PENDING_BUCKETS = [
  { key: 'advance', label: 'Advance', color: 'var(--green)', types: ['advance_to_interview'] },
  { key: 'send_assessment', label: 'Send assessment', color: 'var(--purple-lav)', types: ['send_assessment', 'resend_assessment_invite'] },
  { key: 'reject', label: 'Reject', color: 'var(--red)', types: ['reject'] },
  { key: 'skip_assessment_reject', label: 'Pre-screen', color: 'var(--red-deep)', types: ['skip_assessment_reject'] },
  { key: 'escalate', label: 'Escalate', color: 'var(--amber)', types: ['escalate_low_confidence'], hideWhenZero: true },
];

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

  const oldestAgeLabel = useMemo(() => {
    if (!kpis.oldest_pending_age_seconds) return '—';
    const s = Number(kpis.oldest_pending_age_seconds);
    if (s < 60) return 'just now';
    const m = Math.round(s / 60);
    if (m < 60) return `${m}m`;
    const h = Math.round(m / 60);
    if (h < 24) return `${h}h`;
    return `${Math.round(h / 24)}d`;
  }, [kpis.oldest_pending_age_seconds]);

  // Org budget tile (spent / cap + bar + projection) — same helper the Jobs
  // and role strips use so the format is identical everywhere.
  const orgBudget = useMemo(
    () => budgetTile(kpis.org_budget_spent_cents, kpis.org_budget_cap_cents),
    [kpis.org_budget_cap_cents, kpis.org_budget_spent_cents],
  );

  // "In pipeline" — open candidates summed across every role, from the same
  // /agent/roles/breakdown stage_counts the Jobs strip uses.
  const orgPipeline = useMemo(() => {
    const list = Array.isArray(rolesBreakdown) ? rolesBreakdown : [];
    const total = list.reduce((acc, r) => acc + inPipelineFromStageCounts(r?.stage_counts), 0);
    const withCandidates = list.filter((r) => inPipelineFromStageCounts(r?.stage_counts) > 0).length;
    return { total, withCandidates };
  }, [rolesBreakdown]);

  const headerPendingBuckets = useMemo(() => {
    const counts = kpis.pending_by_type || {};
    const sumFor = (types) => types.reduce((n, t) => n + (Number(counts[t]) || 0), 0);
    return HEADER_PENDING_BUCKETS
      .map((b) => ({ ...b, count: sumFor(b.types) }))
      .filter((b) => !(b.hideWhenZero && b.count === 0));
  }, [kpis.pending_by_type]);

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="home" onNavigate={onNavigate} /> : null}
      <AgentHeader
        breadcrumbs={[{ label: 'Home' }]}
        kicker={`HUB · ${kpis.pending} PENDING · ${kpis.active_role_count} ACTIVE ROLE${kpis.active_role_count === 1 ? '' : 'S'}`}
        title={greetingFor(user)}
        subtitle="Every decision the agent makes that needs you. Approve, override, or teach it — your calls become its training signal. The long-term goal is full automation; this is where you keep the loop honest."
        postTitle={headerPendingBuckets.length ? (
          <div className="ah-pending-strip">
            <span className="ah-pending-strip-label">Pending</span>
            {headerPendingBuckets.map((b) => (
              <span key={b.key} className="ah-pending-strip-item">
                <span className="ah-pending-strip-dot" style={{ background: b.color }} aria-hidden="true" />
                <b>{b.count}</b> {b.label}
              </span>
            ))}
          </div>
        ) : null}
      />

      <div className="home-body">
        {/* Shared org KPI strip — the same four tiles the Jobs list shows:
            In pipeline · Active roles · Awaiting you · Org budget · MTD.
            "Awaiting you" is the pending-decision queue (kpis.pending), the
            same metric the Jobs strip sums per role. Formatting (separators,
            $spent / $cap + bar) comes from src/shared/metrics. */}
        <div className="rq-kpis">
          <div className="rq-kpi">
            <div className="l">In pipeline</div>
            <div className="v">{formatCount(orgPipeline.total)}</div>
            <div className="d">
              across {formatCount(kpis.active_role_count)} active role{kpis.active_role_count === 1 ? '' : 's'}
            </div>
          </div>
          <div className="rq-kpi">
            <div className="l">Active roles</div>
            <div className="v">{formatCount(kpis.active_role_count)}</div>
            <div className="d">
              {orgPipeline.withCandidates > 0 ? `${formatCount(orgPipeline.withCandidates)} with candidates` : 'no candidates yet'}
            </div>
          </div>
          <div className="rq-kpi rq-kpi-emph">
            <div className="l">Awaiting you</div>
            <div className="v"><em>{formatCount(kpis.pending)}</em></div>
            <div className="d">
              {kpis.pending > 0 ? `oldest ${oldestAgeLabel}` : 'queue clear'}
            </div>
          </div>
          <div className="rq-kpi">
            <div className="l">Org budget · MTD</div>
            <div className="v">
              {orgBudget.value}
              {orgBudget.unit
                ? <span style={{ color: 'var(--mute)', fontSize: 15, fontWeight: 400 }}> {orgBudget.unit}</span>
                : null}
            </div>
            {kpis.org_budget_cap_cents > 0 ? (
              <div className="rq-bar">
                <i style={{ width: `${orgBudget.pct}%`, background: orgBudget.over ? 'var(--red)' : 'var(--purple)' }} />
              </div>
            ) : null}
            <div className="d">{orgBudget.sub}</div>
          </div>
        </div>

        {/* Decision-Hub health row — agent-quality metrics, org-only. Same
            tile component as the strip above, secondary by position. */}
        <div className="rq-kpis rq-kpis-secondary">
          <div className="rq-kpi">
            <div className="l">Decisions today</div>
            <div className="v">{formatCount(kpis.today)}</div>
            <div className="d">
              {formatCount(kpis.auto_applied_today)} auto · {formatCount(kpis.pending_decisions ?? kpis.pending)} pending
            </div>
          </div>
          <div className="rq-kpi">
            <div className="l">Override rate · 7d</div>
            <div className="v">{kpis.override_rate_pct.toFixed(0)}%</div>
            <div className="d">
              {kpis.teach_rate_pct > 0 ? `${kpis.teach_rate_pct.toFixed(0)}% taught` : 'no teach signal yet'}
              {orgStatus?.last_decision_at ? ` · last ${formatRelativeAge(orgStatus.last_decision_at)} ago` : ''}
            </div>
          </div>
        </div>

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
  );
};

export default HomePage;
