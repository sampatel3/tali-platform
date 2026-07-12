import React from 'react';

import { AgentScene } from './AgentScene';

// HERO — two columns on desktop so the headline block and the live agent stage
// both fit ONE viewport (F stacked them, forcing a scroll). Left: eyebrow pill,
// compact H1 with the `.grad-text` clip on "decides — with you.", a tight lede
// and the two CTAs. Right: the agent-ON gradient STAGE holding the live scene.
// Two blurred orb glows sit behind (z 0); the content grid sits above (z 1).
// Below 940px it stacks (copy, then stage) and the type scale steps down.

export const VariantGHero = ({ onNavigate }) => {
  const go = (target) => () => onNavigate && onNavigate(target);

  return (
    <header className="heroC" id="g-top">
      <div className="heroC-orb a" aria-hidden="true" />
      <div className="heroC-orb b" aria-hidden="true" />
      <div className="wrap heroC-grid">
        <div className="heroC-copy">
          <span className="eyebrow">AGENT-NATIVE HIRING</span>
          <h1 className="display">
            The hiring agent that screens, assesses, and{' '}
            <span className="grad-text">decides — with you.</span>
          </h1>
          <p className="lede">
            One governed agent runs screening, AI-fluency assessment, and defensible decisions end to
            end. You stay in control of every call that matters.
          </p>
          <div className="heroC-actions">
            <button type="button" className="btn btn-primary btn-lg" onClick={go('showcase')}>
              See it live <span className="arw">→</span>
            </button>
            <button type="button" className="btn btn-outline btn-lg" onClick={go('demo-lead')}>
              Book a demo
            </button>
          </div>
        </div>
        <div className="heroC-stage-col">
          <AgentScene />
        </div>
      </div>
    </header>
  );
};

export default VariantGHero;
