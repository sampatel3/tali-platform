import { useState } from 'react';

// Assessment stage stepper. Renders the task's `two_stage` config (Part 1
// Practice & Setup → Part 2 Applied Task) or, for normal tasks, the caller's
// default orientation path. Presentational: it frames the candidate's time and
// lets them advance the highlighted step. It does NOT lock the workspace, so
// it can never break the runtime; it renders nothing below two parts.
export function AssessmentStagePanel({ twoStage }) {
  const parts = Array.isArray(twoStage?.parts) ? twoStage.parts : [];
  const [active, setActive] = useState(0);
  if (parts.length < 2) return null;

  const current = parts[Math.min(active, parts.length - 1)];
  const next = parts[active + 1];

  return (
    <div
      className="mb-4 rounded-xl border p-4"
      style={{
        borderColor: 'var(--taali-runtime-border, rgba(127,127,127,0.25))',
        background: 'var(--taali-runtime-surface, rgba(127,127,127,0.06))',
      }}
    >
      <div className="mb-3 flex flex-wrap items-stretch gap-2">
        {parts.map((p, i) => {
          const isActive = i === active;
          return (
            <button
              key={i}
              type="button"
              onClick={() => setActive(i)}
              className={`taali-btn taali-btn-sm flex flex-1 items-center gap-2.5 justify-start text-left ${
                isActive ? 'taali-btn-soft' : 'taali-btn-secondary'
              }`}
              style={{ minWidth: 200 }}
            >
              <span
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
                style={{
                  background: isActive ? 'var(--purple, #7c5cff)' : 'var(--taali-runtime-border, rgba(127,127,127,0.3))',
                  color: isActive ? '#fff' : 'inherit',
                }}
              >
                {i + 1}
              </span>
              <span className="flex flex-col">
                <span className="text-[0.8125rem] font-semibold leading-tight">{p.title}</span>
                {p.minutes ? (
                  <span className="text-[0.6875rem] opacity-60">~{p.minutes} min</span>
                ) : null}
              </span>
            </button>
          );
        })}
      </div>

      {current?.blurb ? (
        <p className="text-[0.875rem] leading-6 opacity-90">{current.blurb}</p>
      ) : null}

      {next ? (
        <button
          type="button"
          onClick={() => setActive(active + 1)}
          className="taali-btn taali-btn-primary taali-btn-sm mt-3"
        >
          Done — start {next.title} <span aria-hidden>→</span>
        </button>
      ) : null}

      {twoStage?.note ? (
        <p className="mt-3 text-[0.75rem] opacity-60">{twoStage.note}</p>
      ) : null}
    </div>
  );
}

export default AssessmentStagePanel;
