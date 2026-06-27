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
import { Check, Copy, ExternalLink, FileText, Paperclip, Plus, RefreshCw, Rocket, Share2, X } from 'lucide-react';

import { ChatComposer, ChatMarkdown, ChatMessage, ThinkingDots } from '../../shared/chat';
import { requisitionApi } from './api';
import { clientApi } from '../clients/api';
import { LiveBrief } from './LiveBrief';
import { JobSpec, renderJobSpec } from './JobSpec';
import { RequisitionEconomics } from './RequisitionEconomics';
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
  // Transient "Copied" tick on the share-URL copy button.
  const [copied, setCopied] = useState(false);
  // Minting / "Copied" tick for the client-intake link (the no-login link a
  // consultancy recruiter sends to their client to describe the role).
  const [clientLinking, setClientLinking] = useState(false);
  const [clientCopied, setClientCopied] = useState(false);
  const [savingKey, setSavingKey] = useState(null);
  const [loadingBrief, setLoadingBrief] = useState(false);
  const [error, setError] = useState('');
  // Internal economics: the org's clients (for the assign dropdown) + the
  // in-flight save flag for the client/rate strip.
  const [clients, setClients] = useState([]);
  const [savingEconomics, setSavingEconomics] = useState(false);
  // In-flight flag for the per-requisition Job-spec (JD) override save.
  const [savingOverride, setSavingOverride] = useState(false);
  // In-flight flag for the AI "Draft responsibilities" action on the Job spec.
  const [draftingResponsibilities, setDraftingResponsibilities] = useState(false);
  // Right column: the live Job spec (JD) document by default, or the
  // structured Brief.
  const [rightTab, setRightTab] = useState('jobspec');

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

  // Load the org's clients once for the assign dropdown (best-effort — the
  // economics strip still renders, just without options, if this fails).
  const loadClients = useCallback(async () => {
    try {
      const list = await clientApi.list();
      setClients(Array.isArray(list) ? list : []);
    } catch {
      /* non-fatal */
    }
  }, []);

  useEffect(() => { void loadClients(); }, [loadClients]);

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
  // Core: post one turn (message + files) with an optimistic user echo. Used by
  // both the composer and the tappable quick replies.
  const runTurn = useCallback(async (message, files, echoAttachments) => {
    if (!selectedId || turnInFlight) return;
    if (!message && (!files || files.length === 0)) return;

    setTurnInFlight(true);
    setError('');

    // Optimistic echo so the recruiter's turn shows immediately.
    setBrief((prev) => (prev
      ? { ...prev, messages: [...(prev.messages || []), { role: 'user', content: message, attachments: echoAttachments || [] }] }
      : prev));

    try {
      const res = await requisitionApi.chat(selectedId, { message, files: files || [] });
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
  }, [selectedId, turnInFlight, loadList]);

  // Send from the composer (text + staged attachments).
  const sendTurn = useCallback(() => {
    const message = composer.trim();
    const files = attachments.map((a) => a.file);
    if (!message && files.length === 0) return;
    const echoAttachments = attachments.map((a) => ({ name: a.file.name, kind: isImage(a.file) ? 'image' : 'file' }));
    setComposer('');
    clearAttachments();
    void runTurn(message, files, echoAttachments);
  }, [composer, attachments, clearAttachments, runTurn]);

  // Tap a multiple-choice quick reply → send it as the next turn immediately.
  const sendQuickReply = useCallback((text) => {
    const t = String(text || '').trim();
    if (t) void runTurn(t, [], []);
  }, [runTurn]);

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

  // ---- internal economics: assign a client / set the client rate ----
  // Both go through the EXISTING requisitionApi.update — the serialized brief
  // it returns now carries client_id/client_name/client_rate/margin/margin_pct,
  // so merging the response keeps the margin read-out in sync after each save.
  const saveEconomics = useCallback(async (payload) => {
    if (!selectedId) return;
    setSavingEconomics(true);
    setError('');
    try {
      const updated = await requisitionApi.update(selectedId, payload);
      setBrief((prev) => ({ ...(prev || {}), ...(updated || {}) }));
    } catch {
      setError('Could not save the client details. Try again.');
    } finally {
      setSavingEconomics(false);
    }
  }, [selectedId]);

  // ---- per-requisition Job spec (JD) override ----
  // Same shape as the economics save: PATCH jd_override (a string to set the
  // override, or null to clear it → revert to the template-filled draft) and
  // merge the returned brief so `brief.jd_override` updates in place.
  const saveOverride = useCallback(async (textOrNull) => {
    if (!selectedId) return;
    setSavingOverride(true);
    setError('');
    try {
      const updated = await requisitionApi.update(selectedId, { jd_override: textOrNull });
      setBrief((prev) => ({ ...(prev || {}), ...(updated || {}) }));
    } catch {
      setError('Could not save the job spec. Try again.');
    } finally {
      setSavingOverride(false);
    }
  }, [selectedId]);

  // ---- AI-draft the JD responsibilities ----
  // POST /draft-responsibilities returns the FULL serialized brief (same shape
  // as update()) with custom_fields.responsibilities populated; merge it like
  // saveEconomics/saveOverride so the {{responsibilities}} section fills in.
  // The recruiter can still hand-edit the whole JD via the existing override.
  const draftResponsibilities = useCallback(async () => {
    if (!selectedId) return;
    setDraftingResponsibilities(true);
    setError('');
    try {
      const updated = await requisitionApi.draftResponsibilities(selectedId);
      setBrief((prev) => ({ ...(prev || {}), ...(updated || {}) }));
    } catch {
      setError('Could not draft responsibilities. Try again.');
    } finally {
      setDraftingResponsibilities(false);
    }
  }, [selectedId]);

  const assignClient = useCallback((clientId) => {
    // Empty selection clears the assignment. Coerce the <select>'s string value
    // to a number so the backend FK gets an int, not a stringified id.
    const cid = clientId === '' || clientId == null ? null : Number(clientId);
    void saveEconomics({ client_id: Number.isNaN(cid) ? null : cid });
  }, [saveEconomics]);

  const setClientRate = useCallback((rate) => {
    void saveEconomics({ client_rate: rate });
  }, [saveEconomics]);

  // Inline "+ New client" — create, refetch the list, then assign it here.
  const createAndAssignClient = useCallback(async (clientName) => {
    const name = String(clientName || '').trim();
    if (!name || !selectedId) return;
    setSavingEconomics(true);
    setError('');
    try {
      const created = await clientApi.create({ name });
      await loadClients();
      if (created?.id != null) {
        const updated = await requisitionApi.update(selectedId, { client_id: created.id });
        setBrief((prev) => ({ ...(prev || {}), ...(updated || {}) }));
      }
    } catch {
      setError('Could not create that client. Try again.');
    } finally {
      setSavingEconomics(false);
    }
  }, [selectedId, loadClients]);

  // ---- publish ----
  // Snapshot the RENDERED JD (the recruiter's per-requisition override if set,
  // else the live template-filled draft — the exact markdown shown in the Job
  // spec panel) onto the public job page. Re-running refreshes that snapshot.
  // The backend returns `job_page` ({ token, url, status, published_at }) on
  // the serialized brief, which drives the published-state UI below.
  const publish = useCallback(async () => {
    if (!selectedId) return;
    setPublishing(true);
    setError('');
    try {
      const jdMarkdown = (typeof brief?.jd_override === 'string' && brief.jd_override.trim() !== '')
        ? brief.jd_override
        : renderJobSpec(template, brief);
      const res = await requisitionApi.publish(selectedId, jdMarkdown);
      // The publish response carries the job_page fields (token/url/status/…);
      // fold them into brief.job_page so the published state renders without a
      // refetch, and lift status to keep the header chip in sync.
      setBrief((prev) => ({
        ...(prev || {}),
        status: res?.status ?? prev?.status,
        job_page: res?.token
          ? { token: res.token, url: res.url, status: res.status, published_at: res.published_at }
          : (prev?.job_page || null),
      }));
      await loadList();
    } catch {
      setError('Publish failed — resolve any missing required fields and try again.');
    } finally {
      setPublishing(false);
    }
  }, [selectedId, loadList, brief, template]);

  // The published job page (token/url/status/published_at) or null. Drives the
  // header's published state on load and after a (re-)publish.
  const jobPage = brief?.job_page || null;
  // Prefer the backend-supplied absolute URL; fall back to building the
  // /job/:token link off the current origin so Copy/View always work.
  const jobPageUrl = jobPage
    ? (jobPage.url || (typeof window !== 'undefined' ? `${window.location.origin}/job/${jobPage.token}` : `/job/${jobPage.token}`))
    : '';

  const copyJobUrl = useCallback(async () => {
    if (!jobPageUrl) return;
    try {
      await navigator.clipboard.writeText(jobPageUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      setError('Could not copy the link — select and copy it manually.');
    }
  }, [jobPageUrl]);

  // ---- share with client (the no-login client-intake link) ----
  // The serialized brief carries `client_link` ({ token, url } or null). Build
  // the absolute /intake/:token URL the same way the job page does, so Copy/
  // open work whether or not the backend hands back an absolute url.
  const clientLink = brief?.client_link || null;
  const clientLinkUrl = clientLink
    ? (clientLink.url || (typeof window !== 'undefined' ? `${window.location.origin}/intake/${clientLink.token}` : `/intake/${clientLink.token}`))
    : '';

  // Mint the client-intake link on demand (idempotent on the backend), then
  // fold it into the brief so the link + Copy reveal without a refetch.
  const makeClientLink = useCallback(async () => {
    if (!selectedId) return;
    setClientLinking(true);
    setError('');
    try {
      const res = await requisitionApi.clientLink(selectedId);
      setBrief((prev) => ({
        ...(prev || {}),
        client_link: res?.token ? { token: res.token, url: res.url } : (prev?.client_link || null),
      }));
    } catch {
      setError('Could not create the client link. Try again.');
    } finally {
      setClientLinking(false);
    }
  }, [selectedId]);

  const copyClientUrl = useCallback(async () => {
    if (!clientLinkUrl) return;
    try {
      await navigator.clipboard.writeText(clientLinkUrl);
      setClientCopied(true);
      setTimeout(() => setClientCopied(false), 1800);
    } catch {
      setError('Could not copy the link — select and copy it manually.');
    }
  }, [clientLinkUrl]);

  // Reset the transient "Copied" ticks when switching requisitions.
  useEffect(() => { setCopied(false); setClientCopied(false); }, [selectedId]);

  const published = Boolean(jobPage) || isPublished(brief?.status);
  const canSend = (composer.trim() || attachments.length > 0) && !turnInFlight;
  // Multiple-choice quick replies for the latest agent turn (tap instead of type).
  const lastMsg = messages.length ? messages[messages.length - 1] : null;
  const quickReplies = (!turnInFlight && lastMsg && lastMsg.role === 'assistant' && Array.isArray(lastMsg.suggested_replies))
    ? lastMsg.suggested_replies.filter(Boolean)
    : [];

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
                <div className="rq-head-actions">
                  {/* Share with client — the no-login intake link a
                      consultancy recruiter sends to their client so the client
                      describes the role to the same agent (economics hidden). */}
                  {clientLink ? (
                    <div className="rq-clientlink">
                      <div className="rq-clientlink-top">
                        <span className="rq-clientlink-flag"><Share2 size={14} /> Client link</span>
                        <a
                          className="rq-published-url"
                          href={clientLinkUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          title={clientLinkUrl}
                        >
                          {clientLinkUrl}
                        </a>
                      </div>
                      <div className="rq-published-actions">
                        <span className="rq-clientlink-hint">Send this to your client — no login needed.</span>
                        <button type="button" className="rq-btn-sm is-ghost" onClick={copyClientUrl}>
                          {clientCopied ? <Check size={13} /> : <Copy size={13} />} {clientCopied ? 'Copied' : 'Copy'}
                        </button>
                        <a
                          className="rq-btn-sm is-ghost"
                          href={clientLinkUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          <ExternalLink size={13} /> Open
                        </a>
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="rq-btn-sm is-ghost rq-share-btn"
                      onClick={makeClientLink}
                      disabled={clientLinking}
                      title="Get a no-login link to send to your client"
                    >
                      {clientLinking ? <span className="rq-spinner" /> : <Share2 size={14} />} Share with client
                    </button>
                  )}

                  {jobPage ? (
                    <div className="rq-published">
                      <div className="rq-published-top">
                        <span className="rq-published-flag"><Check size={15} /> Published</span>
                        <a
                          className="rq-published-url"
                          href={jobPageUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          title={jobPageUrl}
                        >
                          {jobPageUrl}
                        </a>
                      </div>
                      <div className="rq-published-actions">
                        <button type="button" className="rq-btn-sm is-ghost" onClick={copyJobUrl}>
                          {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? 'Copied' : 'Copy'}
                        </button>
                        <a
                          className="rq-btn-sm is-ghost"
                          href={jobPageUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          <ExternalLink size={13} /> View job page
                        </a>
                        <button type="button" className="rq-btn-sm is-ghost" onClick={publish} disabled={publishing}>
                          {publishing ? <span className="rq-spinner" /> : <RefreshCw size={13} />} Re-publish
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button type="button" className="rq-publish-btn" onClick={publish} disabled={publishing}>
                      {publishing ? <span className="rq-spinner" /> : <Rocket size={15} />} Publish job page
                    </button>
                  )}
                </div>
              </header>

              <RequisitionEconomics
                brief={brief}
                clients={clients}
                saving={savingEconomics}
                onAssignClient={assignClient}
                onSetClientRate={setClientRate}
                onCreateClient={createAndAssignClient}
              />

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

                    {quickReplies.length > 0 ? (
                      <div className="rq-quick-replies">
                        {quickReplies.map((q, i) => (
                          <button
                            key={`${q}-${i}`}
                            type="button"
                            className="rq-quick-chip"
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

                {/* Right column — Job spec (live JD document) + Brief tabs */}
                <div className="rq-right">
                  <div className="rq-tabs" role="tablist" aria-label="Requisition detail">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={rightTab === 'jobspec'}
                      className={`rq-tab${rightTab === 'jobspec' ? ' is-active' : ''}`}
                      onClick={() => setRightTab('jobspec')}
                    >
                      Job spec
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={rightTab === 'brief'}
                      className={`rq-tab${rightTab === 'brief' ? ' is-active' : ''}`}
                      onClick={() => setRightTab('brief')}
                    >
                      Brief
                    </button>
                  </div>
                  {loadingBrief ? (
                    <div className="rq-brief"><div className="rq-brief-scroll"><span className="rq-spinner" /></div></div>
                  ) : rightTab === 'jobspec' ? (
                    <JobSpec
                      template={template}
                      brief={brief}
                      onSaveOverride={saveOverride}
                      savingOverride={savingOverride}
                      onDraftResponsibilities={draftResponsibilities}
                      draftingResponsibilities={draftingResponsibilities}
                    />
                  ) : (
                    <LiveBrief
                      template={template}
                      brief={brief}
                      onSave={saveField}
                      savingKey={savingKey}
                    />
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
};

export default RequisitionsPage;
