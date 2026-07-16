import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { RotateCcw, Sparkles } from 'lucide-react';

import {
  MotionTab,
  MotionTabs,
  useReducedMotionSync,
} from '../../shared/motion';
import {
  AGENT_PROMPT_CONCEPTS,
  DEFAULT_AGENT_PROMPT_VARIANT,
} from './agentPromptPreviewConcepts';
import { AgentPromptPreviewSections } from './AgentPromptPreviewSections';
import './AgentPromptPreviewPage.css';

export function AgentPromptPreviewPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const reduced = useReducedMotionSync();
  const [replay, setReplay] = useState(0);
  const requested = (searchParams.get('v') || DEFAULT_AGENT_PROMPT_VARIANT).toLowerCase();
  const active = AGENT_PROMPT_CONCEPTS[requested] ? requested : DEFAULT_AGENT_PROMPT_VARIANT;
  const concept = AGENT_PROMPT_CONCEPTS[active];

  const pick = (variant) => {
    const next = new URLSearchParams(searchParams);
    next.set('v', variant);
    setSearchParams(next, { replace: true });
    setReplay((value) => value + 1);
  };

  return (
    <main className="apc-lab">
      <header className="apc-lab-head">
        <div>
          <p className="apc-kicker"><Sparkles size={13} aria-hidden="true" /> Interaction study · Motion.dev</p>
          <h1>How should an agent ask for help?</h1>
          <p>Four structural directions, one identical scenario, shown at the real chat-dock density.</p>
        </div>
        <div className="apc-lab-actions">
          <span>{reduced ? 'Reduced motion on' : 'Motion on'}</span>
          <button type="button" onClick={() => setReplay((value) => value + 1)}>
            <RotateCcw size={14} aria-hidden="true" /> Replay motion
          </button>
        </div>
      </header>

      <MotionTabs
        value={active}
        onValueChange={pick}
        className="apc-concept-tabs"
        aria-label="Agent prompt design direction"
      >
        {Object.values(AGENT_PROMPT_CONCEPTS).map((item) => {
          const Icon = item.icon;
          return (
            <MotionTab
              key={item.id}
              value={item.id}
              className={active === item.id ? 'is-active' : ''}
              indicatorClassName="apc-tab-indicator"
            >
              <span className="apc-tab-number">{item.number}</span>
              <span className="apc-tab-icon"><Icon size={15} aria-hidden="true" /></span>
              <span><strong>{item.name}</strong><small>{item.verdict}</small></span>
            </MotionTab>
          );
        })}
      </MotionTabs>

      <AgentPromptPreviewSections active={active} concept={concept} replay={replay} />
    </main>
  );
}

export default AgentPromptPreviewPage;
