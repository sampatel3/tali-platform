// Public, no-login CLIENT INTAKE — a consultancy's client describes the role
// they're hiring for, talking to the SAME conversational agent the recruiter
// uses, with all company/economics fields hidden.
//
// Reached via /intake/:token. Like the public job page (/job/:token) and the
// candidate assessment (/assess/:token) it renders WITHOUT a NavComponent and
// WITHOUT a recruiter session — every call goes through publicIntakeApi, which
// uses a bare, JWT-free axios so the link works for anyone.
//
// Layout: a branded header, the conversation (reusing the shared chat kit +
// the SAME message / quick-reply / attachment rendering as RequisitionsPage),
// a compact "Role so far" panel built from `captured`, a completeness bar, and
// a "Submit to {org}" button that flips the page to a clean done state.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, FileText, Paperclip, Send, X } from 'lucide-react';

import { ChatComposer, ChatMarkdown, ChatMessage, ThinkingDots } from '../../shared/chat';
import { publicIntakeApi } from '../requisitions/api';
import './clientintake.css';

const ACCEPT = '.txt,.vtt,.srt,.md,.pdf,image/*';
const isImage = (file) => Boolean(file && (file.type || '').startsWith('image/'));

// One staged attachment = the File + a stable id + (for images) an object URL
// for the thumbnail preview. We revoke the URL when the chip is removed / sent.
// Mirrors RequisitionsPage's staging exactly.
let attachSeq = 0;
const stageFile = (file) => ({
  id: `att_${Date.now()}_${attachSeq++}`,
  file,
  url: isImage(file) ? URL.createObjectURL(file) : null,
});

// Humanise a captured field key (e.g. `must_haves` → "Must haves",
// `target_start` → "Target start") for the read-out panel.
const humanizeKey = (key) => String(key || '')
  .replace(/_/g, ' ')
  .replace(/\b\w/g, (c) => c.toUpperCase())
  .trim();

const isEmptyValue = (v) => (
  v == null
  || v === ''
  || (Array.isArray(v) && v.length === 0)
  || (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0)
);

// Render a single struct row (e.g. `{ factor, weight }` / `{ kind, description }`)
// as readable text — same shapes the recruiter brief uses.
const formatStructRow = (row) => {
  if (row == null) return '';
  if (typeof row === 'string') return row;
  if (row.label != null) return `${row.label}${row.detail != null && row.detail !== '' ? `: ${row.detail}` : ''}`;
  if (row.factor != null) return `${row.factor}${row.weight != null ? ` (${row.weight})` : ''}`;
  if (row.kind != null) return `${row.kind}${row.description != null ? `: ${row.description}` : ''}`;
  const vals = Object.values(row).filter((v) => v != null && v !== '');
  return vals.slice(0, 2).join(': ');
};

// One conversation turn rendered with the shared message bubbles — IDENTICAL
// to RequisitionsPage's Turn: assistant turns render Markdown; user turns show
// their text plus any attachment chips.
function Turn({ msg }) {
  const attachments = Array.isArray(msg.attachments) ? msg.attachments : [];
  if (msg.role === 'user') {
    return (
      <div className="tk-msg-user-wrap">
        <div className="tk-msg-user">
          {msg.content}
          {attachments.length > 0 ? (
            <div className="ci-attach-row" style={{ marginTop: msg.content ? 8 : 0, marginBottom: 0 }}>
              {attachments.map((a, i) => (
                <span key={i} className="ci-attach-chip" style={{ background: 'rgba(255,255,255,0.12)', borderColor: 'rgba(255,255,255,0.2)', color: '#fff' }}>
                  <span className="ci-attach-glyph"><FileText size={13} /></span>
                  <span className="ci-attach-name">{a.name}</span>
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    );
  }
  return (
    <ChatMessage role="assistant">
      <ChatMarkdown>{msg.content}</ChatMarkdown>
    </ChatMessage>
  );
}

// The compact "Role so far" panel — the captured ROLE fields (no economics,
// no client internals; the backend already scrubs those) + a completeness bar.
function RolePanel({ captured, gaps, completeness }) {
  const entries = useMemo(() => (
    Object.entries(captured || {}).filter(([, v]) => !isEmptyValue(v))
  ), [captured]);
  const pct = Math.max(0, Math.min(100, Number(completeness) || 0));
  const gapList = Array.isArray(gaps) ? gaps : [];

  return (
    <aside className="ci-panel">
      <div className="ci-panel-scroll">
        <div className="ci-meter">
          <div className="ci-meter-top">
            <span className="ci-meter-label">Role so far</span>
            <span className="ci-meter-pct">{pct}%</span>
          </div>
          <div className="ci-meter-track">
            <div className="ci-meter-fill" style={{ width: `${pct}%` }} />
          </div>
          {/* Message tracks the BAR, not just required gaps — so it never says
              "complete" while the bar is low. Keeps the manager adding depth
              (tech stack, projects, challenges) until the role's well covered. */}
          {pct >= 85 ? (
            <div className="ci-gaps-line">Looking complete — review and submit when you're ready.</div>
          ) : gapList.length > 0 ? (
            <div className="ci-gaps-line">
              <strong>{gapList.length}</strong> thing{gapList.length === 1 ? '' : 's'} still to cover —
              {' '}the assistant will ask about {gapList.length === 1 ? 'it' : 'them'}.
            </div>
          ) : entries.length > 0 ? (
            <div className="ci-gaps-line">Good start — keep adding detail (tech stack, projects, challenges) so we capture the full picture.</div>
          ) : (
            <div className="ci-gaps-line">Start describing the role and this fills in as you go.</div>
          )}
        </div>

        {entries.length === 0 ? (
          <p className="ci-panel-empty">Nothing captured yet.</p>
        ) : (
          <dl className="ci-fields">
            {entries.map(([key, value]) => (
              <div key={key} className="ci-field">
                <dt className="ci-field-label">{humanizeKey(key)}</dt>
                <dd className="ci-field-value">
                  {Array.isArray(value) ? (
                    <div className="ci-chips">
                      {value.map((it, i) => (
                        <span key={i} className="ci-chip">
                          {typeof it === 'string' ? it : formatStructRow(it)}
                        </span>
                      ))}
                    </div>
                  ) : (
                    String(value)
                  )}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </aside>
  );
}

export function ClientIntakePage() {
  // Read the token straight off the path so the page works without the router
  // params hook plumbing (mirrors how the page is mounted, /intake/:token).
  const token = useMemo(() => {
    if (typeof window === 'undefined') return '';
    const m = window.location.pathname.match(/^\/intake\/(.+)$/);
    return m ? decodeURIComponent(m[1]) : '';
  }, []);

  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [orgName, setOrgName] = useState('');
  const [messages, setMessages] = useState([]);
  const [captured, setCaptured] = useState({});
  const [gaps, setGaps] = useState([]);
  const [completeness, setCompleteness] = useState(0);
  const [status, setStatus] = useState('');
  const [suggested, setSuggested] = useState([]);

  const [composer, setComposer] = useState('');
  const [attachments, setAttachments] = useState([]);
  const [turnInFlight, setTurnInFlight] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  const fileInputRef = useRef(null);
  const threadEndRef = useRef(null);
  const attachmentsRef = useRef(attachments);
  attachmentsRef.current = attachments;

  // Adopt a server payload (initial load OR a chat response) into local state.
  const adopt = useCallback((data) => {
    if (!data) return;
    if (Array.isArray(data.messages)) setMessages(data.messages);
    if (data.captured && typeof data.captured === 'object') setCaptured(data.captured);
    if (Array.isArray(data.gaps)) setGaps(data.gaps);
    if (data.completeness != null) setCompleteness(data.completeness);
    if (data.status != null) setStatus(data.status);
    // suggested_replies live on the chat response; the GET snapshot carries
    // them on the last message instead — handle both.
    if (Array.isArray(data.suggested_replies)) {
      setSuggested(data.suggested_replies);
    }
  }, []);

  // Initial snapshot. A 404 (revoked / bad token) reads the same to a visitor.
  useEffect(() => {
    let alive = true;
    if (!token) {
      setLoading(false);
      setNotFound(true);
      return undefined;
    }
    setLoading(true);
    publicIntakeApi
      .get(token)
      .then((data) => {
        if (!alive) return;
        setOrgName(data?.organization_name || '');
        adopt(data);
        // If the brief was already submitted, land on the done state.
        if (String(data?.status || '').toLowerCase() === 'submitted') setSubmitted(true);
        setLoading(false);
      })
      .catch(() => {
        if (!alive) return;
        setNotFound(true);
        setLoading(false);
      });
    return () => { alive = false; };
  }, [token, adopt]);

  // Revoke any staged object URLs on unmount.
  useEffect(() => () => {
    attachmentsRef.current.forEach((a) => a.url && URL.revokeObjectURL(a.url));
  }, []);

  // Keep the thread pinned to the latest turn.
  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, turnInFlight]);

  const clearAttachments = useCallback(() => {
    attachmentsRef.current.forEach((a) => a.url && URL.revokeObjectURL(a.url));
    setAttachments([]);
  }, []);

  // ---- attachments (mirrors RequisitionsPage) ----
  const addFiles = useCallback((files) => {
    const staged = Array.from(files || []).filter(Boolean).map(stageFile);
    if (staged.length) setAttachments((prev) => [...prev, ...staged]);
  }, []);

  const onFilePick = useCallback((e) => {
    addFiles(e.target.files);
    e.target.value = ''; // allow re-picking the same file
  }, [addFiles]);

  const removeAttachment = useCallback((id) => {
    setAttachments((prev) => {
      const found = prev.find((a) => a.id === id);
      if (found?.url) URL.revokeObjectURL(found.url);
      return prev.filter((a) => a.id !== id);
    });
  }, []);

  // Paste-to-attach: pull image blobs off the clipboard onto the composer.
  const onPaste = useCallback((e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const imgs = [];
    for (const item of items) {
      if (item.kind === 'file' && (item.type || '').startsWith('image/')) {
        const f = item.getAsFile();
        if (f) imgs.push(f);
      }
    }
    if (imgs.length) {
      e.preventDefault();
      addFiles(imgs);
    }
  }, [addFiles]);

  // ---- send a turn ----
  const runTurn = useCallback(async (message, files, echoAttachments) => {
    if (turnInFlight || submitting || submitted) return;
    if (!message && (!files || files.length === 0)) return;

    setTurnInFlight(true);
    setError('');
    // Clear stale quick replies the moment we send.
    setSuggested([]);

    // Optimistic echo so the client's turn shows immediately.
    setMessages((prev) => [...prev, { role: 'user', content: message, attachments: echoAttachments || [] }]);

    try {
      const res = await publicIntakeApi.chat(token, { message, files: files || [] });
      adopt(res);
    } catch {
      setError('The assistant could not process that — your message is preserved above, try again.');
    } finally {
      setTurnInFlight(false);
    }
  }, [token, turnInFlight, submitting, submitted, adopt]);

  const sendTurn = useCallback(() => {
    const message = composer.trim();
    const files = attachments.map((a) => a.file);
    if (!message && files.length === 0) return;
    const echoAttachments = attachments.map((a) => ({ name: a.file.name, kind: isImage(a.file) ? 'image' : 'file' }));
    setComposer('');
    clearAttachments();
    void runTurn(message, files, echoAttachments);
  }, [composer, attachments, clearAttachments, runTurn]);

  const onComposerSubmit = useCallback(() => { void sendTurn(); }, [sendTurn]);

  const sendQuickReply = useCallback((text) => {
    const t = String(text || '').trim();
    if (t) void runTurn(t, [], []);
  }, [runTurn]);

  // ---- submit ----
  const submit = useCallback(async () => {
    if (submitting || submitted) return;
    setSubmitting(true);
    setError('');
    try {
      const res = await publicIntakeApi.submit(token);
      if (res?.status != null) setStatus(res.status);
      setSubmitted(true);
    } catch {
      setError('Could not submit just yet — please try again.');
    } finally {
      setSubmitting(false);
    }
  }, [token, submitting, submitted]);

  const canSend = (composer.trim() || attachments.length > 0) && !turnInFlight && !submitting;
  // Quick replies for the latest agent turn — prefer the standalone `suggested`
  // (set from the chat response), else fall back to the last message's own.
  const lastMsg = messages.length ? messages[messages.length - 1] : null;
  const quickReplies = (() => {
    if (turnInFlight) return [];
    if (Array.isArray(suggested) && suggested.length) return suggested.filter(Boolean);
    if (lastMsg && lastMsg.role === 'assistant' && Array.isArray(lastMsg.suggested_replies)) {
      return lastMsg.suggested_replies.filter(Boolean);
    }
    return [];
  })();
  const orgLabel = orgName || 'the team';

  // ---- loading / 404 ----
  if (loading) {
    return (
      <div className="ci-shell">
        <div className="ci-state">
          <div className="ci-brand">taali<span>.</span></div>
          <div className="ci-muted">Loading…</div>
        </div>
      </div>
    );
  }

  if (notFound) {
    return (
      <div className="ci-shell">
        <div className="ci-state">
          <div className="ci-brand">taali<span>.</span></div>
          <h1 className="ci-state-title">This link isn't valid</h1>
          <p className="ci-muted">
            The intake link may have expired or been revoked. Ask whoever shared it for a fresh one.
          </p>
        </div>
      </div>
    );
  }

  // ---- submitted (done) state ----
  if (submitted) {
    return (
      <div className="ci-shell">
        <div className="ci-state">
          <div className="ci-brand">taali<span>.</span></div>
          <div className="ci-done-glyph"><Check size={26} /></div>
          <h1 className="ci-state-title">Thanks — submitted!</h1>
          <p className="ci-muted">
            We've shared what you told us about the role with {orgLabel}. They'll take it from here and
            be in touch. You can close this tab.
          </p>
        </div>
      </div>
    );
  }

  // ---- main conversation ----
  return (
    <div className="ci-shell">
      <div className="ci-page">
        <header className="ci-head">
          <div className="ci-brand">taali<span>.</span></div>
          {orgName ? <div className="ci-org">{orgName}</div> : null}
          <h1 className="ci-title">
            {orgName ? `${orgName} — ` : ''}tell us about the role you're hiring for
          </h1>
          <p className="ci-lede">
            Chat with our assistant the way you'd brief a colleague — what the role is, what great looks
            like, must-haves and nice-to-haves. Paste notes or a draft JD if you have one. It fills in
            beside the conversation as you go.
          </p>
        </header>

        <div className="ci-split">
          {/* Conversation */}
          <div className="ci-convo">
            <div className="ci-thread">
              {messages.map((m, i) => <Turn key={i} msg={m} />)}
              {turnInFlight ? (
                <ChatMessage role="assistant"><ThinkingDots label="thinking…" /></ChatMessage>
              ) : null}
              <div ref={threadEndRef} />
            </div>

            {error ? <div className="ci-error">{error}</div> : null}

            <div className="ci-composer-wrap">
              {attachments.length > 0 ? (
                <div className="ci-attach-row">
                  {attachments.map((a) => (
                    <span key={a.id} className="ci-attach-chip">
                      {a.url ? (
                        <img className="ci-attach-thumb" src={a.url} alt={a.file.name} />
                      ) : (
                        <span className="ci-attach-glyph"><FileText size={14} /></span>
                      )}
                      <span className="ci-attach-name">{a.file.name}</span>
                      <button type="button" className="ci-attach-x" aria-label={`Remove ${a.file.name}`} onClick={() => removeAttachment(a.id)}>
                        <X size={13} />
                      </button>
                    </span>
                  ))}
                </div>
              ) : null}

              <div className="ci-composer-tools">
                <button
                  type="button"
                  className="ci-attach-btn"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={turnInFlight}
                >
                  <Paperclip size={14} /> Attach
                </button>
                <span className="ci-attach-hint">notes or a JD · or paste an image</span>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={ACCEPT}
                  multiple
                  hidden
                  onChange={onFilePick}
                />
              </div>

              {quickReplies.length > 0 ? (
                <div className="ci-quick-replies">
                  {quickReplies.map((q, i) => (
                    <button
                      key={`${q}-${i}`}
                      type="button"
                      className="ci-quick-chip"
                      onClick={() => sendQuickReply(q)}
                      disabled={turnInFlight}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              ) : null}

              <ChatComposer
                value={composer}
                onChange={setComposer}
                onSubmit={onComposerSubmit}
                onPaste={onPaste}
                placeholder="Describe the role, or answer the assistant's question…"
                busy={turnInFlight}
              />

              {/* Attachments-only send (the composer's own send is disabled on
                  empty text). */}
              {composer.trim() === '' && attachments.length > 0 ? (
                <div className="ci-send-attachments">
                  <button type="button" className="ci-btn-sm is-primary" onClick={() => sendTurn()} disabled={!canSend}>
                    {turnInFlight ? <span className="ci-spinner" /> : null} Send {attachments.length} attachment{attachments.length === 1 ? '' : 's'}
                  </button>
                </div>
              ) : null}
            </div>
          </div>

          {/* Role so far + submit */}
          <div className="ci-side">
            <RolePanel captured={captured} gaps={gaps} completeness={completeness} />
            <div className="ci-submit-bar">
              <button
                type="button"
                className="ci-submit-btn"
                onClick={submit}
                disabled={submitting || turnInFlight}
              >
                {submitting ? <span className="ci-spinner" /> : <Send size={15} />}
                {' '}Submit to {orgLabel}
              </button>
              <p className="ci-submit-hint">Keep chatting until it feels complete — then send it over.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ClientIntakePage;
