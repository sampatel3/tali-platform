import { Sparkles } from 'lucide-react';

import { PresenceSwap } from '../../shared/motion';
import { ConceptDock } from './AgentPromptConceptMockups';

function WorkspaceContext() {
  return (
    <div className="apc-workspace-context" aria-hidden="true">
      <div className="apc-context-head">
        <div><span>Now · needs you</span><strong>Review queue.</strong></div>
        <span className="apc-context-paused"><i /> Agent paused</span>
      </div>
      <div className="apc-context-stats">
        <div><span>Applied</span><strong>145</strong></div>
        <div><span>Scored</span><strong>138</strong></div>
        <div><span>Invited</span><strong>8</strong></div>
        <div><span>Advanced</span><strong>8</strong></div>
      </div>
      <div className="apc-context-grid">
        <div className="apc-context-roles">
          <span>Your agents</span>
          <div className="is-selected"><i /> <b>AI Engineer</b><small>6 decisions waiting</small></div>
          <div><i /> <b>Platform Engineer</b><small>Agent on</small></div>
          <div><i /> <b>Data Engineer</b><small>Agent paused</small></div>
        </div>
        <div className="apc-context-candidate">
          <div className="apc-context-candidate-head"><span>DK</span><div><strong>Dinesh Kumar</strong><small>AI Engineer · score 72</small></div></div>
          <div className="apc-context-score"><span>72</span><div><b>Assessment recommended</b><small>Clears 4 of 5 must-haves</small></div></div>
          <p>Strong ML platform experience with clear production ownership. Knowledge graph depth still needs verification.</p>
          <button type="button">Send assessment</button>
        </div>
      </div>
    </div>
  );
}

function ConceptNotes({ concept }) {
  return (
    <section className="apc-notes" aria-label={`${concept.name} design notes`}>
      <article>
        <span>Core idea</span>
        <h2>{concept.thesis}</h2>
      </article>
      <article>
        <span>Best for</span>
        <p>{concept.bestFor}</p>
      </article>
      <article>
        <span>Watch out</span>
        <p>{concept.watchOut}</p>
      </article>
      <article className="apc-motion-notes">
        <span>Motion choreography</span>
        <ul>
          {concept.motion.map((note) => <li key={note}>{note}</li>)}
        </ul>
      </article>
    </section>
  );
}

export function AgentPromptPreviewSections({ active, concept, replay }) {
  return (
    <>
      <section className="apc-stage" aria-labelledby="apc-stage-title">
        <div className="apc-stage-head">
          <div>
            <span>Direction {concept.number}</span>
            <h2 id="apc-stage-title">{concept.name}</h2>
          </div>
          <span className="apc-stage-verdict">{concept.verdict}</span>
        </div>
        <PresenceSwap presenceKey={`${active}-${replay}`} className="apc-preview-swap">
          <div className="apc-workspace">
            <WorkspaceContext />
            <ConceptDock key={`${active}-${replay}`} variant={active} />
          </div>
        </PresenceSwap>
      </section>

      <ConceptNotes concept={concept} />

      <aside className="apc-recommendation">
        <span className="apc-recommendation-icon" aria-hidden="true"><Sparkles size={17} /></span>
        <div>
          <span>My recommendation</span>
          <strong>Use a small system, not one universal card.</strong>
          <p>Conversation turn for normal asks, composer mode for typed replies, the tray only for real blockers, and the ledger for run/tool history.</p>
        </div>
      </aside>
    </>
  );
}

export default AgentPromptPreviewSections;
