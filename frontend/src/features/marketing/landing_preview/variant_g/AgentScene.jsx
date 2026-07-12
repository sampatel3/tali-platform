import React, { useEffect, useRef, useState } from 'react';
import { AgentLoop, MOTION_EASE, stagger, useAnimate, useReducedMotionSync } from '../../../../shared/motion';
import { CANDIDATES, FUNNEL_STATS, verdictLabel } from './variantG.data';

// ---------------------------------------------------------------------------
// AGENT SCENE — the hero's signature. A job/role card that starts OFF (quiet,
// desaturated, "AGENT OFF"), flips ON, then three candidate rows flow into a
// decision lane and each verdict pill "stamps" in.
//
// TECHNIQUE — a Motion `useAnimate` timeline that holds in its AGENT-OFF beat at
// the top of the page and fires ONCE, on the user's FIRST scroll down past a
// small threshold (SCROLL_TRIGGER). It never autoplays on load — the hero reads
// OFF until the visitor engages. Beats are the handoff's playAgentScene:
//   • 700ms  — card flips ON (gradient border/glow + purple "hot" cell), pill
//              swaps OFF→ON.
//   • 1150ms — candidate rows animate in from translateY(10px)/opacity 0,
//              staggered +480ms each.
//   • +180ms after each row lands — its verdict pill stamps (scale .7→1.06→1).
// `completedRef` gates the settled state so it holds once played.
//
// Reduced motion → render the settled ON state immediately (rows visible,
// verdicts stamped, no data-armed, no timeline) so meaningful content is never
// gated behind a scroll the user can't trigger. The scoped CSS hides rows only
// while `data-armed`.
// ---------------------------------------------------------------------------

// Handoff timings, in seconds.
const FLIP_ON = 0.7; // card + pill flip ON
const ROWS_AT = 1.15; // first row lands
const ROW_STAGGER = 0.48; // each subsequent row
const STAMP_OFFSET = 0.18; // verdict stamps after its row lands
const SCROLL_TRIGGER = 24; // px scrolled down before the OFF→ON timeline fires

export const AgentScene = () => {
  const [scope, animate] = useAnimate();
  const reduced = useReducedMotionSync();
  // `on` drives the card frame (.is-on) + the OFF→ON pill swap. Reduced motion
  // seeds it ON so the scene reads as its settled final state.
  const [on, setOn] = useState(reduced);
  // `triggered` arms the timeline. Starts false so the scene holds OFF at the
  // top of the page; the first scroll past SCROLL_TRIGGER flips it true (once).
  const [triggered, setTriggered] = useState(false);
  const completedRef = useRef(false);

  // Fire on the user's first scroll down. If the page is already scrolled on
  // mount (deep-link / restored position) arm immediately. Detaches after one
  // trigger. Lenis drives native window scroll, so a plain listener suffices.
  useEffect(() => {
    if (reduced || typeof window === 'undefined') return undefined;
    if (window.scrollY > SCROLL_TRIGGER) {
      setTriggered(true);
      return undefined;
    }
    const onScroll = () => {
      if (window.scrollY > SCROLL_TRIGGER) {
        setTriggered(true);
        window.removeEventListener('scroll', onScroll);
      }
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, [reduced]);

  useEffect(() => {
    if (reduced) {
      setOn(true);
      return undefined;
    }
    if (!triggered || completedRef.current) return undefined;

    let cancelled = false;
    const timers = [];

    // Beat 0 — reset to the quiet, agent-OFF state.
    setOn(false);
    animate('.cand-row', { opacity: 0, y: 10 }, { duration: 0 });
    animate('.cand-row .verdict', { opacity: 0, scale: 0.7 }, { duration: 0 });

    // Beat 1 — the job's agent flips ON (card frame + pill swap via `on`).
    timers.push(setTimeout(() => { if (!cancelled) setOn(true); }, FLIP_ON * 1000));

    (async () => {
      try {
        await animate([
          // Beat 2 — candidate rows flow into the decision lane.
          ['.cand-row', { opacity: [0, 1], y: [10, 0] }, { duration: 0.5, delay: stagger(ROW_STAGGER, { startDelay: ROWS_AT }), ease: MOTION_EASE.emphasized }],
          // Beat 3 — each verdict pill stamps in, .18s after its row lands.
          ['.cand-row .verdict', { opacity: [0, 1], scale: [0.7, 1.06, 1] }, { duration: 0.42, delay: stagger(ROW_STAGGER, { startDelay: ROWS_AT + STAMP_OFFSET }), ease: MOTION_EASE.confirm }],
        ]);
        if (!cancelled) completedRef.current = true; // settled — hold it
      } catch {
        /* stopped on cleanup (unmounted mid-play) */
      }
    })();

    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
  }, [triggered, reduced, animate]);

  return (
    <AgentLoop
      as="div"
      kind="flow"
      active={on}
      className="stage"
      ref={scope}
      {...(reduced ? {} : { 'data-armed': 'true' })}
    >
      <div className="stage-cap">
        <span className="t">Agent · live</span>
      </div>

      <div className={`job-card${on ? ' is-on' : ''}`}>
        <div className="job-top">
          <div>
            <div className="job-title">AI Engineer</div>
            <div className="job-meta">#312 · ENGINEERING · REMOTE · 312 APPLIED</div>
          </div>
          {on ? (
            <AgentLoop kind="flow" className="agent-pill">
              <AgentLoop kind="pulse" className="led" />AGENT ON
            </AgentLoop>
          ) : (
            <span className="agent-pill off"><span className="led" />AGENT OFF</span>
          )}
        </div>

        <div className="funnel-stats">
          {FUNNEL_STATS.map((s) => (
            <div className={`fstat${s.hot ? ' hot' : ''}`} key={s.k}>
              <div className="k">{s.k}</div>
              <div className="v">{s.v}</div>
            </div>
          ))}
        </div>

        <div className="lane">
          <div className="lane-head">
            <span className="lane-title">Decision lane</span>
            <span className="lane-await">awaiting you</span>
          </div>
          {CANDIDATES.map((c) => (
            <div className="cand-row" key={c.name}>
              <div className="avatar">{c.initials}</div>
              <div>
                <div className="cand-name">{c.name}</div>
                <div className="cand-sub">{c.sub}</div>
              </div>
              <span className={`score-chip${c.verdict === 'reject' ? ' low' : ''}`}>{c.score}</span>
              <span className={`verdict ${c.verdict}`}>{verdictLabel(c.verdict)}</span>
            </div>
          ))}
        </div>
      </div>
    </AgentLoop>
  );
};

export default AgentScene;
