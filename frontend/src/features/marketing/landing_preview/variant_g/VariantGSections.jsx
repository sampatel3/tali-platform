import React from 'react';

import { Reveal } from '../../../../shared/motion/previewMotion';
import { CANDIDATES, FUNNEL, DDS, CONTROL, COMPOSITE } from './variantG.data';

// The body sections — each its OWN one-screen destination for a nav item, in
// order: #g-funnel (the 5-step funnel) → #g-fluency (the 5-Ds scorecard) →
// #g-control (the agent advises, you decide — closing with the CTA band). Every
// section is `.section-vp` (min-height min(100svh,900px), content
// vertically centred) so clicking its nav item shows the whole section without
// further scrolling. Entrances reuse the shared one-shot CSS <Reveal>. Copy is
// verbatim from F, ledes trimmed for density.

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

// ── #g-funnel — AGENTIC HIRING: the 5-step funnel, one screen. ──
export const FunnelSection = ({ reduced }) => (
  <section className="section-vp" id="g-funnel">
    <div className="wrap section-vp-in">
      <Reveal className="section-head" reduced={reduced} y={20}>
        <span className="eyebrow">AGENTIC HIRING</span>
        <h2 className="display">One agent, <span className="grad-text">your whole funnel.</span></h2>
        <p className="lede">
          It sources, reads every CV, runs the assessment, and puts a decision in front of you with
          the evidence attached. You approve. It executes.
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
    </div>
  </section>
);

// ── #g-fluency — AI-NATIVE ASSESSMENTS: the 5-Ds scorecard, one screen. ──
export const FluencySection = ({ reduced }) => (
  <section className="section-vp fluencyC" id="g-fluency">
    <div className="wrap section-vp-in">
      <Reveal className="section-head" reduced={reduced} y={20}>
        <span className="eyebrow">AI-NATIVE ASSESSMENTS</span>
        <h2 className="display">Measure how people <span className="grad-text">actually work with AI.</span></h2>
        <p className="lede">
          Five dimensions, scored from the real session. Planted traps they should catch. Same
          rubric, every candidate — engineering or knowledge work.
        </p>
      </Reveal>

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
            <div className="dd-track"><div className="dd-fill" style={{ width: `${d.val}%` }} /></div>
            <div className="dd-val">{d.val}</div>
          </div>
        ))}
      </Reveal>
    </div>
  </section>
);

// ── #g-control — YOU STAY IN CONTROL: the last content section, one screen.
// The agent-advises grid, then the closing CTA band as its finale (relocated
// here when the standalone Proof section was dropped). ──
export const ControlSection = ({ reduced, onNavigate }) => {
  const go = (target) => () => onNavigate && onNavigate(target);
  return (
    <section className="section-vp" id="g-control">
      <div className="wrap section-vp-in">
        <div className="controlC-grid">
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

        <Reveal className="cta-band dark control-cta" reduced={reduced} y={24}>
          <span className="eyebrow" style={{ color: 'var(--lavender)' }}>SEE IT LIVE</span>
          <h2 className="display" style={{ fontSize: 'clamp(28px,3vw,40px)', marginTop: 12 }}>
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
