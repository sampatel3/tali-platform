// PUBLIC, auth-free PREVIEW of the real /home Hub with the Motion library
// (motion.dev) applied — so the founder can feel how the authenticated product
// would move if we adopted Motion across the app. It is NOT a new design: it
// composes the EXACT production components (AgentHeader + the `.abar` agent-ON
// strip, AgentSidebar, KpiTile/KpiStrip, FunnelBoard, ActivityFeed, the
// AgentDecisionCard detail panel, the agent chat dock) on the SAME fixtures the
// existing HomeShowcaseView uses. Motion only adds motion.
//
// The headline moment: the agent starts OFF and empty. The visitor clicks the
// real in-app "Turn on" activator inside the header strip → the `.abar` lights
// up (real CSS crossfade, enhanced with a Motion glow), the pending badge ticks
// up as the queue populates row-by-row (staggered), and the KPI numbers count
// up. Approving the top decision animates it out and promotes the next pending
// row into the detail slot (AnimatePresence). Everything respects
// prefers-reduced-motion via the shared MotionSystemProvider.

import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AnimatePresence,
  MOTION_DURATION,
  MOTION_STAGGER,
  MotionNumber,
  MotionSystemProvider,
  m,
  motionTransition,
  stagger,
  useReducedMotionSync,
} from '../../shared/motion';

import { AgentHeader } from '../../shared/layout/AgentHeader';
import { KpiTile } from '../../shared/ui/KpiStrip';
import { FunnelBoard } from '../../shared/ui/FunnelBoard';
import { useToast } from '../../context/ToastContext';
import { ActivityFeed } from './ActivityFeed';
import { DecisionDetail } from './HomeNow';
import { AgentSidebar } from './agentchat/AgentSidebar';
import {
  INITIAL_FEED_ROWS,
  SHOWCASE_AGENT,
  SHOWCASE_KPIS,
  SHOWCASE_AGENTS,
  ShowcaseDock,
} from './HomeShowcaseView';
import './home.css';
import './agentchat/agentchat.css';
import './HomeMotionPreview.css';

// A number that counts up to `to` once on mount, then holds. Reduced motion →
// renders the final value immediately with no tween. Used for the KPI/pulse
// tiles so the platform numbers animate in the way they would in-app.
const NumberTicker = ({ to, prefix = '', suffix = '', reduced }) => (
  <MotionNumber
    value={to}
    initialValue={0}
    reduced={reduced}
    format={(value) => `${prefix}${Math.round(value)}${suffix}`}
  />
);

// One-shot fade+rise reveal for the app sections. Uses initial→animate (NOT
// whileInView) because the Hub body scrolls inside its own column — whileInView
// would leave below-the-fold sections stuck hidden until scrolled. Under
// MotionSystemProvider policy drops the transform to its final state.
const Reveal = ({ children, className, style, delay = 0, y = 16, reduced = false }) => (
  <m.div
    className={className}
    style={style}
    initial={reduced ? false : { opacity: 0, y }}
    animate={{ opacity: 1, y: 0 }}
    transition={reduced ? motionTransition.instant : { ...motionTransition.reveal, delay }}
  >
    {children}
  </m.div>
);

// The KPI numbers to count up — keyed to SHOWCASE_KPIS so the labels, unit,
// budget bar, sub-lines and purple emphasis all stay the real fixture; only the
// value becomes an animated ticker.
const KPI_TICKERS = {
  awaiting: { to: 103 },
  today: { to: 14 },
  budget: { to: 18, prefix: '$' },
  override: { to: 8, suffix: '%' },
};

export const HomeMotionPreview = () => {
  const reduced = useReducedMotionSync();
  const { showToast } = useToast() || { showToast: () => {} };

  // Agent starts OFF with an empty queue — the whole point is watching it come
  // to life. Budget seeds from the real activator's default.
  const [on, setOn] = useState(false);
  const [budget, setBudget] = useState(5000);
  const [rows, setRows] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [flashKey, setFlashKey] = useState(0);
  const timers = useRef([]);

  const clearTimers = () => {
    timers.current.forEach((id) => clearTimeout(id));
    timers.current = [];
  };
  useEffect(() => clearTimers, []);

  const selected = useMemo(
    () => rows.find((row) => row.id === selectedId) || null,
    [rows, selectedId],
  );

  // Pending count is DERIVED from the queue, so the `.abar` pending badge ticks
  // up as rows mount and back down as you clear them — no separate counter.
  const pendingCount = rows.filter((row) => row.status === 'pending').length;

  const agent = on
    ? { ...SHOWCASE_AGENT, on: true, paused: false, pending: pendingCount, budgetCents: budget }
    : { on: false, paused: false, pending: 0, spentCents: 0, budgetCents: budget, tick: null, inFlight: false };

  // The agent-ON moment. The real header strip's inline "Turn on" activator
  // fires this with the chosen budget cents. We flip `on`, glow the strip, and
  // populate the decision queue row-by-row (staggered) so it fills in front of
  // you. Reduced motion drops straight to the final populated state.
  const handleActivate = (cents) => {
    clearTimers();
    setBudget(Number(cents) > 0 ? Number(cents) : 5000);
    setOn(true);
    setFlashKey((k) => k + 1);

    if (reduced) {
      setRows(INITIAL_FEED_ROWS);
      setSelectedId(INITIAL_FEED_ROWS[0].id);
      return;
    }

    setRows([]);
    setSelectedId(null);
    INITIAL_FEED_ROWS.forEach((row, i) => {
      const id = setTimeout(() => {
        setRows((prev) => [...prev, row]);
        if (i === 0) setSelectedId(row.id);
      }, 150 * i);
      timers.current.push(id);
    });
  };

  const handleReset = () => {
    clearTimers();
    setOn(false);
    setRows([]);
    setSelectedId(null);
    setBudget(5000);
    setFlashKey(0);
  };

  const patchRow = (id, patch) =>
    setRows((prev) => prev.map((row) => (row.id === id ? { ...row, ...patch } : row)));

  // Approve the top decision → the card animates OUT of the detail slot and the
  // next pending row is promoted IN (AnimatePresence, below). The approved row
  // stays in the feed, now resolved.
  const handleApprove = (decision) => {
    const next = rows.find((row) => row.status === 'pending' && row.id !== decision.id);
    patchRow(decision.id, { status: 'approved', human_disposition: 'approved', resolved_at: new Date().toISOString() });
    setSelectedId(next ? next.id : null);
    const verb = decision.decision_type === 'reject' ? 'rejected' : 'advanced';
    showToast(`Approved — ${decision.candidate_name} ${verb}. In the live product this writes back to Workable.`, 'success');
  };

  const handleAlternative = (decision, alt) => {
    const next = rows.find((row) => row.status === 'pending' && row.id !== decision.id);
    patchRow(decision.id, { status: 'overridden', human_disposition: 'overridden', resolution_note: `override → ${String(alt?.label || 'alternative').toLowerCase()}`, resolved_at: new Date().toISOString() });
    setSelectedId(next ? next.id : null);
    showToast(`Overridden — ${alt?.label || 'alternative'}. Your call becomes the agent's training signal.`, 'success');
  };

  const handleTeach = (decision) => {
    patchRow(decision.id, { status: 'reverted_for_feedback' });
    showToast(`Sent back with feedback — the agent re-evaluates ${decision.candidate_name} with your correction.`, 'info');
  };

  const handleSnooze = () => showToast('Snoozed 1h — it drops back into your queue later.', 'info');

  const kpiTiles = SHOWCASE_KPIS.map((tile) => {
    const t = KPI_TICKERS[tile.key];
    return {
      ...tile,
      value: t
        ? <NumberTicker to={t.to} prefix={t.prefix} suffix={t.suffix} reduced={reduced} />
        : tile.value,
    };
  });

  return (
    <MotionSystemProvider>
        <div className="home-app hmp-root" data-brand="taali" style={{ height: '100vh' }}>
          {/* Floating "this is a mockup" badge + demo controls. */}
          <div className="hmp-badge" role="note">
            <span className="hmp-badge-dot" aria-hidden="true" />
            <span className="hmp-badge-label">PREVIEW · Home on Motion</span>
            {on ? (
              <button type="button" className="hmp-badge-btn" onClick={handleReset}>Replay</button>
            ) : null}
            <a className="hmp-badge-link" href="/landing-preview">Landing ↗</a>
          </div>

          <div className="hmp-header">
            {/* Motion glow that flashes over the strip on the OFF→ON flip,
                layered on top of the real `.abar` CSS crossfade. Remounts via
                key each flip so it replays; reduced motion → no glow. */}
            {!reduced ? (
              <AnimatePresence>
                {flashKey > 0 ? (
                  <m.span
                    key={flashKey}
                    className="hmp-flash"
                    aria-hidden="true"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: [0, 0.6, 0] }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 1.0, ease: 'easeOut' }}
                  />
                ) : null}
              </AnimatePresence>
            ) : null}
            <Reveal reduced={reduced}>
              <AgentHeader
                kicker="HUB · 103 AWAITING YOU · 4 ACTIVE ROLES"
                title="Good morning"
                subtitle="Steer each role's agent in plain English, then approve, override, or teach its calls — this is where you keep the loop honest."
                agent={agent}
                onActivateAgent={handleActivate}
                onPauseAgent={() => showToast('Paused — the agent stops acting until you resume.', 'info')}
                onTurnOffAgent={handleReset}
              />
            </Reveal>
          </div>

          <div className="ac-shell">
            <AgentSidebar agents={SHOWCASE_AGENTS} activeRoleId={109} onSelect={() => {}} />

            <div className="ac-main">
              <div className="home-body">
                {/* KPI / platform-pulse tiles — real KpiTile, staggered in, with
                    the values counting up. */}
                <m.div
                  className="kpi-strip hmp-kpi-strip"
                  style={{ '--kpi-cols': 4 }}
                  initial={reduced ? false : 'hidden'}
                  animate="show"
                  variants={{ hidden: {}, show: { transition: { delayChildren: stagger(MOTION_STAGGER.default, { startDelay: 0.1 }) } } }}
                >
                  {kpiTiles.map((tile) => (
                    <m.div
                      key={tile.key || tile.label}
                      className="hmp-kpi-cell"
                      variants={{
                        hidden: { opacity: 0, y: 14, scale: 0.99 },
                        show: { opacity: 1, y: 0, scale: 1, transition: motionTransition.reveal },
                      }}
                    >
                      <KpiTile {...tile} />
                    </m.div>
                  ))}
                </m.div>

                <Reveal delay={MOTION_DURATION.instant} reduced={reduced}>
                  <FunnelBoard
                    variant="flat"
                    scopeLabel="all roles"
                    stageCounts={{ applied: 312, scored: 184, invited: 9, completed: 4, advanced: 61, rejected: 1905, in_assessment: 6, invited_opened: 7, invited_delivered: 8 }}
                    decisionsByType={{ send_assessment: 20, reject: 80, advance_to_interview: 3, skip_assessment_reject: 0 }}
                  />
                </Reveal>

                <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:items-start">
                  {/* Decision feed. Rows are appended one at a time on flip; a
                      scoped CSS keyframe (.hmp-feed .rq-stream-item) fades each
                      one in as it mounts, so the queue visibly populates. */}
                  <Reveal delay={MOTION_DURATION.fast} className="hmp-feed" reduced={reduced}>
                    {rows.length === 0 ? (
                      <div className="hmp-feed-empty">
                        <strong>Agent is off.</strong>
                        <span>Turn it on in the header to watch the decision queue fill in.</span>
                      </div>
                    ) : (
                      <ActivityFeed
                        rows={rows}
                        selectedId={selectedId}
                        onSelect={setSelectedId}
                        onNavigate={() => {}}
                        subtitle="Click any pending decision to review it on the right — approve, override, or send it back to teach the agent."
                      />
                    )}
                  </Reveal>

                  <div className="lg:sticky lg:top-4">
                    <AnimatePresence mode="wait" initial={false}>
                      {selected ? (
                        <m.div
                          key={selected.id}
                          layout
                          initial={{ opacity: 0, y: 14 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0, y: -14 }}
                          transition={motionTransition.spatial}
                        >
                          <DecisionDetail
                            decision={selected}
                            onApprove={handleApprove}
                            onAlternative={handleAlternative}
                            onTeach={handleTeach}
                            onSnooze={handleSnooze}
                            onNavigate={() => {}}
                            onReEvaluate={() => {}}
                            busy={false}
                          />
                        </m.div>
                      ) : (
                        <m.div
                          key="empty"
                          className="hmp-detail-empty"
                          initial={{ opacity: 0 }}
                          animate={{ opacity: 1 }}
                          exit={{ opacity: 0 }}
                          transition={motionTransition.base}
                        >
                          {on
                            ? 'Queue clear — every decision reviewed. Nice.'
                            : 'Turn the agent on to see its first recommendation here.'}
                        </m.div>
                      )}
                    </AnimatePresence>
                  </div>
                </div>
              </div>
            </div>

            <ShowcaseDock onAct={(msg) => showToast(msg, 'info')} />
          </div>
        </div>
    </MotionSystemProvider>
  );
};

export default HomeMotionPreview;
