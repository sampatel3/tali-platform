// Inline panel showing the orchestrator's open questions. Recruiters
// answer inline; the next agent cycle picks the response up and
// unblocks itself.
//
// Data flows through /api/v1/agent-needs-input (listing + answer +
// dismiss). The card hides itself entirely when there are no open
// rows, so a healthy queue doesn't show an empty container.
//
// `roleId` is optional: when set, the card scopes to one role (used by
// the role-page deeplink); when unset, it shows every open question
// across the org (the default on Home).

import React, { useCallback, useEffect, useState } from 'react';
import { ArrowUpRight, CheckCircle2, MessageSquareWarning, UserX, X } from 'lucide-react';

import api from '../../shared/api/httpClient';

const STATUS_OPEN = 'open';

const fetchOpen = (roleId) => {
  const params = { status: STATUS_OPEN };
  if (roleId != null) params.role_id = roleId;
  return api.get('/agent-needs-input', { params }).then((r) => r.data || []);
};

export default function AgentNeedsInputCard({ roleId }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);
  // Two-step arm for the destructive "Reject — no CV" action: first click
  // arms (id stored here), second click on Confirm fires the bulk reject.
  const [confirmingRejectId, setConfirmingRejectId] = useState(null);

  const reload = useCallback(() => {
    setLoading(true);
    fetchOpen(roleId)
      .then((data) => {
        setRows(Array.isArray(data) ? data : []);
        setError(null);
      })
      .catch((e) => setError(e.response?.data?.detail || e.message))
      .finally(() => setLoading(false));
  }, [roleId]);

  useEffect(() => {
    reload();
  }, [reload]);

  if (loading && rows.length === 0) return null;
  if (!loading && rows.length === 0 && !error) return null;

  const handleAnswer = async (id, value) => {
    setBusyId(id);
    try {
      await api.post(`/agent-needs-input/${id}/answer`, {
        response: { value },
      });
      reload();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusyId(null);
    }
  };

  const handleDismiss = async (id) => {
    setBusyId(id);
    try {
      await api.post(`/agent-needs-input/${id}/dismiss`);
      reload();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusyId(null);
    }
  };

  // Bulk-reject every candidate on the role that has no CV file at all.
  // Only offered on `missing_cv` cards — never on `cv_unreadable`, where a
  // CV was submitted and the text just couldn't be read.
  const handleRejectMissingCv = async (id) => {
    setBusyId(id);
    try {
      const { data } = await api.post(`/agent-needs-input/${id}/reject-missing-cv`);
      const failed = Array.isArray(data?.failed) ? data.failed : [];
      if (failed.length) {
        setError(
          `Rejected ${data.rejected}. ${failed.length} couldn't be rejected ` +
            `(Workable write-back failed) — they're left open; try again or ` +
            `reject them from the role page.`,
        );
      } else {
        setError(null);
      }
      setConfirmingRejectId(null);
      reload();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="agent-needs-input">
      <header className="agent-needs-input-head">
        <span className="agent-needs-input-icon">
          <MessageSquareWarning size={14} strokeWidth={1.8} />
        </span>
        <div>
          <b>Agent has {rows.length === 1 ? 'a question' : `${rows.length} questions`} for you</b>
          <p>Answers unblock the next agent cycle on this role.</p>
        </div>
      </header>

      {error ? <div className="agent-needs-input-error">{error}</div> : null}

      <ol className="agent-needs-input-list">
        {rows.map((row) => (
          <li key={row.id} className={`agent-needs-input-row kind-${row.kind}`}>
            <div className="agent-needs-input-prompt">
              {/* Org-wide card on Home: show the role this question belongs
                  to so the recruiter has context. Hidden when scoped to one
                  role (job page) — the role is implicit there. */}
              {!roleId && row.role_name ? (
                <div className="agent-needs-input-role">
                  Role:{' '}
                  <a
                    href={`/jobs/${row.role_id}`}
                    onClick={(e) => {
                      e.stopPropagation();
                    }}
                  >
                    {row.role_name}
                  </a>
                </div>
              ) : null}
              <p>{row.prompt}</p>
              {row.rationale ? (
                <p className="agent-needs-input-rationale">{row.rationale}</p>
              ) : null}
            </div>

            <div className="agent-needs-input-actions">
              {Array.isArray(row.options) && row.options.length > 0 ? (
                row.options.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    disabled={busyId === row.id}
                    className="agent-needs-input-option"
                    onClick={() => handleAnswer(row.id, opt.value)}
                  >
                    <CheckCircle2 size={12} />
                    {opt.label}
                  </button>
                ))
              ) : LINK_ONLY_KINDS.has(row.kind) ? (
                // Data-readiness gaps are fixed by adding the missing data
                // (via the link), not by typing an answer — so no text box.
                null
              ) : (
                <FreeTextAnswer
                  busy={busyId === row.id}
                  multiline={LONG_FORM_KINDS.has(row.kind)}
                  placeholder={
                    row.kind === 'intent_slot_missing'
                      ? 'e.g. 5+ years Python, AWS, remote-friendly, US time zones…'
                      : row.kind === 'intent_clarification'
                        ? 'Reply to the agent\'s specific question above…'
                        : 'Your answer…'
                  }
                  onSubmit={(text) => handleAnswer(row.id, text)}
                />
              )}
              {row.link_url ? (
                <a
                  className="agent-needs-input-link"
                  href={row.link_url}
                  onClick={(e) => e.stopPropagation()}
                >
                  <ArrowUpRight size={12} />
                  {row.link_label || 'Open settings'}
                </a>
              ) : null}
              {/* Reject shortcut — only for missing_cv (no file at all). The
                  cv_unreadable card deliberately has no reject: those
                  candidates did submit a CV we just couldn't read. */}
              {row.kind === 'missing_cv' ? (
                confirmingRejectId === row.id ? (
                  <>
                    <button
                      type="button"
                      className="agent-needs-input-reject confirm"
                      disabled={busyId === row.id}
                      onClick={() => handleRejectMissingCv(row.id)}
                    >
                      <UserX size={12} />
                      Confirm reject
                    </button>
                    <button
                      type="button"
                      className="agent-needs-input-reject-cancel"
                      disabled={busyId === row.id}
                      onClick={() => setConfirmingRejectId(null)}
                    >
                      Cancel
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="agent-needs-input-reject"
                    disabled={busyId === row.id}
                    onClick={() => setConfirmingRejectId(row.id)}
                    title="Reject every candidate on this role that has no CV"
                  >
                    <UserX size={12} />
                    Reject — no CV
                  </button>
                )
              ) : null}
              <button
                type="button"
                className="agent-needs-input-dismiss"
                disabled={busyId === row.id}
                onClick={() => handleDismiss(row.id)}
                title="Dismiss without answering"
              >
                <X size={12} />
                Skip
              </button>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

// Kinds whose answers are long-form prose (must-haves, intent context)
// get a textarea instead of a single-line input. Single-line still wins
// for short numeric answers (threshold, budget).
const LONG_FORM_KINDS = new Set(['intent_slot_missing', 'intent_clarification']);

// Data-readiness gaps: the recruiter resolves them by adding the missing
// data (job spec / CV) via the link, not by typing an answer — so we render
// just the link + Skip, no free-text box.
const LINK_ONLY_KINDS = new Set(['missing_job_spec', 'missing_cv', 'cv_unreadable']);

function FreeTextAnswer({ busy, onSubmit, multiline = false, placeholder = 'Your answer…' }) {
  const [text, setText] = useState('');
  const submit = (e) => {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
  };
  return (
    <form className="agent-needs-input-freeform" onSubmit={submit}>
      {multiline ? (
        <textarea
          rows={3}
          placeholder={placeholder}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy}
        />
      ) : (
        <input
          type="text"
          placeholder={placeholder}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy}
        />
      )}
      <button type="submit" disabled={busy || !text.trim()}>
        Send
      </button>
    </form>
  );
}
