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
import { formatRelativeAge, formatUsd } from './atoms';
import { HomeNow } from './HomeNow';
import { HomeRoles } from './HomeRoles';
import { HomeSignal } from './HomeSignal';
import { HomeEverything } from './HomeEverything';
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
      // Pending sidebar always shows status=pending. The feed reflects
      // the toolbar's status filter. When the user is on the default
      // ('pending') view, both fetches would be identical — skip the
      // duplicate and reuse the result.
      const pendingParams = { status: 'pending', role_id: filters.role_id || undefined, limit: 100 };
      const feedParams = filtersToParams(filters);
      const sameParams = (
        feedParams.status === 'pending'
        && (feedParams.role_id || undefined) === pendingParams.role_id
        && !feedParams.type
        && !feedParams.q
      );
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
      // Pending sidebar: oldest-first so the top item is the most-aged.
      const pending = [...pendingRows].sort(
        (a, b) => new Date(a.created_at) - new Date(b.created_at),
      );
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

  // Org-status poll — the live KPI strip and tab badge.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const res = await agentApi.orgStatus();
        if (cancelled) return;
        setOrgStatus(res?.data || null);
      } catch { /* silent */ }
    };
    void tick();
    const id = window.setInterval(tick, ORG_STATUS_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => { void loadDecisions(); }, [loadDecisions]);
  useEffect(() => { void loadRoles(); }, [loadRoles]);
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

  const budgetPct = useMemo(() => {
    const cap = Number(kpis.org_budget_cap_cents || 0);
    const spent = Number(kpis.org_budget_spent_cents || 0);
    return cap > 0 ? Math.min(100, (spent / cap) * 100) : 0;
  }, [kpis.org_budget_cap_cents, kpis.org_budget_spent_cents]);

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="home" onNavigate={onNavigate} /> : null}
      <AgentHeader
        kicker={`HUB · ${kpis.pending} PENDING · ${kpis.active_role_count} ACTIVE ROLE${kpis.active_role_count === 1 ? '' : 'S'}`}
        title={greetingFor(user)}
        subtitle="Every decision the agent makes that needs you. Approve, override, or teach it — your calls become its training signal. The long-term goal is full automation; this is where you keep the loop honest."
      />

      <div className="home-body">
        {/* KPI strip */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
          <div className="rq-kpi rq-kpi-emph">
            <div className="l">Awaiting your review</div>
            <div className="v"><em>{kpis.pending}</em></div>
            <div className="d">
              {kpis.pending > 0 ? `oldest ${oldestAgeLabel}` : 'Queue is clear'}
            </div>
          </div>
          <div className="rq-kpi">
            <div className="l">Decisions today</div>
            <div className="v">{kpis.today}</div>
            <div className="d">
              {kpis.auto_applied_today} auto-applied · {kpis.pending_decisions ?? kpis.pending} pending
            </div>
          </div>
          <div className="rq-kpi">
            <div className="l">Org budget · MTD</div>
            <div className="v">
              {formatUsd(kpis.org_budget_spent_cents)}
              {kpis.org_budget_cap_cents > 0
                ? <span style={{ color: 'var(--mute)', fontSize: 14, fontWeight: 400 }}> / {formatUsd(kpis.org_budget_cap_cents)}</span>
                : null}
            </div>
            {kpis.org_budget_cap_cents > 0 ? (
              <div className="rq-bar">
                <i style={{
                  width: `${budgetPct}%`,
                  background: budgetPct > 100 ? 'var(--red)' : budgetPct > 80 ? 'var(--amber)' : 'var(--purple)',
                }} />
              </div>
            ) : (
              <div className="d">No cap configured</div>
            )}
          </div>
          <div className="rq-kpi">
            <div className="l">Override rate · 7d</div>
            <div className="v">{kpis.override_rate_pct.toFixed(0)}%</div>
            <div className="d">
              {kpis.teach_rate_pct > 0 ? `${kpis.teach_rate_pct.toFixed(0)}% taught` : 'No teach signal yet'}
              {orgStatus?.last_decision_at ? ` · last decision ${formatRelativeAge(orgStatus.last_decision_at)} ago` : ''}
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

        <HomeRoles
          rows={rolesBreakdown}
          loading={loadingRoles}
          onNavigate={onNavigate}
        />

        <HomeSignal
          feedback={feedback}
          outcomes={outcomes}
          loading={loadingSignal}
          reload={reloadAll}
        />

        <HomePlatformUpdates />

        <HomeEverything
          rows={decisions}
          onNavigate={onNavigate}
          onSelect={(id) => {
            setSelectedId(id);
            // Force a status reset to 'all' if the row is non-pending so
            // it shows up in the activity feed of the NOW section.
            if (filters.status === 'pending') {
              const row = decisions.find((d) => d.id === id);
              if (row && row.status !== 'pending') {
                setFilters((f) => ({ ...f, status: 'all' }));
              }
            }
            window.scrollTo({ top: 0, behavior: 'smooth' });
          }}
        />
      </div>
    </div>
  );
};

export default HomePage;
