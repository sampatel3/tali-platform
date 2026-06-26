// AI-native Requisition — the recruiter intake surface.
//
// This is a CHAT AGENT, not a form. Split-view: the conversation on the left
// (the agent drives it; the recruiter talks / pastes / drops a transcript /
// screenshots a JD), and a live brief on the right that fills in as the agent
// extracts fields. The brief is rendered FROM the org's requisition spec
// template, and every field is click-to-edit so the recruiter can refine fast
// without chatting.
//
// Reuses the SHARED CHAT KIT (ChatComposer / ChatMessage / ChatMarkdown /
// ThinkingDots) — the one standard chat UI across Search, the Home dock and
// the candidate workspace — and the global purple design tokens.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle2, FileText, Paperclip, Plus, Rocket, X } from 'lucide-react';

import { ChatComposer, ChatMarkdown, ChatMessage, ThinkingDots } from '../../shared/chat';
import { requisitionApi } from './api';
import { LiveBrief } from './LiveBrief';
import './requisitions.css';

const ACCEPT = '.txt,.vtt,.srt,.md,.pdf,image/*';
const isImage = (file) => Boolean(file && (file.type || '').startsWith('image/'));

// One staged attachment = the File + a stable id + (for images) an object URL
// for the thumbnail preview. We revoke the URL when the chip is removed / sent.
let attachSeq = 0;
const stageFile = (file) => ({
  id: `att_${Date.now()}_${attachSeq++}`,
  file,
  url: isImage(file) ? URL.createObjectURL(file) : null,
});

const statusLabel = (status) => String(status || 'draft').replace(/_/g, ' ');
const isPublished = (status) => String(status || '').toLowerCase() === 'published';

// One conversation turn rendered with the shared message bubbles. Assistant
// turns render Markdown; user turns show their text plus any attachment chips.
function Turn({ msg }) {
  const attachments = Array.isArray(msg.attachments) ? msg.attachments : [];
  if (msg.role === 'user') {
    return (
      <div className="tk-msg-user-wrap">
        <div className="tk-msg-user">
          {msg.content}
          {attachments.length > 0 ? (
            <div className="rq-attach-row" style={{ marginTop: msg.content ? 8 : 0, marginBottom: 0 }}>
              {attachments.map((a, i) => (
                <span key={i} className="rq-attach-chip" style={{ background: 'rgba(255,255,255,0.12)', borderColor: 'rgba(255,255,255,0.2)', color: '#fff' }}>
                  <span className="rq-attach-glyph"><FileText size={13} /></span>
                  <span className="rq-attach-name">{a.name}</span>
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

export const RequisitionsPage = ({ onNavigate, NavComponent = null }) => {
  const [briefs, setBriefs] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [brief, setBrief] = useState(null);
  const [template, setTemplate] = useState(null);
  const [composer, setComposer] = useState('');
  const [attachments, setAttachments] = useState([]);
  const [turnInFlight, setTurnInFlight] = useState(false);
  const [creating, setCreating] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [savingKey, setSavingKey] = useState(null);
  const [loadingBrief, setLoadingBrief] = useState(false);
  const [error, setError] = useState('');

  const fileInputRef = useRef(null);
  const threadEndRef = useRef(null);
  const attachmentsRef = useRef(attachments);
  attachmentsRef.current = attachments;

  const messages = useMemo(() => (Array.isArray(brief?.messages) ? brief.messages : []), [brief]);

  // Load the org template once + the requisition list on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await requisitionApi.getTemplate();
        if (!cancelled) setTemplate(res?.template || null);
      } catch {
        if (!cancelled) setTemplate(null);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const loadList = useCallback(async () => {
    try {
      const list = await requisitionApi.list();
      setBriefs(Array.isArray(list) ? list : []);
    } catch {
      setError('Could not load requisitions.');
    }
  }, []);

  useEffect(() => { void loadList(); }, [loadList]);

  // Revoke any staged object URLs when the page unmounts.
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

  const select = useCallback(async (id) => {
    if (id === selectedId) return;
    setSelectedId(id);
    setError('');
    setComposer('');
    clearAttachments();
    setLoadingBrief(true);
    try {
      setBrief(await requisitionApi.get(id));
    } catch {
      setError('Could not load this requisition.');
      setBrief(null);
    } finally {
      setLoadingBrief(false);
    }
  }, [selectedId, clearAttachments]);

  const createReq = useCallback(async () => {
    setCreating(true);
    setError('');
    try {
      const created = await requisitionApi.create();
      await loadList();
      // create() returns the serialized brief (with the opening assistant
      // message) directly — adopt it without a second round-trip.
      setSelectedId(created.id);
      setBrief(created);
      setComposer('');
      clearAttachments();
    } catch {
      setError('Could not create a requisition.');
    } finally {
      setCreating(false);
    }
  }, [loadList, clearAttachments]);

  // ---- attachments ----
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
      e.preventDefault(); // don't also paste the image's text/path into the box
      addFiles(imgs);
    }
  }, [addFiles]);

  // ---- send a turn ----
  const sendTurn = useCallback(async () => {
    if (!selectedId || turnInFlight) return;
    const message = composer.trim();
    const files = attachments.map((a) => a.file);
    // Allow sending with attachments and an empty message.
    if (!message && files.length === 0) return;

    setTurnInFlight(true);
    setError('');

    // Optimistic echo so the recruiter's turn shows immediately.
    const echo = {
      role: 'user',
      content: message,
      attachments: attachments.map((a) => ({ name: a.file.name, kind: isImage(a.file) ? 'image' : 'file' })),
    };
    setBrief((prev) => (prev ? { ...prev, messages: [...(prev.messages || []), echo] } : prev));
    setComposer('');
    clearAttachments();

    try {
      const res = await requisitionApi.chat(selectedId, { message, files });
      // The response is authoritative for the brief + the full message log.
      setBrief((prev) => ({
        ...(prev || {}),
        ...(res.brief || {}),
        messages: res.messages || res.brief?.messages || (prev?.messages ?? []),
        gaps: res.gaps ?? res.brief?.gaps ?? prev?.gaps,
      }));
      void loadList(); // title / completeness may have changed in the sidebar
    } catch {
      setError('The agent could not process that turn. Your message is preserved above — try again.');
    } finally {
      setTurnInFlight(false);
    }
  }, [selectedId, turnInFlight, composer, attachments, clearAttachments, loadList]);

  // ChatComposer's onSubmit only fires with non-empty text; we also need an
  // attachments-only send, so the composer's submit defers to sendTurn and we
  // expose a separate send affordance for the empty-text + attachments case.
  const onComposerSubmit = useCallback(() => { void sendTurn(); }, [sendTurn]);

  // ---- click-to-edit a brief field ----
  const saveField = useCallback(async (key, value, isCustom) => {
    if (!selectedId) return;
    setSavingKey(key);
    try {
      // Custom fields share one JSON dict, so merge rather than replace —
      // sending just { [key]: value } would wipe sibling custom fields.
      // Column fields PATCH directly.
      const payload = isCustom
        ? { custom_fields: { ...(brief?.custom_fields || {}), [key]: value } }
        : { [key]: value };
      const updated = await requisitionApi.update(selectedId, payload);
      setBrief((prev) => ({ ...(prev || {}), ...(updated || {}) }));
      void loadList();
    } catch {
      setError('Could not save that field. Try again.');
    } finally {
      setSavingKey(null);
    }
  }, [selectedId, loadList, brief]);

  // ---- publish ----
  const publish = useCallback(async () => {
    if (!selectedId) return;
    setPublishing(true);
    setError('');
    try {
      const res = await requisitionApi.publish(selectedId);
      setBrief((prev) => ({ ...(prev || {}), ...(res || {}) }));
      await loadList();
    } catch {
      setError('Publish failed — resolve any missing required fields and try again.');
    } finally {
      setPublishing(false);
    }
  }, [selectedId, loadList]);

  const published = isPublished(brief?.status);
  const canSend = (composer.trim() || attachments.length > 0) && !turnInFlight;

  return (
    <>
      {NavComponent ? <NavComponent currentPage="requisitions" onNavigate={onNavigate} /> : null}
      <div className="rq-root">
        {/* Sidebar — the requisition list */}
        <aside className="rq-side">
          <div className="rq-side-head">
            <button type="button" className="rq-new-btn" onClick={createReq} disabled={creating}>
              {creating ? <span className="rq-spinner" /> : <Plus size={15} />} New requisition
            </button>
          </div>
          <ul className="rq-side-list">
            {briefs.length === 0 ? (
              <li className="rq-side-empty">No requisitions yet. Start one and tell the agent about the role.</li>
            ) : (
              briefs.map((b) => (
                <li key={b.id}>
                  <button
                    type="button"
                    className={`rq-side-item${b.id === selectedId ? ' is-active' : ''}`}
                    onClick={() => select(b.id)}
                  >
                    <span className="rq-side-title">{b.title || 'Untitled requisition'}</span>
                    <span className="rq-side-meta">
                      <span className={`rq-dot ${isPublished(b.status) ? 'is-published' : 'is-open'}`} />
                      {statusLabel(b.status)}
                      {b.completeness != null ? ` · ${b.completeness}%` : ''}
                    </span>
                  </button>
                </li>
              ))
            )}
          </ul>
        </aside>

        {/* Main — header + the two columns */}
        <div className="rq-main">
          {!brief ? (
            <div className="rq-blank">
              <div className="rq-blank-card">
                <div className="rq-blank-glyph"><FileText size={22} /></div>
                <h2>Draft a requisition with the agent</h2>
                <p>
                  Start a new requisition, then tell the agent about the role — talk it through,
                  paste a kickoff-call transcript, or screenshot a JD. The brief fills in beside
                  the conversation as you go.
                </p>
              </div>
            </div>
          ) : (
            <>
              <header className="rq-main-head">
                <div className="rq-main-head-titles">
                  <h1 className="rq-main-title">{brief.title || 'Untitled requisition'}</h1>
                  <div className="rq-main-sub">
                    <span className="rq-status-chip">{statusLabel(brief.status)}</span>
                    <span>{Math.max(0, Math.min(100, Number(brief.completeness) || 0))}% complete</span>
                  </div>
                </div>
                {published ? (
                  <span className="rq-published-flag"><CheckCircle2 size={16} /> Published to role</span>
                ) : (
                  <button type="button" className="rq-publish-btn" onClick={publish} disabled={publishing}>
                    {publishing ? <span className="rq-spinner" /> : <Rocket size={15} />} Publish → role
                  </button>
                )}
              </header>

              {error ? <div className="rq-error">{error}</div> : null}

              <div className="rq-split">
                {/* Conversation */}
                <div className="rq-convo">
                  <div className="rq-thread">
                    {messages.map((m, i) => <Turn key={i} msg={m} />)}
                    {turnInFlight ? (
                      <ChatMessage role="assistant"><ThinkingDots label="thinking…" /></ChatMessage>
                    ) : null}
                    <div ref={threadEndRef} />
                  </div>

                  <div className="rq-composer-wrap">
                    {attachments.length > 0 ? (
                      <div className="rq-attach-row">
                        {attachments.map((a) => (
                          <span key={a.id} className="rq-attach-chip">
                            {a.url ? (
                              <img className="rq-attach-thumb" src={a.url} alt={a.file.name} />
                            ) : (
                              <span className="rq-attach-glyph"><FileText size={14} /></span>
                            )}
                            <span className="rq-attach-name">{a.file.name}</span>
                            <button type="button" className="rq-attach-x" aria-label={`Remove ${a.file.name}`} onClick={() => removeAttachment(a.id)}>
                              <X size={13} />
                            </button>
                          </span>
                        ))}
                      </div>
                    ) : null}

                    <div className="rq-composer-tools">
                      <button
                        type="button"
                        className="rq-attach-btn"
                        onClick={() => fileInputRef.current?.click()}
                        disabled={turnInFlight}
                      >
                        <Paperclip size={14} /> Attach
                      </button>
                      <span className="rq-attach-hint">transcript or JD screenshot · or paste an image</span>
                      <input
                        ref={fileInputRef}
                        type="file"
                        accept={ACCEPT}
                        multiple
                        hidden
                        onChange={onFilePick}
                      />
                    </div>

                    <ChatComposer
                      value={composer}
                      onChange={setComposer}
                      onSubmit={onComposerSubmit}
                      onPaste={onPaste}
                      placeholder="Tell the agent about the role, or answer its question…"
                      busy={turnInFlight}
                    />

                    {/* Attachments-only send (the composer's own send is
                        disabled on empty text). */}
                    {composer.trim() === '' && attachments.length > 0 ? (
                      <div style={{ marginTop: 8, display: 'flex', justifyContent: 'flex-end' }}>
                        <button type="button" className="rq-btn-sm is-primary" onClick={() => sendTurn()} disabled={!canSend}>
                          {turnInFlight ? <span className="rq-spinner" /> : null} Send {attachments.length} attachment{attachments.length === 1 ? '' : 's'}
                        </button>
                      </div>
                    ) : null}
                  </div>
                </div>

                {/* Live brief */}
                {loadingBrief ? (
                  <div className="rq-brief"><div className="rq-brief-scroll"><span className="rq-spinner" /></div></div>
                ) : (
                  <LiveBrief
                    template={template}
                    brief={brief}
                    onSave={saveField}
                    savingKey={savingKey}
                  />
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
};

export default RequisitionsPage;
