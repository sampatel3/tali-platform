import React from 'react';
import { ArrowUpRight, Check, FileSearch, Inbox, Sparkles } from 'lucide-react';
import {
  MOTION_STAGGER,
  MotionProgress,
  Reveal,
  useReducedMotionSync,
} from '../../../../shared/motion';
import { Avatar, ScoreChip, VerdictPill, initialsFrom } from '../../../home/atoms';

// ---------------------------------------------------------------------------
// THE FUNNEL — shown ONCE. The agent working ONE candidate end to end as a
// single coherent scene: Source → Screen → Assess → Decide → Hand back. One
// candidate (Maya Chen) moves through five compact steps, each lighting as it's
// reached — not four separate feature bands.
//
// Motion: shared in-view Reveal and MotionProgress primitives own the one-shot
// sequence. Reduced motion renders the fully-lit final state immediately.
// ---------------------------------------------------------------------------

const STEPS = [
  {
    key: 'source', n: '01', label: 'Source', Icon: Inbox,
    glimpse: <span className="lve-fn-tag">From Workable</span>,
  },
  {
    key: 'screen', n: '02', label: 'Screen', Icon: FileSearch,
    glimpse: (
      <span className="lve-fn-ev">
        <Check size={11} strokeWidth={2.6} aria-hidden="true" /> Must-haves 6 / 6
      </span>
    ),
  },
  {
    key: 'assess', n: '03', label: 'Assess', Icon: Sparkles,
    glimpse: <span className="lve-fn-score">88<em> / 100</em></span>,
  },
  {
    key: 'decide', n: '04', label: 'Decide', Icon: ArrowUpRight,
    glimpse: <VerdictPill type="advance_to_interview" />,
  },
  {
    key: 'handback', n: '05', label: 'Hand back', Icon: Check,
    glimpse: <span className="lve-fn-tag is-synced">Synced to Workable</span>,
  },
];

export const FunnelScene = () => {
  const reduced = useReducedMotionSync();

  return (
    <div className="lve-fn">
      {/* The candidate threading the whole funnel — one person, start to finish. */}
      <div className="lve-fn-cand">
        <Avatar initials={initialsFrom('Maya Chen')} size={30} />
        <div className="lve-fn-cand-body">
          <span className="lve-fn-cand-name">Maya Chen</span>
          <span className="lve-fn-cand-role">Senior Backend Engineer · one candidate, one pass</span>
        </div>
        <ScoreChip score={88} size="sm" />
      </div>

      <div className="lve-fn-track">
        <span className="lve-fn-rail" aria-hidden="true">
          <MotionProgress
            className="lve-fn-rail-fill"
            amount={0.35}
            reduced={reduced}
          />
        </span>
        <div className="lve-fn-steps">
          {STEPS.map(({ key, n, label, Icon, glimpse }, index) => (
            <Reveal
              as="div"
              className="lve-fn-step"
              key={key}
              amount={0.35}
              delay={index * MOTION_STAGGER.default}
              reduced={reduced}
            >
              <span className="lve-fn-node" aria-hidden="true">
                <Icon size={15} strokeWidth={2} />
              </span>
              <span className="lve-fn-n">{n}</span>
              <span className="lve-fn-label">{label}</span>
              <span className="lve-fn-glimpse">{glimpse}</span>
            </Reveal>
          ))}
        </div>
      </div>
    </div>
  );
};

export default FunnelScene;
