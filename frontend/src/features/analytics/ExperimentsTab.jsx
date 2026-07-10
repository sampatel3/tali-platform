// A/B TASKS — assessment-task experiments, one card per experiment with two
// arm columns. The leading arm (higher score discrimination) is highlighted,
// but ONLY once both arms clear the sample gate; below it the card reads "not
// yet significant" and declares no winner. Source: /analytics/experiments/
// comparison — first the experiment list, then each experiment's per-arm
// metrics. Pilot-honest: every rate carries its denominator.

import React, { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';

import { analytics as analyticsApi } from '../../shared/api';
import { safeNum } from './analyticsFormat';

const armScore = (arm) => {
  const avg = arm?.discrimination?.score?.avg;
  return avg == null ? null : Number(avg);
};

const advanceHireLabel = (arm) => {
  const o = arm?.outcome || {};
  const adv = safeNum(o.advanced);
  const hired = safeNum(o.hired);
  if (adv <= 0) return 'advance→hire —';
  return `advance→hire ${Math.round((hired / adv) * 100)}%`;
};

const ArmColumn = ({ arm, leading }) => {
  const score = armScore(arm);
  const completed = safeNum(arm?.n_completed);
  return (
    <div className={`an-abarm${leading ? ' win' : ''}`}>
      {leading ? <span className="winflag">▲ leading</span> : null}
      <div className="al">Arm {String(arm?.arm_key || '?').toUpperCase()}{arm?.task_name ? ` · ${arm.task_name}` : ''}</div>
      <div className="av">{score != null ? Math.round(score) : '—'}<small> avg</small></div>
      <div className="asub">
        {completed} completion{completed === 1 ? '' : 's'} · {advanceHireLabel(arm)}
      </div>
    </div>
  );
};

const ExperimentCard = ({ experiment }) => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    analyticsApi.experimentsComparison({ experiment_id: experiment.id })
      .then((res) => { if (!cancelled) setData(res?.data || null); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [experiment.id]);

  const arms = useMemo(() => (Array.isArray(data?.arms) ? data.arms : []), [data]);
  const anySmall = arms.length === 0 || arms.some((a) => a.small_sample);
  const totalCompleted = arms.reduce((acc, a) => acc + safeNum(a.n_completed), 0);

  // Leading arm = highest discrimination avg, only declared when the sample
  // gate is met (matches the backend's "no auto winner" honesty).
  const leadingArmId = useMemo(() => {
    if (anySmall || arms.length < 2) return null;
    let best = null;
    let bestScore = -Infinity;
    arms.forEach((a) => {
      const s = armScore(a);
      if (s != null && s > bestScore) { bestScore = s; best = a.arm_id; }
    });
    return best;
  }, [arms, anySmall]);

  const status = String(experiment.status || 'running');
  const running = status.toLowerCase() === 'running' || status.toLowerCase() === 'active';

  return (
    <div className="an-abcard">
      <div className="ch">
        <div>
          <div className="ct2">{experiment.name || experiment.key}</div>
          <div className="cd">
            {totalCompleted} completion{totalCompleted === 1 ? '' : 's'}
            {anySmall ? ' · needs more results before a leader is shown' : ' · enough results to compare'}
          </div>
        </div>
        <span className="an-runtag">
          {running ? <span className="dot" aria-hidden="true" /> : null}
          {running ? 'Running' : status}
        </span>
      </div>
      {loading && !data ? (
        <div className="an-empty"><Loader2 size={13} className="animate-spin" aria-hidden="true" /> Loading results…</div>
      ) : arms.length === 0 ? (
        <div className="an-empty">No results for this comparison yet.</div>
      ) : (
        <div className="an-abrow">
          {arms.slice(0, 2).map((arm) => (
            <ArmColumn key={arm.arm_id} arm={arm} leading={arm.arm_id === leadingArmId} />
          ))}
        </div>
      )}
    </div>
  );
};

export const ExperimentsTab = ({ roleId }) => {
  const [experiments, setExperiments] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    analyticsApi.experimentsComparison(roleId ? { role_id: roleId } : {})
      .then((res) => {
        if (cancelled) return;
        setExperiments(Array.isArray(res?.data?.experiments) ? res.data.experiments : []);
      })
      .catch(() => { if (!cancelled) setExperiments([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [roleId]);

  if (loading) {
    return <div className="an-tabpanel"><div className="an-empty"><Loader2 size={14} className="animate-spin" aria-hidden="true" /> Loading experiments…</div></div>;
  }

  return (
    <div className="an-tabpanel">
      <div className="an-kicker">
        Task A/B experiments{experiments.length ? ` · ${experiments.length} ${experiments.length === 1 ? 'experiment' : 'experiments'}` : ''}
      </div>
      {experiments.length === 0 ? (
        <div className="an-abcard">
          <div className="an-empty">
            No A/B experiments {roleId ? 'for this role ' : ''}yet. Experiments compare two assessment tasks
            head-to-head to find which best predicts a strong hire — they appear here once one is running.
          </div>
        </div>
      ) : (
        experiments.map((exp) => <ExperimentCard key={exp.id} experiment={exp} />)
      )}
    </div>
  );
};

export default ExperimentsTab;
