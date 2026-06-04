import React, { useCallback, useEffect, useState } from 'react';
import { ArrowRight, Sparkles } from 'lucide-react';

import { tasks as tasksApi } from '../../shared/api';

/**
 * Pointer to the generated-draft review, which now lives in the agent chat.
 *
 * The JD→spec generator authors assessment tasks as is_active=false drafts
 * (extra_data.generated). Reviewing them — approve, or reject-with-structured-
 * feedback so the agent re-authors rather than deletes — happens in each
 * role's agent chat on Home (the `list_draft_tasks` / draft_task_review card).
 * This panel is just the at-a-glance cue on the Tasks page + a jump-in link;
 * it intentionally no longer owns the approve/reject controls.
 */
export const GeneratedDraftsPanel = ({ onNavigate }) => {
  const [drafts, setDrafts] = useState([]);
  const [loading, setLoading] = useState(true);

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

  if (loading || drafts.length === 0) return null; // silent until there's something to review

  return (
    <div
      className="mb-5 rounded-[16px] border p-4"
      style={{
        borderColor: 'color-mix(in oklab, var(--purple) 28%, var(--line))',
        background: 'var(--purple-soft)',
      }}
    >
      <div className="flex items-center gap-3">
        <Sparkles size={16} className="shrink-0 text-[var(--purple)]" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-[var(--ink)]">
            {drafts.length} generated task {drafts.length === 1 ? 'draft' : 'drafts'} awaiting review
          </div>
          <div className="text-xs text-[var(--mute)]">
            Auto-authored from the role JD. Review, approve, or reject-with-feedback with each role’s agent.
          </div>
        </div>
        <button
          type="button"
          onClick={() => onNavigate?.('home')}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-[var(--purple)] px-3.5 py-2 text-[12px] font-semibold text-white transition-colors hover:bg-[var(--purple-2)]"
        >
          Review with the agent <ArrowRight size={13} />
        </button>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {drafts.slice(0, 6).map((t) => (
          <span
            key={t.id}
            className="rounded-full border border-[var(--line)] bg-[var(--bg)] px-3 py-1 text-[11.5px] text-[var(--ink-2)]"
          >
            {t.name}
          </span>
        ))}
        {drafts.length > 6 && (
          <span className="text-[11.5px] text-[var(--mute)]">+{drafts.length - 6} more</span>
        )}
      </div>
    </div>
  );
};

export default GeneratedDraftsPanel;
