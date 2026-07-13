import React from 'react';
import { AgentLoop, MotionLoop } from '../../../../shared/motion';
import { useStaticMode } from './sceneProgress';

// ── The agent switch — grey OFF → purple ON, same vocabulary as variant C. ──
const AgentSwitch = ({ on, pressing, onToggle }) => (
  <div className="lvd-switch-wrap">
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={on ? 'Agent on. Turn the agent off.' : 'Agent off. Turn the agent on.'}
      className={`lvd-switch${on ? ' is-on' : ''}${pressing ? ' is-pressing' : ''}`}
      onClick={onToggle}
    >
      <AgentLoop kind="flow" active={on} className="lvd-switch-track" aria-hidden="true">
        <span className="lvd-switch-glow" />
        <span className="lvd-switch-knob">
          <AgentLoop kind="ring" active={on} className="lvd-switch-ring" />
        </span>
      </AgentLoop>
    </button>
    <span className="lvd-switch-caption" aria-hidden="true">
      agent: <b>{on ? 'on' : 'off'}</b>
    </span>
  </div>
);

// ── Section 1 · HERO ─────────────────────────────────────────────────────
// Restrained: a soft purple radial glow on the pale bg (no dot lattice, no
// falling cards). H1 rises per word on flip; the "scroll to watch" cue appears
// once the agent is ON and smooth-scrolls (via Lenis) to the scene.
export const VariantDHero = ({ on, pressing, onToggle, onNavigate, onWatch }) => {
  const staticMode = useStaticMode();

  return (
    <section className="lvd-hero">
      <div className="lvd-hero-inner">
        <div className="lvd-kicker">
          <span className="lvd-kicker-dot" /> AGENT-NATIVE HIRING
        </div>

        <h1 className="lvd-h1">
          {['Turn', 'the', 'agent', 'on.'].map((w, i) => (
            <React.Fragment key={w}>
              <span className="lvd-word" style={{ transitionDelay: `${0.12 + i * 0.09}s` }}>
                {w}
              </span>
              {i < 3 ? ' ' : ''}
            </React.Fragment>
          ))}
        </h1>

        <p className="lvd-sub">
          Taali works your pipeline end to end and measures the one thing every CV now hides: how well
          this person actually works with AI.
        </p>

        <div className="lvd-cta-row">
          <button
            type="button"
            className="lvd-btn lvd-btn--primary"
            onClick={() => onNavigate('demo-lead')}
          >
            See it live <span aria-hidden="true">→</span>
          </button>
          <button type="button" className="lvd-btn lvd-btn--ghost" onClick={onWatch}>
            Watch it work
          </button>
        </div>
      </div>

      <AgentSwitch on={on} pressing={pressing} onToggle={onToggle} />

      <button type="button" className="lvd-scrollcue" onClick={onWatch}>
        <span>scroll to watch</span>
        <MotionLoop
          as="svg"
          kind="bob"
          active={on && !staticMode}
          className="lvd-scrollcue-chev"
          viewBox="0 0 24 24"
          fill="none"
          aria-hidden="true"
        >
          <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </MotionLoop>
      </button>
    </section>
  );
};

export default VariantDHero;
