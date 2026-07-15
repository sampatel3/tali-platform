import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { roles } from '../../shared/api';
import { Spinner } from '../../shared/ui/TaaliPrimitives';

const formatTimestamp = (value) => {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
};

const buildAuthorLabel = (note) => {
  if (note?.author_name) return note.author_name;
  if (note?.author_user_id) return `User #${note.author_user_id}`;
  return 'Recruiter';
};

export default function RoleFeedbackNotes({
  roleId,
  roleVersion,
  onRoleVersionChange,
  onRoleConflict,
  readOnly = false,
  readOnlyReason = null,
}) {
  const [notes, setNotes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');

  const refresh = useCallback(async () => {
    if (!roleId) return;
    setLoading(true);
    setLoadError('');
    try {
      const resp = await roles.listFeedbackNotes(roleId);
      setNotes(Array.isArray(resp?.data) ? resp.data : []);
    } catch {
      setLoadError('Couldn\'t load feedback notes — try again.');
    } finally {
      setLoading(false);
    }
  }, [roleId]);

  useEffect(() => { refresh(); }, [refresh]);

  const trimmedDraft = draft.trim();
  const canSubmit = !readOnly && trimmedDraft.length > 0 && !saving;

  const submit = useCallback(async () => {
    if (readOnly || !trimmedDraft || saving) return;
    setSaving(true);
    setSaveError('');
    try {
      const resp = await roles.createFeedbackNote(roleId, trimmedDraft, roleVersion);
      const created = resp?.data;
      if (created) {
        setNotes((prev) => [created, ...prev]);
        if (created.role_version != null) {
          onRoleVersionChange?.(created.role_version);
        }
      } else {
        await refresh();
      }
      setDraft('');
    } catch (error) {
      const detail = error?.response?.data?.detail;
      if (error?.response?.status === 409 && detail?.code === 'ROLE_VERSION_CONFLICT') {
        setSaveError(detail.message || 'This job changed. Review the latest version and try again.');
        await onRoleConflict?.();
      } else {
        setSaveError('Couldn\'t save your feedback — try again.');
      }
    } finally {
      setSaving(false);
    }
  }, [onRoleConflict, onRoleVersionChange, readOnly, refresh, roleId, roleVersion, saving, trimmedDraft]);

  const onKeyDown = (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      submit();
    }
  };

  const orderedNotes = useMemo(() => {
    return [...notes].sort((a, b) => {
      const aTs = new Date(a?.created_at || 0).getTime();
      const bTs = new Date(b?.created_at || 0).getTime();
      return bTs - aTs;
    });
  }, [notes]);

  return (
    <section className="mc-agent-settings-card" data-testid="role-feedback-notes" title={readOnly ? readOnlyReason : undefined}>
      <div className="mc-agent-settings-card-head">
        <div>
          <h2 className="mc-agent-settings-card-title">
            Feedback to the <em>agent</em>
          </h2>
          <p className="mc-agent-settings-card-help">
            Spot a pattern across candidates? Tell the agent here. The most recent notes are added to the agent's brief on the next cycle — alongside your role criteria. Everything you write is kept in the log below for your records.
          </p>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="e.g. The agent keeps over-weighting recent SaaS experience — we care more about consumer product instincts for this role."
          rows={3}
          maxLength={4000}
          disabled={readOnly || saving}
          style={{
            width: '100%',
            padding: '10px 12px',
            borderRadius: 10,
            border: '1px solid var(--line)',
            background: 'var(--bg-2)',
            color: 'var(--ink)',
            fontFamily: 'inherit',
            fontSize: 14,
            lineHeight: 1.5,
            resize: 'vertical',
            minHeight: 80,
          }}
        />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <span style={{ fontSize: 12, color: 'var(--mute)' }}>
            {saveError
              ? <span style={{ color: 'var(--purple)' }}>{saveError}</span>
              : <>Tip: be specific. <kbd>⌘</kbd>/<kbd>Ctrl</kbd>+<kbd>Enter</kbd> to send.</>}
          </span>
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            className="btn btn-primary"
            style={{
              padding: '8px 14px',
              borderRadius: 10,
              fontSize: 13,
              fontWeight: 600,
              background: canSubmit ? 'var(--purple)' : 'var(--bg-3)',
              color: canSubmit ? '#fff' : 'var(--mute)',
              border: 'none',
              cursor: canSubmit ? 'pointer' : 'not-allowed',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            {saving ? <Spinner size={14} className="!text-current" /> : null}
            {saving ? 'Saving…' : 'Add feedback'}
          </button>
        </div>
      </div>

      <div style={{ marginTop: 20, borderTop: '1px solid var(--line)', paddingTop: 16 }}>
        <div className="kicker mute" style={{ marginBottom: 10 }}>
          FEEDBACK LOG
        </div>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--mute)', fontSize: 13 }}>
            <Spinner size={14} className="!text-current" /> Loading…
          </div>
        ) : loadError ? (
          <div style={{ color: 'var(--purple)', fontSize: 13 }}>{loadError}</div>
        ) : orderedNotes.length === 0 ? (
          <div style={{ color: 'var(--mute)', fontSize: 13 }}>
            No feedback yet. Notes you add appear here.
          </div>
        ) : (
          <ol style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {orderedNotes.map((note) => (
              <li
                key={note.id}
                style={{
                  background: 'var(--bg-2)',
                  border: '1px solid var(--line)',
                  borderRadius: 12,
                  padding: '12px 14px',
                }}
              >
                <div style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'baseline',
                  gap: 12,
                  marginBottom: 6,
                  fontSize: 12,
                  color: 'var(--mute)',
                }}>
                  <strong style={{ color: 'var(--ink-2)', fontWeight: 600 }}>
                    {buildAuthorLabel(note)}
                  </strong>
                  <span>{formatTimestamp(note.created_at)}</span>
                </div>
                <div style={{ fontSize: 14, color: 'var(--ink)', lineHeight: 1.55, whiteSpace: 'pre-wrap' }}>
                  {note.note}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
