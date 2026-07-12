import React from 'react';

import { AgentScene } from './AgentScene';

// HERO — centered, type-led. Eyebrow pill (animated gradient dot), H1 with the
// `.grad-text` clipped accent on "decides — with you.", lede, two CTAs, then the
// agent-ON gradient STAGE holding the live agent scene. Two blurred orb glows
// sit behind (z 0); the content wrap sits above (z 1).

export const VariantFHero = ({ onNavigate }) => {
  const go = (target) => () => onNavigate && onNavigate(target);

  return (
    <header className="heroC" id="top">
      <div className="heroC-orb a" aria-hidden="true" />
      <div className="heroC-orb b" aria-hidden="true" />
      <div className="wrap heroC-hero">
        <span className="eyebrow">AGENT-NATIVE HIRING</span>
        <h1 className="display">
          The hiring agent that screens, assesses, and{' '}
          <span className="grad-text">decides — with you.</span>
        </h1>
        <p className="lede">
          An agentic recruiting platform that runs screening, AI-fluency assessment, and defensible
          decisions end to end. You stay in control of every call that matters.
        </p>
        <div className="heroC-actions">
          <button type="button" className="btn btn-primary btn-lg" onClick={go('/signup')}>
            See it live <span className="arw">→</span>
          </button>
          <button type="button" className="btn btn-outline btn-lg" onClick={go('/demo')}>
            Book a demo
          </button>
        </div>
        <AgentScene />
      </div>
    </header>
  );
};

export default VariantFHero;
