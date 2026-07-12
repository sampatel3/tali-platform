import React from 'react';

import { AgentLoop, Reveal } from '../../../../shared/motion';
import { CANDIDATES, FUNNEL, DDS, CONTROL, PROOF, COMPOSITE } from './variantF.data';

// The scrolling body sections, in narrative order: Problem → Agentic hiring
// (funnel + folded-in "You decide" control block) → AI-native assessments (5-Ds
// scorecard + folded-in proof stats) → Close CTA. Section entrances reuse the
// shared once-in-view <Reveal>. Copy is
// verbatim from the handoff.

const TICK = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M20 6 9 17l-5-5" />
  </svg>
);

// Glimpse chip(s) pinned to each funnel card's foot.
const FunnelViz = ({ viz }) => {
  if (viz.kind === 'evidence') {
    return (
      <div className="evid-row"><span className="tick">✓</span><span>{viz.text}</span></div>
    );
  }
  if (viz.kind === 'score') {
    return <div className="mini-score">{viz.value}<small>{viz.unit}</small></div>;
  }
  return (
    <div className="fchip-row">
      {viz.chips.map((c) => (
        <span key={c.label} className={`fchip${c.variant === 'plain' ? ' plain' : c.variant === 'ok' ? ' ok' : ''}`}>{c.label}</span>
      ))}
    </div>
  );
};

// ── §2 PROBLEM — a single tinted statement card. ──
export const ProblemSection = ({ reduced }) => (
  <section className="problemC">
    <div className="wrap">
      <Reveal className="card" reduced={reduced} y={24}>
        <span className="eyebrow mute">THE SHIFT</span>
        <p className="big">
          Everyone works with AI now.
          <span className="dim">A CV can’t prove how well. A conversation can only hint.</span>
          <span className="grad-text">You need to see the real work.</span>
        </p>
      </Reveal>
    </div>
  </section>
);

// ── §3 AGENTIC HIRING — the 5-step funnel + the folded-in control block. ──
export const FunnelSection = ({ reduced }) => (
  <section className="section" id="funnel" style={{ paddingTop: 0 }}>
    <div className="wrap">
      <Reveal className="section-head" reduced={reduced} y={20}>
        <span className="eyebrow">AGENTIC HIRING</span>
        <h2 className="display">One agent, <span className="grad-text">your whole funnel.</span></h2>
        <p className="lede">
          It finds candidates, reads every CV, runs the assessment, and puts a decision in front of
          you with the evidence attached. You approve. It executes.
        </p>
      </Reveal>

      <div className="funnel">
        {FUNNEL.map((s, i) => (
          <Reveal key={s.n} className="fstep" reduced={reduced} delay={i * 0.06}>
            {i < FUNNEL.length - 1 ? <span className="fflow-track" aria-hidden="true" /> : null}
            <span className="fnum">{s.n}</span>
            <h3>{s.key}</h3>
            <p>{s.body}</p>
            <div className="fviz"><FunnelViz viz={s.viz} /></div>
          </Reveal>
        ))}
      </div>

      {/* You stay in control — folded into the agentic-hiring pillar. */}
      <div id="control" className="controlC-grid" style={{ marginTop: 100, paddingTop: 64, borderTop: '1px solid var(--line)' }}>
        <div className="control-copy">
          <span className="eyebrow">YOU STAY IN CONTROL</span>
          <h2 className="display">The agent advises. <span className="grad-text">You decide.</span></h2>
          <div className="control-points">
            {CONTROL.map((c, i) => (
              <Reveal key={c} className="control-point" reduced={reduced} delay={i * 0.05}>
                <span className="cp-ico">{TICK}</span>
                <p>{c}</p>
              </Reveal>
            ))}
          </div>
        </div>
        <Reveal className="glow-card" reduced={reduced} y={24}>
          <div className="dg-head">DECISION · AWAITING YOU</div>
          <div className="dg-card">
            <div className="dg-row">
              <div className="avatar">MC</div>
              <div>
                <div className="dg-name">Maya Chen</div>
                <div className="dg-sub">AI ENGINEER #312 · 88/100</div>
              </div>
              <span className="dg-verdict">Advance</span>
            </div>
            <div className="dg-ev"><span className="lk">EV·1</span>Caught the unsafe default in the eval gate.</div>
            <div className="dg-ev"><span className="lk">EV·2</span>Verified before claiming done — ran the suite twice.</div>
          </div>
        </Reveal>
      </div>
    </div>
  </section>
);

// ── §4 AI-NATIVE ASSESSMENTS — the 5-Ds scorecard + the folded-in proof row. ──
export const FluencySection = ({ reduced }) => (
  <section className="section fluencyC" id="fluency">
    <div className="wrap">
      <Reveal className="section-head" reduced={reduced} y={20}>
        <span className="eyebrow">AI-NATIVE ASSESSMENTS</span>
        <h2 className="display">Measure how people <span className="grad-text">actually work with AI.</span></h2>
        <p className="lede">
          Five dimensions, scored from the real session. Planted traps they should catch.
          Verification that’s scored, not assumed. Engineering or knowledge work — the same rubric
          for every candidate.
        </p>
      </Reveal>

      <div style={{ maxWidth: 820, margin: '0 auto' }}>
        <Reveal className="scorecard" reduced={reduced} y={24}>
          <div className="sc-head">
            <div className="who">
              <div className="avatar">MC</div>
              <div>
                <div className="sc-title">Maya Chen · AI-fluency</div>
                <div className="sc-sub">SCORED FROM SESSION · AI ENGINEER #312</div>
              </div>
            </div>
            <div className="sc-total">
              <div className="big">{COMPOSITE}</div>
              <div className="lbl">Composite / 100</div>
            </div>
          </div>
          {DDS.map((d) => (
            <div className="dd-row" key={d.name}>
              <div>
                <div className="dd-name">{d.name}</div>
                <div className="dd-def">{d.def}</div>
              </div>
              <div className="dd-track"><AgentLoop kind="flow" className="dd-fill" style={{ width: `${d.val}%` }} /></div>
              <div className="dd-val">{d.val}</div>
            </div>
          ))}
        </Reveal>
      </div>

      <div className="proof-grid" id="proof" style={{ maxWidth: 900, margin: '88px auto 0', paddingTop: 8 }}>
        {PROOF.map((p, i) => (
          <Reveal key={p.num} className="proof-item" reduced={reduced} delay={i * 0.06}>
            <div className="proof-num">{p.num}</div>
            <div className="proof-lbl">{p.lbl}</div>
          </Reveal>
        ))}
      </div>
    </div>
  </section>
);

// ── §6 CLOSE — CTA band on the dark agent gradient. ──
export const CloseSection = ({ reduced, onNavigate }) => {
  const go = (target) => () => onNavigate && onNavigate(target);
  return (
    <section className="section" style={{ paddingTop: 0 }}>
      <div className="wrap">
        <Reveal className="cta-band dark" reduced={reduced} y={24}>
          <span className="eyebrow" style={{ color: 'var(--lavender)' }}>SEE IT LIVE</span>
          <h2 className="display" style={{ fontSize: 'clamp(32px,3.8vw,48px)', marginTop: 16 }}>
            Ready to put the agent to work?
          </h2>
          <div className="cta-actions">
            <button type="button" className="btn btn-lg" style={{ background: '#fff', color: '#241147' }} onClick={go('/signup')}>
              See it live <span className="arw">→</span>
            </button>
            <button type="button" className="btn btn-lg btn-outline" style={{ background: 'rgba(255,255,255,.08)', color: '#fff', borderColor: 'rgba(255,255,255,.28)' }} onClick={go('/demo')}>
              Book a demo
            </button>
          </div>
        </Reveal>
      </div>
    </section>
  );
};

// Referenced by the smoke test to assert the lane candidates thread the page.
export const HERO_LANE = CANDIDATES;
