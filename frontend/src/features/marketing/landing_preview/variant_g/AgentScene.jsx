import React, { useEffect, useRef, useState } from 'react';
import { stagger, useAnimate, useInView } from 'motion/react';

import { useReducedMotionSync } from '../../../../shared/motion/previewMotion';
import { CANDIDATES, FUNNEL_STATS, verdictLabel } from './variantG.data';

// ---------------------------------------------------------------------------
// AGENT SCENE — the hero's signature. A job/role card that starts OFF (quiet,
// desaturated, "AGENT OFF"), flips ON, then three candidate rows flow into a
// decision lane and each verdict pill "stamps" in.
//
// TECHNIQUE — a Motion `useAnimate` timeline, armed by `useInView`, autoplays
// ONCE on enter then holds. Mirrors the merged /home HomeMotionPreview + variant
// E hero pattern (agent-on → populate). Beats are the handoff's playAgentScene:
//   • 700ms  — card flips ON (gradient border/glow + purple "hot" cell), pill
//              swaps OFF→ON.
//   • 1150ms — candidate rows animate in from translateY(10px)/opacity 0,
//              staggered +480ms each.
//   • +180ms after each row lands — its verdict pill stamps (scale .7→1.06→1).
// A "↻ Replay" affordance re-runs it. `completedRef` gates the settled state so
// a play interrupted by scrolling away replays on re-entry (never stuck).
//
// Reduced motion → render the settled ON state (rows visible, no data-armed, no
// timeline, no replay button). The scoped CSS hides rows only while `data-armed`.
// ---------------------------------------------------------------------------

// Handoff timings, in seconds.
const FLIP_ON = 0.7; // card + pill flip ON
const ROWS_AT = 1.15; // first row lands
const ROW_STAGGER = 0.48; // each subsequent row
const STAMP_OFFSET = 0.18; // verdict stamps after its row lands

export const AgentScene = () => {
  const [scope, animate] = useAnimate();
  const inView = useInView(scope, { amount: 0.4 });
  const reduced = useReducedMotionSync();
  // `on` drives the card frame (.is-on) + the OFF→ON pill swap. Reduced motion
  // seeds it ON so the scene reads as its settled final state.
  const [on, setOn] = useState(reduced);
  const [replayNonce, setReplayNonce] = useState(0);
  const completedRef = useRef(false);

  useEffect(() => {
    if (reduced) {
      setOn(true);
      return undefined;
    }
    if (!inView || completedRef.current) return undefined;

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
          ['.cand-row', { opacity: [0, 1], y: [10, 0] }, { duration: 0.5, delay: stagger(ROW_STAGGER, { startDelay: ROWS_AT }), ease: [0.2, 0.7, 0.2, 1] }],
          // Beat 3 — each verdict pill stamps in, .18s after its row lands.
          ['.cand-row .verdict', { opacity: [0, 1], scale: [0.7, 1.06, 1] }, { duration: 0.42, delay: stagger(ROW_STAGGER, { startDelay: ROWS_AT + STAMP_OFFSET }), ease: [0.2, 1.3, 0.4, 1] }],
        ]);
        if (!cancelled) completedRef.current = true; // settled — hold it
      } catch {
        /* stopped on cleanup (scrolled away mid-play) — replays on re-entry */
      }
    })();

    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
  }, [inView, reduced, replayNonce, animate]);

  const replay = () => {
    completedRef.current = false;
    setReplayNonce((n) => n + 1);
  };

  return (
    <div className="stage" ref={scope} {...(reduced ? {} : { 'data-armed': 'true' })}>
      <div className="stage-cap">
        <span className="t">Agent · live</span>
        {!reduced ? (
          <button type="button" className="replay" onClick={replay}>↻ Replay</button>
        ) : null}
      </div>

      <div className={`job-card${on ? ' is-on' : ''}`}>
        <div className="job-top">
          <div>
            <div className="job-title">AI Engineer</div>
            <div className="job-meta">#312 · ENGINEERING · REMOTE · 312 APPLIED</div>
          </div>
          {on ? (
            <span className="agent-pill"><span className="led" />AGENT ON</span>
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
    </div>
  );
};

export default AgentScene;
