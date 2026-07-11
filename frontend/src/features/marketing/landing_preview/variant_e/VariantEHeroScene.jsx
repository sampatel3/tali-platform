import React, { useEffect, useRef, useState } from 'react';
import { Sparkles, Star } from 'lucide-react';
import { stagger, useAnimate, useInView } from 'motion/react';

import { useReducedMotionSync } from '../../../../shared/motion/previewMotion';
import { Avatar, ScoreChip, VerdictPill, initialsFrom } from '../../../home/atoms';

// ---------------------------------------------------------------------------
// HERO SCENE — the product's core loop, live and right-sized.
//
// One grounded autoplay animation replaces the old hero toggle + oversized
// decision card. A real jobs-board role card (the exact .job-card / .job-head /
// .job-agent-pill markup + Taali agent-ON vocabulary the /jobs board renders)
// turns its agent ON, then three candidates flow out of it into a compact
// decision lane and each lands with a real VerdictPill stamp.
//
// TECHNIQUE (per the merged /home-preview HomeMotionPreview flow: "agent turns
// on → items flow in"): a Motion `useAnimate` timeline, started by `useInView`,
// autoplays once on enter and then holds the settled state. It is NOT scroll-
// scrubbed, so it's reviewable. A small "Replay" affordance re-runs it. Under
// prefers-reduced-motion the scene renders its FINAL composed state with no
// animation (the `data-armed` contract below hides nothing when unarmed).
// ---------------------------------------------------------------------------

const BEATS = {
  activate: 0.55, // agent pill flips ON
  chipsIn: 1.3, // candidates emerge from the card
  verdicts: 2.75, // verdict stamps land
};

// The three candidates that flow out of the role card, each with the real
// score + verdict the product would emit. Maya tops the lane.
const CANDIDATES = [
  { name: 'Maya Chen', score: 88, verdict: 'advance_to_interview', highlight: true },
  { name: 'Jordan Patel', score: 84, verdict: 'advance_to_interview' },
  { name: 'Tariq Al-Ahmad', score: 41, verdict: 'reject' },
];

export const HeroScene = () => {
  const [scope, animate] = useAnimate();
  const inView = useInView(scope, { amount: 0.4 });
  const reduced = useReducedMotionSync();
  // `on` drives the card frame (.agent-on) + the OFF→ON pill crossfade. Reduced
  // motion seeds it ON so the scene reads as its settled final state.
  const [on, setOn] = useState(reduced);
  const [replayNonce, setReplayNonce] = useState(0);
  // Only a COMPLETED play holds the settled state. If a play is interrupted
  // (scrolled away mid-timeline), this stays false so re-entering the viewport
  // replays it — the scene can never be left half-revealed.
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
    animate('.lve-hs-pill-on', { opacity: 0 }, { duration: 0 });
    animate('.lve-hs-glow', { opacity: 0 }, { duration: 0 });
    animate('.lve-hs-chip', { opacity: 0, y: -14 }, { duration: 0 });
    animate('.lve-hs-verdict', { opacity: 0, scale: 0.7 }, { duration: 0 });

    // Beat 1 — the job's agent flips ON (card frame + pill crossfade).
    timers.push(setTimeout(() => { if (!cancelled) setOn(true); }, BEATS.activate * 1000));

    (async () => {
      try {
        await animate([
          ['.lve-hs-pill-on', { opacity: 1, scale: [0.85, 1] }, { duration: 0.4, at: BEATS.activate }],
          ['.lve-hs-glow', { opacity: [0, 0.55, 0.22] }, { duration: 1.0, at: BEATS.activate }],
          // Beat 2 — candidates emerge from the card into the lane.
          ['.lve-hs-chip', { opacity: [0, 1], y: [-14, 0] }, { duration: 0.5, delay: stagger(0.26), at: BEATS.chipsIn }],
          // Beat 3 — each lands with a verdict stamp.
          ['.lve-hs-verdict', { opacity: [0, 1], scale: [0.7, 1] }, { duration: 0.4, delay: stagger(0.26), at: BEATS.verdicts }],
        ]);
        if (!cancelled) completedRef.current = true; // settled — hold it
      } catch {
        /* stopped on cleanup (scrolled away mid-play) — will replay on re-entry */
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
    <div className="lve-hs" ref={scope} {...(reduced ? {} : { 'data-armed': 'true' })}>
      <span className="lve-hs-glow" aria-hidden="true" />

      {/* The real jobs-board role card. */}
      <div className={`job-card lve-hs-card${on ? ' agent-on' : ''}`}>
        <div className="job-head">
          <span
            className="job-star is-locked"
            aria-hidden="true"
            style={{ padding: 2, marginTop: 2, flexShrink: 0, color: on ? 'var(--purple)' : 'var(--ink-soft)', display: 'inline-flex' }}
          >
            <Star size={16} strokeWidth={1.5} fill={on ? 'currentColor' : 'none'} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3, flexWrap: 'wrap' }}>
              <h3 className="role-name">AI Engineer</h3>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: 'var(--mute)' }}>#312</span>
              <span className="chip purple" style={{ fontSize: 10 }}>Role</span>
            </div>
            <div className="role-meta">Engineering · Remote · 312 applied</div>
          </div>

          {/* OFF → ON pill crossfade, using the real .job-agent-pill vocabulary. */}
          <span className="lve-hs-pillbox">
            <span className="job-agent-pill is-off lve-hs-pill-off" style={{ opacity: on ? 0 : 1 }}>OFF</span>
            <span className="job-agent-pill is-on lve-hs-pill-on" title="Agent on for this role">
              <span className="d"><Sparkles size={11} strokeWidth={2.2} /></span>
              AGENT ON
            </span>
          </span>
        </div>

        <div className="job-stats">
          {[
            { k: 'Applied', v: '312' },
            { k: 'Screened', v: '184' },
            { k: 'Assessed', v: '22' },
            { k: 'Advanced', v: '9' },
          ].map((c) => (
            <div className="js-cell" key={c.k}>
              <div className="k">{c.k}</div>
              <div className="v">{c.v}</div>
            </div>
          ))}
        </div>

        <div className="job-foot">
          <span className="lve-hs-foot" style={{ opacity: on ? 1 : 0 }}>
            <Sparkles size={12} aria-hidden="true" /> Agent working the funnel
          </span>
          <span className="job-foot-open">Open pipeline →</span>
        </div>
      </div>

      {/* The decision lane — candidates land here with a verdict. */}
      <div className="lve-hs-lane" aria-hidden="true">
        <div className="lve-hs-lane-head">DECISION LANE · AWAITING YOU</div>
        {CANDIDATES.map((c) => (
          <div className={`lve-hs-chip${c.highlight ? ' is-top' : ''}`} key={c.name}>
            <Avatar initials={initialsFrom(c.name)} size={26} />
            <span className="lve-hs-chip-name">{c.name}</span>
            <ScoreChip score={c.score} size="sm" />
            <span className="lve-hs-verdict">
              <VerdictPill type={c.verdict} />
            </span>
          </div>
        ))}
      </div>

      {!reduced ? (
        <button type="button" className="lve-hs-replay" onClick={replay}>
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
            <path d="M3 12a9 9 0 1 0 3-6.7L3 8" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M3 4v4h4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Replay
        </button>
      ) : null}
    </div>
  );
};

export default HeroScene;
