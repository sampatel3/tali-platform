// A/B — assessment-task experiment comparison. One column per arm; rows grouped
// into the three win-signal families (discrimination, completion+time,
// downstream outcome, candidate experience). Pilot-honest: every rate shows its
// denominator and no winner is declared while any arm is below the sample gate.

import React, { useEffect, useMemo, useState } from 'react';

import { analytics as analyticsApi } from '../../shared/api';
import { Select } from '../../shared/ui/TaaliPrimitives';

const fmtPct = (rate) => (rate == null ? '—' : `${Math.round(rate * 100)}%`);
const fmtRate = (count, rate, denom) =>
  rate == null ? '—' : `${count}/${denom} (${Math.round(rate * 100)}%)`;
const fmtNum = (v, d = '—') => (v == null || Number.isNaN(v) ? d : v);
const fmtDur = (seconds) =>
  seconds == null ? '—' : `${Math.round(Number(seconds) / 60)}m`;

const fmtScore = (s) => {
  if (!s || s.median == null) return '—';
  return `${s.median} (${s.p25}–${s.p75})`;
};

// Inline purple spread bar showing the IQR band on a 0–100 score scale.
const SpreadBar = ({ score }) => {
  if (!score || score.p25 == null || score.p75 == null) return null;
  const left = Math.max(0, Math.min(100, score.p25));
  const width = Math.max(2, Math.min(100, score.p75) - left);
  return (
    <div
      style={{
        position: 'relative', height: 6, marginTop: 4, borderRadius: 999,
        background: 'color-mix(in oklab, var(--purple) 12%, var(--bg-2))',
      }}
    >
      <div
        style={{
          position: 'absolute', top: 0, bottom: 0, left: `${left}%`, width: `${width}%`,
          background: 'var(--purple)', borderRadius: 999,
        }}
      />
    </div>
  );
};

const SectionRow = ({ label }) => (
  <tr>
    <td
      colSpan={99}
      style={{
        paddingTop: 14, paddingBottom: 4, fontSize: 'var(--fs-body-lg)', letterSpacing: '0.06em',
        textTransform: 'uppercase', color: 'var(--purple)', fontWeight: 600,
      }}
    >
      {label}
    </td>
  </tr>
);

const MetricRow = ({ label, values }) => (
  <tr>
    <td style={{ padding: '4px 8px', color: 'var(--mute)', whiteSpace: 'nowrap' }}>{label}</td>
    {values.map((v, i) => (
      <td key={i} style={{ padding: '4px 12px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
        {v}
      </td>
    ))}
  </tr>
);

export const HomeExperiments = ({ roleId, dateFrom }) => {
  const [experiments, setExperiments] = useState([]);
  const [experimentId, setExperimentId] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  // Load the list of experiments (optionally scoped by role).
  useEffect(() => {
    let cancelled = false;
    analyticsApi
      .experimentsComparison({ ...(roleId ? { role_id: roleId } : {}) })
      .then((res) => {
        if (cancelled) return;
        const list = Array.isArray(res?.data?.experiments) ? res.data.experiments : [];
        setExperiments(list);
        setExperimentId((prev) => (list.some((e) => String(e.id) === String(prev)) ? prev : (list[0]?.id ?? '')));
      })
      .catch(() => {
        if (!cancelled) { setExperiments([]); setExperimentId(''); }
      });
    return () => { cancelled = true; };
  }, [roleId]);

  // Load the comparison for the selected experiment.
  useEffect(() => {
    if (!experimentId) { setData(null); return undefined; }
    let cancelled = false;
    setLoading(true);
    analyticsApi
      .experimentsComparison({
        experiment_id: experimentId,
        ...(dateFrom ? { date_from: dateFrom } : {}),
      })
      .then((res) => { if (!cancelled) setData(res?.data || null); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [experimentId, dateFrom]);

  const arms = useMemo(() => (Array.isArray(data?.arms) ? data.arms : []), [data]);
  const anySmall = arms.length === 0 || arms.some((a) => a.small_sample);

  if (experiments.length === 0) {
    return (
      <div className="home-empty">
        No A/B experiments configured{roleId ? ' for this role' : ''} yet.
      </div>
    );
  }

  return (
    <div className="hm-tabpanel">
      <div className="hm-controls">
        <label className="hm-rolefilter">
          <span className="kicker">Experiment</span>
          <Select inline value={experimentId} onChange={(e) => setExperimentId(e.target.value)}>
            {experiments.map((e) => (
              <option key={e.id} value={e.id}>{e.name} · {e.status}</option>
            ))}
          </Select>
        </label>
      </div>

      {anySmall ? (
        <div
          style={{
            margin: '8px 0', padding: '8px 12px', borderRadius: 8, fontSize: 'var(--fs-subtitle)',
            color: 'var(--ink)',
            background: 'color-mix(in oklab, var(--purple) 7%, var(--bg-2))',
            border: '1px solid color-mix(in oklab, var(--purple) 28%, var(--line))',
          }}
        >
          {data?.guidance || 'Pilot — sample too small to call a winner.'} No winner is auto-declared; compare n per arm.
        </div>
      ) : null}

      {data?.cohort_drift ? (
        <div style={{ margin: '4px 0 8px', fontSize: 'var(--fs-subtitle)', color: 'var(--mute)' }}>
          ⚠ Arm assignment counts are materially unequal — interpret rates with care.
        </div>
      ) : null}

      {loading ? (
        <div className="home-empty">Loading…</div>
      ) : arms.length === 0 ? (
        <div className="home-empty">No assignments recorded for this experiment yet.</div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--fs-subtitle)' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '4px 8px' }} />
              {arms.map((a) => (
                <th key={a.arm_id} style={{ textAlign: 'right', padding: '4px 12px' }}>
                  <div style={{ fontWeight: 600 }}>{a.arm_key}</div>
                  <div style={{ fontSize: 'var(--fs-body-lg)', color: 'var(--mute)', fontWeight: 400 }}>{a.task_name || `task ${a.task_id}`}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <MetricRow label="Assigned / started / completed" values={arms.map((a) => `${a.n_assigned} / ${a.n_started} / ${a.n_completed}`)} />

            <SectionRow label="Discrimination" />
            <MetricRow label="Score · median (p25–p75)" values={arms.map((a) => fmtScore(a.discrimination?.score))} />
            <tr>
              <td style={{ padding: '0 8px', color: 'var(--mute)' }}>Spread (IQR)</td>
              {arms.map((a) => (
                <td key={a.arm_id} style={{ padding: '4px 12px' }}>
                  <div style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtNum(a.discrimination?.spread_iqr)}</div>
                  <SpreadBar score={a.discrimination?.score} />
                </td>
              ))}
            </tr>

            <SectionRow label="Completion & time" />
            <MetricRow label="Never started" values={arms.map((a) => fmtRate(a.completion?.never_started, a.completion?.never_started_rate, a.n_assigned))} />
            <MetricRow label="Abandoned" values={arms.map((a) => fmtRate(a.completion?.abandoned, a.completion?.abandonment_rate, a.n_assigned))} />
            <MetricRow label="Timed out" values={arms.map((a) => fmtRate(a.completion?.timed_out, a.completion?.timeout_rate, a.n_completed))} />
            <MetricRow label="Time to complete (median)" values={arms.map((a) => fmtDur(a.completion?.time_to_complete_seconds?.median))} />

            <SectionRow label="Downstream outcome" />
            <MetricRow label="Advanced" values={arms.map((a) => fmtRate(a.outcome?.advanced, a.outcome?.advanced_rate, a.outcome?.n_with_application))} />
            <MetricRow label="Hired" values={arms.map((a) => fmtRate(a.outcome?.hired, a.outcome?.hired_rate, a.outcome?.n_with_application))} />
            <MetricRow label="Rejected" values={arms.map((a) => fmtRate(a.outcome?.rejected, a.outcome?.rejected_rate, a.outcome?.n_with_application))} />

            <SectionRow label="Candidate experience" />
            <MetricRow label="Instructions drop-off" values={arms.map((a) => fmtPct(a.experience?.instructions_dropoff_rate))} />
            <MetricRow label="Avg tab switches" values={arms.map((a) => fmtNum(a.experience?.avg_tab_switches))} />
            <MetricRow label="Avg focus ratio" values={arms.map((a) => fmtNum(a.experience?.avg_browser_focus_ratio))} />
            <MetricRow label="Avg time to first prompt" values={arms.map((a) => fmtDur(a.experience?.avg_time_to_first_prompt_seconds))} />
            <MetricRow label="Feedback generated" values={arms.map((a) => fmtNum(a.experience?.has_feedback_count))} />
          </tbody>
        </table>
      )}
    </div>
  );
};

export default HomeExperiments;
