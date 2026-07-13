import React, { useEffect, useState } from 'react';
import { AgentLoop, MOTION_EASE, stagger, useAnimate, useReducedMotionSync } from '../../../../shared/motion';
import { CANDIDATES, FUNNEL_STATS, verdictLabel } from './variantG.data';

// ---------------------------------------------------------------------------
// AGENT SCENE — the hero's signature. A job/role card that starts OFF (quiet,
// desaturated, "AGENT OFF"), flips ON, then three candidate rows flow into a
// decision lane and each verdict pill "stamps" in.
//
// TECHNIQUE — a Motion `useAnimate` timeline that plays automatically ON MOUNT
// (page load) and then LOOPS continuously: it holds its quiet AGENT-OFF beat,
// flips ON, flows the candidate rows into the decision lane, stamps each
// verdict, holds the settled ON state a moment, then resets to OFF and replays.
// The beats are a relaxed, slowed-down version of the handoff's playAgentScene:
//   • OFF_HOLD  — the card reads quiet/desaturated ("AGENT OFF").
//   • FLIP_ON   — card flips ON (gradient border/glow + purple "hot" cell),
//                 pill swaps OFF→ON.
//   • rows flow in from translateY(12px)/opacity 0, staggered.
//   • each verdict pill stamps (scale .7→1.06→1) shortly after its row lands.
//   • SETTLE_HOLD — the fully-decided ON state holds before looping to OFF.
//
// Reduced motion → render the settled ON state immediately (rows visible,
// verdicts stamped, no data-armed, no timeline, NO loop) so meaningful content
// is never gated behind motion. The scoped CSS hides rows only while
// `data-armed`.
//
// `loop` (default true) can be turned off by a caller that wants a single
// play-through; the landing + preview both loop on load.
// ---------------------------------------------------------------------------

// Handoff timings, in seconds — a relaxed reveal (deliberately slower than the
// original snap so the OFF→ON→decide story reads at a calm pace).
const OFF_HOLD = 1.3; // hold on the quiet AGENT-OFF beat before flipping ON
const FLIP_ON_HOLD = 1.3; // dwell on the ON flip before candidates flow in
const ROWS_AT = 0.2; // first row lands (measured from the flow beat start)
const ROW_STAGGER = 0.75; // each subsequent candidate row
const STAMP_OFFSET = 0.28; // verdict stamps after its row lands
const ROW_DURATION = 0.85; // a row's flow-in
const STAMP_DURATION = 0.6; // a verdict's stamp
const SETTLE_HOLD = 3.4; // hold the fully-decided ON state before looping to OFF

export const AgentScene = ({ loop = true }) => {
  const [scope, animate] = useAnimate();
  const reduced = useReducedMotionSync();
  // `on` drives the card frame (.is-on) + the OFF→ON pill swap. Reduced motion
  // seeds it ON so the scene reads as its settled final state.
  const [on, setOn] = useState(reduced);

  useEffect(() => {
    if (reduced) {
      setOn(true);
      return undefined;
    }

    let cancelled = false;
    const sleep = (s) => new Promise((resolve) => { setTimeout(resolve, s * 1000); });

    // One full play-through: quiet OFF → flip ON → rows flow → verdicts stamp.
    const playOnce = async () => {
      // Beat 0 — reset to the quiet, agent-OFF state.
      setOn(false);
      await animate('.cand-row', { opacity: 0, y: 12 }, { duration: 0 });
      await animate('.cand-row .verdict', { opacity: 0, scale: 0.7 }, { duration: 0 });
      await sleep(OFF_HOLD);
      if (cancelled) return;

      // Beat 1 — the job's agent flips ON (card frame + pill swap via `on`).
      setOn(true);
      await sleep(FLIP_ON_HOLD);
      if (cancelled) return;

      // Beats 2 + 3 — candidate rows flow into the decision lane, then each
      // verdict pill stamps in shortly after its row lands.
      await animate([
        ['.cand-row', { opacity: [0, 1], y: [12, 0] }, { duration: ROW_DURATION, delay: stagger(ROW_STAGGER, { startDelay: ROWS_AT }), ease: MOTION_EASE.emphasized }],
        ['.cand-row .verdict', { opacity: [0, 1], scale: [0.7, 1.06, 1] }, { duration: STAMP_DURATION, delay: stagger(ROW_STAGGER, { startDelay: ROWS_AT + STAMP_OFFSET }), ease: MOTION_EASE.confirm }],
      ]);
    };

    (async () => {
      try {
        do {
          await playOnce();
          if (cancelled) break;
          await sleep(SETTLE_HOLD); // hold the decided state before looping
        } while (loop && !cancelled);
      } catch {
        /* stopped on cleanup (unmounted mid-play) */
      }
    })();

    return () => { cancelled = true; };
  }, [reduced, animate, loop]);

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
