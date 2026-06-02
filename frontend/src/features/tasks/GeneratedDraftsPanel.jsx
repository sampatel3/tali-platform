import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Bot, Check, ChevronDown, ChevronUp, Sparkles, X } from 'lucide-react';

import { useToast } from '../../context/ToastContext';
import { tasks as tasksApi } from '../../shared/api';

const getErr = (e, f) => e?.response?.data?.detail || e?.message || f;

const lensColor = (lens) =>
  lens === 'deliverable'
    ? 'text-[var(--workable)]'
    : 'text-[var(--purple)]'; // decision / interrogation

/**
 * Review surface for auto-generated assessment-task drafts.
 *
 * The JD→spec generator authors tasks as is_active=false drafts
 * (extra_data.generated). This panel lists them, lets the recruiter
 * inspect the generated spec (scenario, decision_points, lens rubric,
 * repo), and approve (activate) or reject (delete) each.
 *
 * onChange() is called after any approve/reject so the parent can
 * refresh the live task catalogue.
 */
export const GeneratedDraftsPanel = ({ onChange }) => {
  const { showToast } = useToast();
  const [drafts, setDrafts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await tasksApi.drafts();
      setDrafts(Array.isArray(res?.data) ? res.data : []);
    } catch (e) {
      setDrafts([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const approve = useCallback(async (id) => {
    setBusyId(id);
    try {
      await tasksApi.approve(id);
      showToast('Task approved — it is now live and assignable.', 'success');
      await load();
      onChange?.();
    } catch (e) {
      showToast(getErr(e, 'Failed to approve task.'), 'error');
    } finally {
      setBusyId(null);
    }
  }, [load, onChange, showToast]);

  const reject = useCallback(async (id) => {
    setBusyId(id);
    try {
      await tasksApi.reject(id);
      showToast('Draft rejected.', 'success');
      await load();
      onChange?.();
    } catch (e) {
      showToast(getErr(e, 'Failed to reject draft.'), 'error');
    } finally {
      setBusyId(null);
    }
  }, [load, onChange, showToast]);

  if (loading || drafts.length === 0) return null; // silent until there's something to review

  return (
    <div
      className="mb-5 rounded-[16px] border p-4"
      style={{
        borderColor: 'color-mix(in oklab, var(--purple) 28%, var(--line))',
        background: 'var(--purple-soft)',
      }}
    >
      <div className="mb-3 flex items-center gap-2">
        <Sparkles size={16} className="text-[var(--purple)]" />
        <div className="text-sm font-semibold text-[var(--ink)]">
          {drafts.length} generated {drafts.length === 1 ? 'task' : 'tasks'} awaiting review
        </div>
        <div className="text-xs text-[var(--mute)]">
          Auto-authored from the role JD. Review, then approve to make it assignable.
        </div>
      </div>

      <div className="space-y-2">
        {drafts.map((t) => (
          <DraftRow
            key={t.id}
            task={t}
            open={expanded === t.id}
            busy={busyId === t.id}
            onToggle={() => setExpanded(expanded === t.id ? null : t.id)}
            onApprove={() => approve(t.id)}
            onReject={() => reject(t.id)}
          />
        ))}
      </div>
    </div>
  );
};

const DraftRow = ({ task, open, busy, onToggle, onApprove, onReject }) => {
  const extra = task?.extra_data || {};
  const decisionPoints = Array.isArray(extra.decision_points) ? extra.decision_points : [];
  const deliverable = extra.deliverable || {};
  const rubric = task?.evaluation_rubric || {};
  const repoFiles = useMemo(() => {
    const files = task?.repo_structure?.files || {};
    return Object.keys(files);
  }, [task]);

  const decisionW = useMemo(
    () => Object.values(rubric).reduce((s, v) => s + (v?.lens === 'decision' || v?.grader === 'interrogation_outcome' ? Number(v.weight) || 0 : 0), 0),
    [rubric],
  );
  const deliverableW = useMemo(
    () => Object.values(rubric).reduce((s, v) => s + (v?.lens === 'deliverable' ? Number(v.weight) || 0 : 0), 0),
    [rubric],
  );

  return (
    <div className="rounded-[12px] border border-[var(--line)] bg-[var(--bg)]">
      <div className="flex items-center gap-3 px-3.5 py-2.5">
        <Bot size={15} className="shrink-0 text-[var(--purple)]" />
        <button type="button" onClick={onToggle} className="min-w-0 flex-1 text-left">
          <div className="truncate text-[13.5px] font-medium text-[var(--ink)]">{task.name}</div>
          <div className="truncate font-mono text-[10.5px] uppercase tracking-[0.06em] text-[var(--mute)]">
            {task.role} · {(deliverable.kind || 'code')} · decision {decisionW.toFixed(2)} / deliverable {deliverableW.toFixed(2)}
          </div>
        </button>
        <button
          type="button"
          onClick={onApprove}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-full bg-[var(--purple)] px-3 py-1.5 text-[12px] font-semibold text-white transition-colors hover:bg-[var(--purple-2)] disabled:opacity-50"
        >
          <Check size={13} /> Approve
        </button>
        <button
          type="button"
          onClick={onReject}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-3 py-1.5 text-[12px] font-medium text-[var(--mute)] transition-colors hover:border-[var(--taali-danger)] hover:text-[var(--taali-danger)] disabled:opacity-50"
        >
          <X size={13} /> Reject
        </button>
        <button type="button" onClick={onToggle} className="text-[var(--mute)] hover:text-[var(--ink)]">
          {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
      </div>

      {open ? (
        <div className="border-t border-[var(--line)] px-3.5 py-3 text-[13px] leading-6 text-[var(--ink-2)]">
          <div className="mb-3 whitespace-pre-line text-[12.5px] text-[var(--mute)]">
            {String(task.scenario || '').slice(0, 600)}
            {String(task.scenario || '').length > 600 ? '…' : ''}
          </div>

          <div className="mb-3">
            <div className="mb-1 font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--purple)]">Decisions ({decisionPoints.length})</div>
            <ul className="list-disc space-y-0.5 pl-5">
              {decisionPoints.map((dp) => (
                <li key={dp.id}><span className="font-medium text-[var(--ink)]">{dp.headline}</span> {dp.tension}</li>
              ))}
            </ul>
          </div>

          <div className="mb-3">
            <div className="mb-1 font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--purple)]">Rubric (lens)</div>
            <ul className="space-y-0.5">
              {Object.entries(rubric).map(([k, v]) => (
                <li key={k} className="flex items-center gap-2">
                  <span className={`font-mono text-[10px] uppercase ${lensColor(v?.lens || (v?.grader === 'interrogation_outcome' ? 'decision' : ''))}`}>
                    {v?.grader === 'interrogation_outcome' ? 'decision*' : (v?.lens || '—')}
                  </span>
                  <span className="text-[var(--ink)]">{k}</span>
                  <span className="text-[var(--mute)]">{v?.weight}</span>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <div className="mb-1 font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--purple)]">Repo ({repoFiles.length} files)</div>
            <div className="font-mono text-[11px] text-[var(--mute)]">{repoFiles.join('  ·  ')}</div>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default GeneratedDraftsPanel;
