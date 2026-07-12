import React, { useState } from 'react';
import { Filter, Gauge, Layers3, Sparkles } from 'lucide-react';

import { useToast } from '../../context/ToastContext';
import {
  AGENT_LOOP_DURATION,
  AgentLoop,
  MOTION_DURATION,
  MOTION_EASE,
  MOTION_SPRING,
  MotionDisclosure,
  MotionList,
  MotionListItem,
  MotionNumber,
  MotionTab,
  MotionTabs,
  PresenceSwap,
  useReducedMotionSync,
} from '../../shared/motion';
import { Button, Dialog, Sheet } from '../../shared/ui/TaaliPrimitives';
import './MotionShowcasePage.css';

const DURATIONS = [
  ['Instant', '80ms', MOTION_DURATION.instant, 'Press and icon feedback'],
  ['Fast', '140ms', MOTION_DURATION.fast, 'Hover, focus, and exits'],
  ['Base', '200ms', MOTION_DURATION.base, 'Tabs, popovers, and validation'],
  ['Spatial', '280ms', MOTION_DURATION.spatial, 'Sheets and layout continuity'],
  ['Reveal', '480ms', MOTION_DURATION.reveal, 'One-shot narrative entrances'],
  ['Data', '750ms', MOTION_DURATION.data, 'Charts and number interpolation'],
];

const SAMPLE_ROLES = [
  { id: 'data', title: 'Data Engineer', source: 'Workable' },
  { id: 'product', title: 'Product Designer', source: 'Taali' },
  { id: 'sales', title: 'Enterprise AE', source: 'Workable' },
];

export function MotionShowcasePage() {
  const { showToast } = useToast();
  const reduced = useReducedMotionSync();
  const [tab, setTab] = useState('continuity');
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [workableOnly, setWorkableOnly] = useState(false);
  const [metric, setMetric] = useState(42);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const roles = workableOnly
    ? SAMPLE_ROLES.filter((role) => role.source === 'Workable')
    : SAMPLE_ROLES;

  return (
    <main className="motion-lab">
      <header className="motion-lab-hero">
        <div>
          <p className="motion-lab-kicker">Design system · /dev/motion</p>
          <h1>Taali motion</h1>
          <p>Calm, directional motion that explains state change and preserves continuity.</p>
        </div>
        <span className="motion-lab-pref">
          {reduced ? 'Reduced motion · on' : 'Reduced motion · off'}
        </span>
      </header>

      <section className="motion-lab-section" aria-labelledby="motion-token-title">
        <div className="motion-lab-section-head">
          <Gauge size={18} aria-hidden="true" />
          <div>
            <h2 id="motion-token-title">Timing vocabulary</h2>
            <p>One scale shared by Motion and CSS.</p>
          </div>
        </div>
        <div className="motion-token-grid">
          {DURATIONS.map(([name, milliseconds, value, purpose]) => (
            <article key={name} className="motion-token-card">
              <div><strong>{name}</strong><code>{milliseconds}</code></div>
              <div className="motion-token-track">
                <span style={{ width: `${Math.max(12, value * 100)}%` }} />
              </div>
              <p>{purpose}</p>
            </article>
          ))}
        </div>
        <div className="motion-lab-contract">
          <code>enter {MOTION_EASE.enter.join(' · ')}</code>
          <code>layout {MOTION_SPRING.layout.stiffness}/{MOTION_SPRING.layout.damping}</code>
        </div>
      </section>

      <section className="motion-lab-section" aria-labelledby="motion-agent-title">
        <div className="motion-lab-section-head">
          <Sparkles size={18} aria-hidden="true" />
          <div>
            <h2 id="motion-agent-title">Live-agent signals</h2>
            <p>The only continuous motion: semantic, tokenized, in-view, and reduced-motion safe.</p>
          </div>
        </div>
        <div className="motion-agent-grid">
          <article className="motion-lab-demo motion-agent-action">
            <h3>Agent-authored action</h3>
            <AgentLoop as="button" kind="flow" type="button" className="motion-agent-cta">
              <Sparkles size={14} aria-hidden="true" /> Approve recommendation
            </AgentLoop>
            <p>Flow · {AGENT_LOOP_DURATION.flow}s</p>
          </article>
          <AgentLoop as="div" kind="glow" className="motion-agent-shell">
            <AgentLoop as="div" kind="ambient" className="motion-agent-surface">
              <span className="motion-agent-glyph">
                <Sparkles size={17} aria-hidden="true" />
                <AgentLoop kind="ring" className="motion-agent-ring" />
              </span>
              <span>
                <strong>Agent mode is ON</strong>
                <small><AgentLoop kind="pulse" className="motion-agent-dot" /> Screening candidates</small>
              </span>
            </AgentLoop>
          </AgentLoop>
        </div>
      </section>

      <section className="motion-lab-section" aria-labelledby="motion-pattern-title">
        <div className="motion-lab-section-head">
          <Layers3 size={18} aria-hidden="true" />
          <div>
            <h2 id="motion-pattern-title">State-change patterns</h2>
            <p>Shared tabs, keyed presence, disclosure, list layout, and values.</p>
          </div>
        </div>

        <MotionTabs
          value={tab}
          onValueChange={setTab}
          className="motion-lab-tabs"
          aria-label="Motion pattern"
        >
          <MotionTab value="continuity" className={tab === 'continuity' ? 'is-active' : ''}>
            Continuity
          </MotionTab>
          <MotionTab value="choreography" className={tab === 'choreography' ? 'is-active' : ''}>
            Choreography
          </MotionTab>
          <MotionTab value="accessibility" className={tab === 'accessibility' ? 'is-active' : ''}>
            Accessibility
          </MotionTab>
        </MotionTabs>
        <PresenceSwap presenceKey={tab} className="motion-lab-panel" role="tabpanel">
          {tab === 'continuity'
            ? 'Use layout and presence to show where a changed item went.'
            : tab === 'choreography'
              ? 'Stagger dense content by 35ms, default content by 60ms, and stop after eight items.'
              : 'Reduced motion settles critical content immediately and disables smooth scrolling.'}
        </PresenceSwap>

        <div className="motion-lab-demo-grid">
          <article className="motion-lab-demo">
            <h3>Measured disclosure</h3>
            <Button size="sm" variant="secondary" onClick={() => setDetailsOpen((open) => !open)}>
              {detailsOpen ? 'Hide details' : 'Show details'}
            </Button>
            <MotionDisclosure open={detailsOpen}>
              <p className="motion-lab-disclosure-copy">
                Height is measured; content stays mounted for its exit; reduced motion settles instantly.
              </p>
            </MotionDisclosure>
          </article>

          <article className="motion-lab-demo">
            <h3>Previous-to-next number</h3>
            <div className="motion-lab-number"><MotionNumber value={metric} /></div>
            <Button size="sm" variant="secondary" onClick={() => setMetric((value) => (value === 42 ? 87 : 42))}>
              Change value
            </Button>
          </article>
        </div>

        <div className="motion-lab-list-head">
          <h3>Filter and reflow</h3>
          <Button size="sm" variant="secondary" onClick={() => setWorkableOnly((value) => !value)}>
            <Filter size={14} aria-hidden="true" />
            {workableOnly ? 'Show all' : 'Workable only'}
          </Button>
        </div>
        <MotionList className="motion-lab-list">
          {roles.map((role, index) => (
            <MotionListItem key={role.id} index={index} className="motion-lab-role">
              <strong>{role.title}</strong>
              <span>{role.source}</span>
            </MotionListItem>
          ))}
        </MotionList>
      </section>

      <section className="motion-lab-section" aria-labelledby="motion-overlay-title">
        <div className="motion-lab-section-head">
          <Sparkles size={18} aria-hidden="true" />
          <div>
            <h2 id="motion-overlay-title">Transient surfaces</h2>
            <p>Every overlay uses the same presence, exit, and focus contracts.</p>
          </div>
        </div>
        <div className="motion-lab-actions">
          <Button onClick={() => setDialogOpen(true)}>Open dialog</Button>
          <Button variant="secondary" onClick={() => setSheetOpen(true)}>Open sheet</Button>
          <Button variant="secondary" onClick={() => showToast('Motion token contract verified.', 'success')}>
            Show toast
          </Button>
        </div>
      </section>

      <Dialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title="Shared dialog motion"
        description="Backdrop, panel, exit, focus trap, and restoration are one primitive."
        footer={<Button onClick={() => setDialogOpen(false)}>Done</Button>}
      >
        Product code supplies content, not a new animation dialect.
      </Dialog>
      <Sheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        title="Shared sheet motion"
        description="Spatial movement follows the sheet edge and uses the layout timing token."
      >
        Sheets preserve the same interaction and reduced-motion contract everywhere.
      </Sheet>
    </main>
  );
}

export default MotionShowcasePage;
