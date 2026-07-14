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
import { organizations as organizationsApi } from '../../shared/api';
import { MotionSkeleton, MotionSpinner, motionSafeScrollBehavior } from '../../shared/motion';
import {
  atsProviderLabel,
  organizationAtsProvider,
  roleAtsProvider,
  roleExternalJobId,
  roleExternalJobLive,
  roleExternalJobState,
} from '../jobs/atsType';
import { requisitionApi } from './api';
import { clientApi } from '../clients/api';
import { LiveBrief } from './LiveBrief';
import { JobSpec, renderJobSpec, stripPlaceholderLines } from './JobSpec';
import { RequisitionDepartment } from './RequisitionDepartment';
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

const REQUISITION_STATUS_LABELS = Object.freeze({
  draft: 'Draft',
  submitted: 'Ready to publish',
  applied: 'Published',
  published: 'Published', // compatibility with pre-lifecycle payloads
});
export const requisitionStatusLabel = (status) => {
  const normalized = String(status || 'draft').toLowerCase();
  return REQUISITION_STATUS_LABELS[normalized]
    || normalized.replace(/_/g, ' ').replace(/^./, (character) => character.toUpperCase());
};
export const isPublishedRequisition = (status) => ['applied', 'published'].includes(String(status || '').toLowerCase());

export const requisitionAtsProvider = (organization, linkedJob = null) => (
  roleAtsProvider(linkedJob) || organizationAtsProvider(organization)
);

export const buildRequisitionAtsSpec = (jdMarkdown, refCode) => {
  if (!refCode) return '';
  const body = String(jdMarkdown || '').replace(/\s+$/, '');
  const refLine = `_Taali ref: ${refCode} — please keep this line so this role links back to your Taali job._`;
  return body ? `${body}\n\n---\n${refLine}\n` : `${refLine}\n`;
};

export const requisitionAtsBridgeModel = (provider, externalJobId = null) => {
  const label = atsProviderLabel(provider);
  const linked = externalJobId != null && String(externalJobId).trim() !== '';
  return {
    linked,
    hint: linked
      ? `This Taali role is already linked to ${label}. Keep using the existing ${label} job — do not create another one. Its provider-side lifecycle remains authoritative.`
      : `Taali applications and the agent do not depend on ${label}. If you also want ${label} distribution, create it there with this spec and keep the ref line for automatic linking.`,
    copyLabel: linked ? null : `Optional: copy for ${label}`,
  };
};

// Prefer the backend's human-readable `detail` (e.g. the 409 "Brief already
// applied to a role") over a generic fallback, so the error banner tells the
// recruiter what actually happened. Only surfaces `detail` when it's a plain
// string (FastAPI validation errors hand back arrays/objects we don't want raw).
const errorDetail = (err, fallback) => {
  const detail = err?.response?.data?.detail;
  return typeof detail === 'string' && detail.trim() ? detail : fallback;
};

// A brief that's already been applied to a live role is FROZEN on the backend
// (update_brief_fields raises 409). Render it read-only rather than offering
// edits that can only 409.
const isBriefApplied = (brief) => String(brief?.status || '').toLowerCase() === 'applied';

// One conversation turn rendered with the shared message bubbles. Assistant
// turns render Markdown under a mono "AGENT" kicker (the same attribution the
// Home dock shows above its agent prose); user turns show their text plus any
// attachment chips in the ink pill.
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
  // Agent turns carry a mono "AGENT" kicker above the prose (home-preview
  // `.msg.bot .who`). We render the kicker + markdown as children — no `text`
  // prop — so the label sits above the shared <ChatMarkdown> body, keeping the
  // prose styling identical to every other chat surface.
  return (
    <ChatMessage role="assistant">
      <div className="rq-agent-say">
        <span className="rq-who">Agent</span>
        <ChatMarkdown>{msg.content}</ChatMarkdown>
      </div>
    </ChatMessage>
  );
}

export const RequisitionsPage = ({ onNavigate, NavComponent = null }) => {
  const [briefs, setBriefs] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [brief, setBrief] = useState(null);
  const [template, setTemplate] = useState(null);
  const [orgData, setOrgData] = useState(null);
  const [composer, setComposer] = useState('');
  const [attachments, setAttachments] = useState([]);
  const [turnInFlight, setTurnInFlight] = useState(false);
  const [creating, setCreating] = useState(false);
  const [publishing, setPublishing] = useState(false);
  // Transient "Copied" tick on the share-URL copy button.
  const [copied, setCopied] = useState(false);
  // Transient "Copied" tick on the careers-board URL copy button.
  const [careersCopied, setCareersCopied] = useState(false);
  // Minting / "Copied" tick for the client-intake link (the no-login link a
  // consultancy recruiter sends to their client to describe the role).
  const [clientLinking, setClientLinking] = useState(false);
  const [clientCopied, setClientCopied] = useState(false);
  // Transient "Copied" tick on the active ATS bridge-spec button.
  const [atsSpecCopied, setAtsSpecCopied] = useState(false);
  const [savingKey, setSavingKey] = useState(null);
  const [loadingBrief, setLoadingBrief] = useState(false);
  // True while the sidebar list is still loading its FIRST response, so we can
  // show skeleton rows instead of the "No requisitions yet" empty copy.
  const [listLoading, setListLoading] = useState(true);
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
  // The most-recently-requested requisition id, so a slow get() that resolves
  // after the user switches again doesn't clobber the newer brief.
  const selectingRef = useRef(null);

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
    organizationsApi.get()
      .then((res) => { if (!cancelled) setOrgData(res?.data || null); })
      .catch(() => { if (!cancelled) setOrgData(null); });
    return () => { cancelled = true; };
  }, []);

  const loadList = useCallback(async () => {
    try {
      const list = await requisitionApi.list();
      setBriefs(Array.isArray(list) ? list : []);
    } catch {
      setError('Could not load job drafts.');
    } finally {
      // First response is in (empty or not) — the sidebar can stop showing
      // skeletons and, if the list really is empty, show the empty copy.
      setListLoading(false);
    }
  }, []);

  useEffect(() => { void loadList(); }, [loadList]);

  // Patch the selected requisition's sidebar row in place from a turn/answer/
  // save response — the only sidebar-visible fields are title/status/
  // completeness. This replaces a full loadList() after every chat turn / quick
  // reply / field save (list MEMBERSHIP only changes on create/publish, which
  // still call loadList).
  const patchListRow = useCallback((id, patch) => {
    if (id == null || !patch) return;
    setBriefs((prev) => prev.map((b) => (b.id === id
      ? {
          ...b,
          ...(patch.title !== undefined ? { title: patch.title } : {}),
          ...(patch.status !== undefined ? { status: patch.status } : {}),
          ...(patch.completeness !== undefined ? { completeness: patch.completeness } : {}),
        }
      : b)));
  }, []);

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
    threadEndRef.current?.scrollIntoView({ behavior: motionSafeScrollBehavior('smooth'), block: 'end' });
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
    // Drop the previous requisition's brief the moment we switch, so the header
    // (title/status/completeness) and the thread don't keep showing the OLD
    // requisition under the newly-selected item. `loadingBrief` then gates a
    // skeleton over the whole panel — and, because `brief` is null, sends and
    // quick-replies are blocked until the new brief arrives (no reply can post
    // to the wrong requisition).
    setBrief(null);
    setLoadingBrief(true);
    selectingRef.current = id;
    try {
      const next = await requisitionApi.get(id);
      // Ignore a response for a requisition the user has since switched away
      // from — otherwise a slow get() would clobber the newer brief.
      if (selectingRef.current !== id) return;
      setBrief(next);
    } catch {
      if (selectingRef.current !== id) return;
      setError('Could not load this job draft.');
      setBrief(null);
    } finally {
      if (selectingRef.current === id) setLoadingBrief(false);
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
      selectingRef.current = created.id;
      setSelectedId(created.id);
      setBrief(created);
      setComposer('');
      clearAttachments();
    } catch {
      setError('Could not create a job draft.');
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
  //
  // `restore` (optional) is the staged-attachment objects for a COMPOSER send:
  // we hold them (and their object URLs) until the POST SUCCEEDS, so a failed
  // send can put the recruiter's text + attachments back in the composer
  // instead of losing them. Quick-replies pass no restore (no attachments).
  const runTurn = useCallback(async (message, files, echoAttachments, restore) => {
    if (!selectedId || turnInFlight || loadingBrief || !brief) return;
    if (!message && (!files || files.length === 0)) return;

    setTurnInFlight(true);
    setError('');

    // Optimistic echo so the recruiter's turn shows immediately. Flagged
    // `__pending` so we can strip it again if the send fails (the text +
    // attachments are restored to the composer instead — see the catch).
    setBrief((prev) => (prev
      ? { ...prev, messages: [...(prev.messages || []), { role: 'user', content: message, attachments: echoAttachments || [], __pending: true }] }
      : prev));

    try {
      const res = await requisitionApi.chat(selectedId, { message, files: files || [] });
      // The response is authoritative for the brief + the full message log.
      const merged = {
        ...(res.brief || {}),
        messages: res.messages || res.brief?.messages,
        gaps: res.gaps ?? res.brief?.gaps,
      };
      setBrief((prev) => ({
        ...(prev || {}),
        ...merged,
        messages: merged.messages ?? (prev?.messages ?? []),
        gaps: merged.gaps ?? prev?.gaps,
      }));
      // Sidebar row (title/status/completeness) may have moved — patch it in
      // place rather than refetching the whole list.
      patchListRow(selectedId, res.brief || {});
      // Success: the staged attachments are now sent — revoke their URLs.
      (restore?.attachments || []).forEach((a) => a.url && URL.revokeObjectURL(a.url));
    } catch {
      if (restore) {
        // Composer send: put the text + staged attachments back in the box (URLs
        // were kept alive above) and drop the pending echo, so the message lives
        // in exactly ONE place — the composer — ready to resend.
        setBrief((prev) => (prev
          ? { ...prev, messages: (prev.messages || []).filter((m) => !m.__pending) }
          : prev));
        if (restore.composer) setComposer((prev) => (prev ? prev : restore.composer));
        if (restore.attachments?.length) setAttachments((prev) => [...restore.attachments, ...prev]);
        setError('The agent couldn\'t process that message. Your text and attachments are back in the box — try sending again.');
      } else {
        // Quick-reply / no restore: leave the echo in place so it can be resent.
        setBrief((prev) => (prev
          ? { ...prev, messages: (prev.messages || []).map((m) => (m.__pending ? { ...m, __pending: false } : m)) }
          : prev));
        setError('The agent couldn\'t process that message. It\'s still shown above — try again.');
      }
    } finally {
      setTurnInFlight(false);
    }
  }, [selectedId, turnInFlight, loadingBrief, brief, patchListRow]);

  // Send from the composer (text + staged attachments). Clear the composer
  // OPTIMISTICALLY but hand the staged attachment objects to runTurn so a
  // failed send can restore them; we DON'T revoke their URLs here (runTurn
  // revokes only on success).
  const sendTurn = useCallback(() => {
    const message = composer.trim();
    const staged = attachments;
    const files = staged.map((a) => a.file);
    if (!message && files.length === 0) return;
    const echoAttachments = staged.map((a) => ({ name: a.file.name, kind: isImage(a.file) ? 'image' : 'file' }));
    setComposer('');
    setAttachments([]); // clear the box WITHOUT revoking — runTurn owns the URLs now
    void runTurn(message, files, echoAttachments, { composer: message, attachments: staged });
  }, [composer, attachments, runTurn]);

  // Record ONE field DETERMINISTICALLY (no LLM) when the recruiter taps a
  // TEMPLATE-OPTION quick reply — a clean structured answer maps to exactly one
  // field=value on the CURRENT gap. We optimistically echo the tapped value as
  // a user turn (like runTurn), POST it to /answer against the currently-loaded
  // brief's first gap, then merge the authoritative response the SAME way the
  // chat-turn merge does. Only called when the rendered chips ARE the current
  // gap's options (see sendQuickReply) — free-form suggested_replies route
  // through runTurn instead, so an answer is never recorded against the wrong
  // field.
  const answerGap = useCallback(async (value) => {
    if (!selectedId || turnInFlight || loadingBrief || !brief) return;
    // Bind to the CURRENTLY-loaded brief's first gap; if none (or we're mid
    // switch and brief is null), fall back to the LLM path so it still lands.
    const gap = (brief?.gaps || [])[0] || null;
    if (!gap) {
      const t = String(value ?? '').trim();
      if (t) void runTurn(t, [], []);
      return;
    }

    setTurnInFlight(true);
    setError('');

    // Optimistic echo so the recruiter's tap shows immediately.
    const echoText = String(value ?? '');
    setBrief((prev) => (prev
      ? { ...prev, messages: [...(prev.messages || []), { role: 'user', content: echoText, attachments: [] }] }
      : prev));

    try {
      const res = await requisitionApi.answer(selectedId, gap.key, value);
      // Response is authoritative for the brief + the full message log.
      setBrief((prev) => ({
        ...(prev || {}),
        ...(res.brief || {}),
        messages: res.messages || res.brief?.messages || (prev?.messages ?? []),
        gaps: res.gaps ?? res.brief?.gaps ?? prev?.gaps,
      }));
      patchListRow(selectedId, res.brief || {}); // title/completeness may move
    } catch {
      setError('Could not record that answer. Your reply is preserved above — try again.');
    } finally {
      setTurnInFlight(false);
    }
  }, [selectedId, turnInFlight, loadingBrief, brief, runTurn, patchListRow]);

  // Tap a quick reply. `deterministic` = the chip IS one of the current gap's
  // template options, so record it against that field with no LLM (answerGap).
  // Otherwise the chip is an LLM-generated suggested_reply — possibly for a
  // DIFFERENT question — so route it through the LLM /chat path (runTurn), which
  // handles free-form routing, rather than force it onto gaps[0].
  const sendQuickReply = useCallback((text, deterministic) => {
    const t = String(text ?? '').trim();
    if (!t) return;
    if (deterministic) void answerGap(t);
    else void runTurn(t, [], []);
  }, [answerGap, runTurn]);

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
      patchListRow(selectedId, updated || {}); // title/completeness may move
    } catch (err) {
      setError(errorDetail(err, 'Could not save that field. Try again.'));
    } finally {
      setSavingKey(null);
    }
  }, [selectedId, patchListRow, brief]);

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
    } catch (err) {
      setError(errorDetail(err, 'Could not save the hiring-department details. Try again.'));
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
    } catch (err) {
      setError(errorDetail(err, 'Could not save the job spec. Try again.'));
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
      setError('Could not create that hiring department. Try again.');
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
    // Frontend gate mirrors the backend required-field validation and gives the
    // recruiter an immediate, field-oriented message before the request.
    const remaining = Array.isArray(brief?.gaps) ? brief.gaps.length : 0;
    if (remaining > 0) {
      setError(`${remaining} required field${remaining === 1 ? '' : 's'} still needed before you can publish — fill them in on the Brief tab or answer the agent.`);
      return;
    }
    setPublishing(true);
    setError('');
    try {
      const rawMarkdown = (typeof brief?.jd_override === 'string' && brief.jd_override.trim() !== '')
        ? brief.jd_override
        : renderJobSpec(template, brief);
      // Belt-and-braces: even if a placeholder slips through, never ship a
      // "(to be captured)" line to candidates — strip any such line before
      // sending the markdown the backend stores on the public page.
      const jdMarkdown = stripPlaceholderLines(rawMarkdown);
      const res = await requisitionApi.publish(selectedId, jdMarkdown);
      // The publish response carries the job_page fields (token/url/status/…);
      // fold them into brief.job_page so the published state renders without a
      // refetch, and lift status to keep the header chip in sync.
      setBrief((prev) => ({
        ...(prev || {}),
        status: res?.status ?? prev?.status,
        // Stage-1 bridge: the ref code + the inactive job publish stood up.
        ref_code: res?.ref_code ?? prev?.ref_code,
        job: res?.role_id
          ? {
              role_id: res.role_id,
              name: prev?.title ?? prev?.job?.name ?? null,
              job_status: res.job_status,
              ats_provider: res?.ats_provider ?? prev?.job?.ats_provider ?? null,
              external_job_id: res?.external_job_id ?? prev?.job?.external_job_id ?? null,
              external_job_state: res?.external_job_state ?? prev?.job?.external_job_state ?? null,
              external_job_live: res?.external_job_live ?? prev?.job?.external_job_live ?? null,
              workable_job_id: res?.workable_job_id ?? prev?.job?.workable_job_id ?? null,
              bullhorn_job_order_id: res?.bullhorn_job_order_id ?? prev?.job?.bullhorn_job_order_id ?? null,
            }
          : (prev?.job || null),
        job_page: res?.token
          ? { token: res.token, url: res.url, status: res.status, published_at: res.published_at }
          : (prev?.job_page || null),
      }));
      await loadList();
    } catch (err) {
      setError(errorDetail(err, 'Publish failed — please try again.'));
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

  // ---- Optional external ATS bridge ----
  // A native requisition remains runnable on its own. When the workspace has an
  // active ATS, retain the ref code + rendered JD bridge so that provider's
  // import can adopt this exact Taali role instead of creating a duplicate.
  const refCode = brief?.ref_code || '';
  const linkedJob = brief?.job || null;
  const linkedJobOpen = String(linkedJob?.job_status || '').toLowerCase() === 'open';
  const activeAts = requisitionAtsProvider(orgData, linkedJob);
  const activeAtsLabel = atsProviderLabel(activeAts);
  const linkedExternalJobId = roleExternalJobId(linkedJob);
  const linkedExternalJobState = roleExternalJobState(linkedJob);
  const linkedExternalJobLive = roleExternalJobLive(linkedJob);
  const atsBridge = requisitionAtsBridgeModel(activeAts, linkedExternalJobId);
  const atsSpec = useMemo(() => {
    const jd = (typeof brief?.jd_override === 'string' && brief.jd_override.trim() !== '')
      ? brief.jd_override
      : renderJobSpec(template, brief);
    return buildRequisitionAtsSpec(jd, refCode);
  }, [refCode, brief, template]);

  const copyAtsSpec = useCallback(async () => {
    if (!atsSpec) return;
    try {
      await navigator.clipboard.writeText(atsSpec);
      setAtsSpecCopied(true);
      setTimeout(() => setAtsSpecCopied(false), 1800);
    } catch {
      setError('Could not copy the spec — select and copy it manually.');
    }
  }, [atsSpec]);

  // The org's public careers board URL (string or null on the brief). When set,
  // we surface it alongside the published job-page link so the recruiter can
  // point candidates at the board where this role also appears.
  const careersUrl = (typeof brief?.careers_url === 'string' && brief.careers_url.trim() !== '')
    ? brief.careers_url
    : '';

  const copyCareersUrl = useCallback(async () => {
    if (!careersUrl) return;
    try {
      await navigator.clipboard.writeText(careersUrl);
      setCareersCopied(true);
      setTimeout(() => setCareersCopied(false), 1800);
    } catch {
      setError('Could not copy the link — select and copy it manually.');
    }
  }, [careersUrl]);

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
      setError('Could not create the hiring-manager link. Try again.');
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
  useEffect(() => { setCopied(false); setClientCopied(false); setCareersCopied(false); setAtsSpecCopied(false); }, [selectedId]);

  // NOTE: the hiring-manager link is minted ONLY when the recruiter clicks
  // "Share with hiring manager" (makeClientLink) — NOT auto-minted on open. That
  // keeps requisitions the recruiter isn't sharing off the polling path below
  // (which only runs while a link exists), so we don't background-poll every
  // opened requisition.

  // Live sync from the client link: the client fills the role from their no-login
  // link and the backend persists each turn to THIS brief — so poll every 5s
  // while a client link is live (and the requisition isn't finalized) so the
  // recruiter sees the client's input appear WITHOUT waiting for them to submit.
  //
  // Bounded, not forever: we only poll while a hiring-manager link EXISTS (the
  // recruiter is actively sharing/watching), the tab is visible, and the brief
  // isn't already complete or applied. We back off to 20s and STOP after a run
  // of idle polls with no change — so an opened-but-idle requisition doesn't
  // poll in the background indefinitely.
  const IDLE_POLL_LIMIT = 6; // ~2 min of no change → stop
  const briefComplete = (brief?.completeness ?? 0) >= 100
    || (Array.isArray(brief?.gaps) && brief.gaps.length === 0 && (brief?.completeness ?? 0) > 0);
  const shouldPoll = Boolean(selectedId) && Boolean(clientLink)
    && brief?.status !== 'applied' && !briefComplete;
  useEffect(() => {
    if (!shouldPoll) return undefined;
    let idle = 0;
    let stopped = false;
    let timer = null;
    // Signature of the last-seen brief so we can count "no change" polls.
    let lastSig = `${brief?.completeness ?? ''}|${Array.isArray(brief?.messages) ? brief.messages.length : 0}`;
    const tick = async () => {
      if (stopped) return;
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') { schedule(); return; }
      if (turnInFlight) { schedule(); return; }
      try {
        const fresh = await requisitionApi.get(selectedId);
        if (stopped) return;
        const sig = `${fresh?.completeness ?? ''}|${Array.isArray(fresh?.messages) ? fresh.messages.length : 0}`;
        if (sig === lastSig) {
          idle += 1;
        } else {
          idle = 0;
          lastSig = sig;
          setBrief((prev) => (prev && fresh && prev.id === fresh.id ? { ...prev, ...fresh } : prev));
        }
      } catch { /* transient — keep polling */ }
      if (idle >= IDLE_POLL_LIMIT) { stopped = true; return; } // give up until the effect re-runs
      schedule();
    };
    const schedule = () => { timer = setTimeout(tick, 20000); };
    schedule();
    return () => { stopped = true; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shouldPoll, selectedId, turnInFlight]);

  const published = Boolean(jobPage) || isPublishedRequisition(brief?.status);
  // Block sends while a switch is in flight (brief null / loadingBrief) so a
  // reply can't post to the wrong requisition, and while a turn is in flight.
  const canSend = Boolean(brief) && !loadingBrief && (composer.trim() || attachments.length > 0) && !turnInFlight;
  // Required fields still open → publish is gated (see publish()). Drives the
  // Publish button's disabled state + hint.
  const requiredRemaining = Array.isArray(brief?.gaps) ? brief.gaps.length : 0;
  const applied = isBriefApplied(brief);

  // The next required field the agent wants (gaps are ordered; first = current).
  const currentGap = (brief?.gaps || [])[0] || null;

  // Find a template field by its key by walking template.sections[].fields[].
  // Returns the field descriptor ({ type, options, … }) or null.
  const templateFieldByKey = useCallback((key) => {
    if (!key) return null;
    const sections = Array.isArray(template?.sections) ? template.sections : [];
    for (const section of sections) {
      for (const field of (Array.isArray(section.fields) ? section.fields : [])) {
        if (field?.key === key) return field;
      }
    }
    return null;
  }, [template]);

  // Multiple-choice quick replies for the latest agent turn (tap instead of
  // type). Prefer the CURRENT gap's template SELECT options when available —
  // clean template options guarantee a tap maps to one field=value, so it can
  // be recorded deterministically (no LLM). Otherwise fall back to the latest
  // assistant message's suggested_replies (existing behaviour).
  const lastMsg = messages.length ? messages[messages.length - 1] : null;
  const gapField = currentGap ? templateFieldByKey(currentGap.key) : null;
  const gapOptions = (gapField && gapField.type === 'select' && Array.isArray(gapField.options))
    ? gapField.options.filter(Boolean)
    : [];
  // Free-text-first: hold back tappable options until the user has answered the
  // opener in their own words, so the brief is grounded in what they say rather
  // than a menu they click through.
  const hasUserTurn = messages.some((m) => m && m.role === 'user');
  // Each chip carries whether it's DETERMINISTIC (a current-gap template option
  // → record via /answer) or an LLM suggested_reply (→ route via /chat). This
  // keeps a suggested_reply for a different question from being recorded against
  // gaps[0]. Also suppressed mid-switch (loadingBrief) so a tap can't post to
  // the wrong requisition.
  const quickReplies = (turnInFlight || loadingBrief || !hasUserTurn)
    ? []
    : gapOptions.length > 0
      ? gapOptions.map((q) => ({ text: q, deterministic: true }))
      : (lastMsg && lastMsg.role === 'assistant' && Array.isArray(lastMsg.suggested_replies))
        ? lastMsg.suggested_replies.filter(Boolean).map((q) => ({ text: q, deterministic: false }))
        : [];

  return (
    <div className="rq-shell">
      {NavComponent ? <NavComponent currentPage="requisitions" onNavigate={onNavigate} /> : null}
      <div className="rq-root">
        {/* Sidebar — the requisition list (a bordered card: kicker head +
            "New requisition" + a flat hairline list, mirroring the Home rail) */}
        <aside className="rq-side">
          <div className="rq-side-head">
            <div className="rq-side-head-row">
              <span className="rq-side-kicker">Job drafts</span>
              {briefs.length > 0 ? (
                <span className="rq-side-count">{briefs.length}</span>
              ) : null}
            </div>
            <button type="button" className="rq-new-btn" onClick={createReq} disabled={creating}>
              {creating ? <MotionSpinner className="rq-motion-spinner" size={15} /> : <Plus size={15} />} New job
            </button>
          </div>
          <ul className="rq-side-list">
            {listLoading && briefs.length === 0 ? (
              // First load — show a few skeleton rows so the rail doesn't flash
              // the "No requisitions yet" empty copy before the list arrives.
              [0, 1, 2].map((i) => (
                <li key={`sk-${i}`} className="rq-side-item is-skeleton" aria-hidden="true">
                  <MotionSkeleton className="rq-skel-line rq-skel-title" />
                  <MotionSkeleton className="rq-skel-line rq-skel-meta" />
                </li>
              ))
            ) : briefs.length === 0 ? (
              <li className="rq-side-empty">No job drafts yet. Start one and tell the agent about the role.</li>
            ) : (
              briefs.map((b) => (
                <li key={b.id}>
                  <button
                    type="button"
                    className={`rq-side-item${b.id === selectedId ? ' is-active' : ''}`}
                    onClick={() => select(b.id)}
                  >
                    <span className="rq-side-title">{b.title || 'Untitled job'}</span>
                    <span className="rq-side-meta">
                      <span className={`rq-dot ${isPublishedRequisition(b.status) ? 'is-published' : 'is-open'}`} />
                      {requisitionStatusLabel(b.status)}
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
          {loadingBrief ? (
            // Switching requisitions — skeleton over the WHOLE panel (header +
            // thread), not just the right column, so the previous requisition's
            // title/status/conversation never shows under the new selection.
            <div className="rq-switching" aria-busy="true" aria-live="polite">
              <div className="rq-switching-head">
                <MotionSkeleton className="rq-skel-line rq-skel-h1" />
                <MotionSkeleton className="rq-skel-line rq-skel-sub" />
              </div>
              <div className="rq-switching-body">
                <MotionSpinner className="rq-motion-spinner" size={15} />
                <span className="rq-switching-note">Loading job…</span>
              </div>
            </div>
          ) : !brief ? (
            <div className="rq-blank">
              <div className="rq-blank-card">
                <div className="rq-blank-glyph"><FileText size={22} /></div>
                <h2>Create a job with the agent</h2>
                <p>
                  Start a new job, then tell the agent about the role — talk it through,
                  paste a kickoff-call transcript, or screenshot a JD. The brief fills in beside
                  the conversation as you go.
                </p>
                {/* On narrow viewports the sidebar (and its "New requisition"
                    button) is hidden, so this CTA is the only way in — keep it
                    reachable here so the empty state isn't a dead end. */}
                <button type="button" className="rq-publish-btn rq-blank-cta" onClick={createReq} disabled={creating}>
                  {creating ? <MotionSpinner className="rq-motion-spinner" size={15} /> : <Plus size={15} />} Start a job
                </button>
              </div>
            </div>
          ) : (
            <>
              <header className="rq-main-head">
                <div className="rq-main-head-titles">
                  <h1 className="rq-main-title">{brief.title || 'Untitled job'}</h1>
                  <div className="rq-main-sub">
                    <span className="rq-status-chip">{requisitionStatusLabel(brief.status)}</span>
                    <span>{Math.max(0, Math.min(100, Number(brief.completeness) || 0))}% complete</span>
                  </div>
                  {/* Hiring department folded into the header (no separate
                      economics band) to keep the chat the focus. */}
                  <RequisitionDepartment
                    brief={brief}
                    clients={clients}
                    saving={savingEconomics}
                    onAssignClient={assignClient}
                    onCreateClient={createAndAssignClient}
                  />
                </div>
                <div className="rq-head-actions">
                  {/* Share with the hiring manager — the no-login intake link
                      the recruiter sends so the hiring manager describes the role
                      to the same agent (economics + internal logistics hidden). */}
                  {clientLink ? (
                    <div className="rq-clientlink">
                      <div className="rq-clientlink-top">
                        <span className="rq-clientlink-flag"><Share2 size={14} /> Hiring-manager link</span>
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
                        <span className="rq-clientlink-hint">Send this to the hiring manager — no login needed.</span>
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
                      title="Get a no-login link to send to the hiring manager"
                    >
                      {clientLinking ? <MotionSpinner className="rq-motion-spinner" size={15} /> : <Share2 size={14} />} Share with hiring manager
                    </button>
                  )}

                  {jobPage ? (
                    <div className="rq-published">
                      <div className="rq-published-top">
                        <span className="rq-published-flag">
                          <Check size={15} /> {linkedJobOpen ? 'Live · accepting applications' : 'Preview ready · applications open after Turn on'}
                        </span>
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
                        {linkedJob?.role_id ? (
                          <button
                            type="button"
                            className="rq-btn-sm is-primary"
                            onClick={() => onNavigate?.('job-pipeline', { roleId: linkedJob.role_id })}
                          >
                            <Rocket size={13} /> {linkedJobOpen ? 'Open job' : 'Open job to turn on'}
                          </button>
                        ) : null}
                        <button type="button" className="rq-btn-sm is-ghost" onClick={copyJobUrl}>
                          {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? 'Copied' : (linkedJobOpen ? 'Copy' : 'Copy preview')}
                        </button>
                        <a
                          className="rq-btn-sm is-ghost"
                          href={jobPageUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          <ExternalLink size={13} /> {linkedJobOpen ? 'View job page' : 'View preview'}
                        </a>
                        <button
                          type="button"
                          className="rq-btn-sm is-ghost"
                          onClick={publish}
                          disabled={publishing || requiredRemaining > 0}
                          title={requiredRemaining > 0 ? `${requiredRemaining} required field${requiredRemaining === 1 ? '' : 's'} still needed` : undefined}
                        >
                          {publishing ? <MotionSpinner className="rq-motion-spinner" size={15} /> : <RefreshCw size={13} />} Re-publish
                        </button>
                      </div>
                      {careersUrl ? (
                        <div className="rq-careers-row">
                          <a
                            className="rq-careers-link"
                            href={careersUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            title={careersUrl}
                          >
                            {linkedJobOpen ? 'Live on your careers page' : 'Appears on your careers page after Turn on'} <ExternalLink size={12} />
                          </a>
                          <button type="button" className="rq-btn-sm is-ghost" onClick={copyCareersUrl}>
                            {careersCopied ? <Check size={13} /> : <Copy size={13} />} {careersCopied ? 'Copied' : 'Copy'}
                          </button>
                        </div>
                      ) : null}

                      {/* Optional active-ATS distribution bridge. The native
                          Taali job + agent workflow is already ready. */}
                      {activeAts ? (
                        <div className="rq-workable-row">
                          <div className="rq-workable-head">
                            <span className={`rq-job-status ${linkedExternalJobId && linkedExternalJobLive !== false ? 'is-open' : 'is-draft'}`}>
                              {linkedExternalJobId
                                ? `Linked to ${activeAtsLabel} · ${linkedExternalJobLive === false ? (linkedExternalJobState || 'not live') : 'Open'}`
                                : `Taali job ready · ${activeAtsLabel} optional`}
                            </span>
                            {refCode ? <code className="rq-ref-code" title="Job reference code">{refCode}</code> : null}
                          </div>
                          <p className="rq-workable-hint">
                            {atsBridge.hint}
                          </p>
                          {atsBridge.copyLabel ? (
                            <button
                              type="button"
                              className="rq-btn-sm is-ghost"
                              onClick={copyAtsSpec}
                              disabled={!atsSpec}
                            >
                              {atsSpecCopied ? <Check size={13} /> : <FileText size={13} />} {atsSpecCopied ? 'Copied' : atsBridge.copyLabel}
                            </button>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  ) : (
                    <div className="rq-publish-wrap">
                      <button
                        type="button"
                        className="rq-publish-btn"
                        onClick={publish}
                        disabled={publishing || requiredRemaining > 0}
                        title={requiredRemaining > 0 ? `${requiredRemaining} required field${requiredRemaining === 1 ? '' : 's'} still needed` : undefined}
                      >
                        {publishing ? <MotionSpinner className="rq-motion-spinner" size={15} /> : <Rocket size={15} />} Publish job page
                      </button>
                      {requiredRemaining > 0 ? (
                        <span className="rq-publish-hint">
                          {requiredRemaining} required field{requiredRemaining === 1 ? '' : 's'} still needed
                        </span>
                      ) : null}
                    </div>
                  )}
                </div>
              </header>

              {error ? (
                <div className="rq-error" role="alert">
                  <span className="rq-error-text">{error}</span>
                  <button
                    type="button"
                    className="taali-icon-btn taali-icon-btn-ghost taali-icon-btn-sm rq-error-dismiss"
                    aria-label="Dismiss message"
                    onClick={() => setError('')}
                  >
                    <X size={14} />
                  </button>
                </div>
              ) : null}

              {/* Applied brief — frozen on the backend (edits 409). Explain
                  why fields are read-only instead of letting saves fail. */}
              {applied ? (
                <div className="rq-applied-note" role="note">
                  This job brief has been applied to a live role, so it is now read-only.
                </div>
              ) : null}

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
                            key={`${q.text}-${i}`}
                            type="button"
                            className="rq-quick-chip"
                            onClick={() => sendQuickReply(q.text, q.deterministic)}
                            disabled={turnInFlight}
                          >
                            {q.text}
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
                          {turnInFlight ? <MotionSpinner className="rq-motion-spinner" size={15} /> : null} Send {attachments.length} attachment{attachments.length === 1 ? '' : 's'}
                        </button>
                      </div>
                    ) : null}
                  </div>
                </div>

                {/* Right column — Job spec (live JD document) + Brief tabs */}
                <div className="rq-right">
                  <div className="rq-tabs" role="tablist" aria-label="Job setup detail">
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
                    <div className="rq-brief"><div className="rq-brief-scroll"><MotionSpinner label="Loading job" size={15} /></div></div>
                  ) : rightTab === 'jobspec' ? (
                    <JobSpec
                      template={template}
                      brief={brief}
                      // Applied briefs are frozen (backend 409s edits) — omit the
                      // save/draft handlers so JobSpec renders view-only.
                      onSaveOverride={applied ? undefined : saveOverride}
                      savingOverride={savingOverride}
                      onDraftResponsibilities={applied ? undefined : draftResponsibilities}
                      draftingResponsibilities={draftingResponsibilities}
                    />
                  ) : (
                    <LiveBrief
                      template={template}
                      brief={brief}
                      onSave={saveField}
                      savingKey={savingKey}
                      readOnly={applied}
                    />
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default RequisitionsPage;
