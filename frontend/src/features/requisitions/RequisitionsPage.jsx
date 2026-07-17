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
import { useSearchParams } from 'react-router-dom';
import { FileText, Plus, X } from 'lucide-react';

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
import { RequisitionConversation } from './RequisitionConversation';
import { RequisitionHeaderActions } from './RequisitionHeaderActions';
import {
  isImageRequisitionAttachment as isImage,
  REQUISITION_ATTACHMENT_ACCEPT,
  requisitionAttachmentErrorDetail,
  stageRequisitionAttachment as stageFile,
  validateRequisitionAttachments,
} from './requisitionAttachments';
import {
  errorDetail,
  isPublishedRequisition,
  isRelatedRoleBrief,
  isRequisitionBriefReadOnly,
  reloadRequisitionAfterRoleConflict,
  requisitionDisplayTitle,
  requisitionGapLabels,
  requisitionHeaderStatusLabel,
  requisitionPublishBlockedMessage,
  requisitionRoleReference,
  requisitionSourceRoleReference,
  requisitionStatusLabel,
} from './requisitionGuards';
import { useRequisitionList } from './useRequisitionList';
import './requisitions.css';

export {
  isSupportedRequisitionAttachment,
  REQUISITION_ATTACHMENT_ACCEPT,
  REQUISITION_ATTACHMENT_MAX_BYTES,
  REQUISITION_ATTACHMENT_MAX_FILES,
  requisitionAttachmentErrorDetail,
  validateRequisitionAttachments,
} from './requisitionAttachments';
export {
  isPublishedRequisition,
  isRelatedRoleBrief,
  isRequisitionBriefReadOnly,
  reloadRequisitionAfterRoleConflict,
  requisitionDisplayTitle,
  requisitionGapLabels,
  requisitionHeaderStatusLabel,
  requisitionPublishBlockedMessage,
  requisitionRoleConflictMessage,
  requisitionRoleReference,
  requisitionSourceRoleReference,
  requisitionStatusLabel,
} from './requisitionGuards';

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

export const RequisitionsPage = ({ onNavigate, NavComponent = null }) => {
  const [searchParams] = useSearchParams();
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
  const [error, setError] = useState('');
  const {
    briefs,
    hasMore: hasMoreBriefs,
    listLoading,
    loadingMore: loadingMoreBriefs,
    loadList,
    loadMore: loadMoreBriefs,
    patchListRow,
  } = useRequisitionList(setError);
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
  const routedBriefRef = useRef(null);

  const messages = useMemo(() => (Array.isArray(brief?.messages) ? brief.messages : []), [brief]);
  const relatedRoleDraft = isRelatedRoleBrief(brief);
  const relatedRolePreview = brief?.related_role_preview || null;
  const sourceRoleReference = requisitionSourceRoleReference(brief, 'the original role');
  const relatedRoleReference = requisitionRoleReference(
    brief?.job?.name || brief?.title,
    brief?.job?.role_id,
    'the related role',
  );

  const handleVersionConflict = useCallback(async (err) => {
    if (selectedId == null) return false;
    const conflictedBriefId = selectedId;
    const result = await reloadRequisitionAfterRoleConflict(conflictedBriefId, err);
    if (!result) return false;
    // A user can switch requisitions while the recovery GET is in flight. The
    // stale request remains handled, but it must not replace the newer panel.
    if (selectingRef.current !== conflictedBriefId) return true;
    if (result.brief) setBrief(result.brief);
    setError(result.message);
    return true;
  }, [selectedId]);

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

  // Job-header and role-agent entry points deep-link directly to the cloned
  // draft. Load it without waiting for (or depending on) the sidebar list.
  useEffect(() => {
    const requested = Number(searchParams.get('brief'));
    if (!Number.isFinite(requested) || requested <= 0 || routedBriefRef.current === requested) return;
    routedBriefRef.current = requested;
    void select(requested);
  }, [searchParams, select]);

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
    const validation = validateRequisitionAttachments(
      attachmentsRef.current.map((attachment) => attachment.file),
      files,
    );
    if (validation.error) {
      setError(validation.error);
      return;
    }
    const staged = validation.files.map(stageFile);
    if (staged.length) {
      setError('');
      setAttachments((prev) => [...prev, ...staged]);
    }
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
      const res = await requisitionApi.chat(selectedId, {
        message,
        files: files || [],
        expectedVersion: brief?.job?.version ?? null,
      });
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
    } catch (err) {
      const conflicted = await handleVersionConflict(err);
      if (restore) {
        // Composer send: put the text + staged attachments back in the box (URLs
        // were kept alive above) and drop the pending echo, so the message lives
        // in exactly ONE place — the composer — ready to resend.
        setBrief((prev) => (prev
          ? { ...prev, messages: (prev.messages || []).filter((m) => !m.__pending) }
          : prev));
        if (restore.composer) setComposer((prev) => (prev ? prev : restore.composer));
        if (restore.attachments?.length) setAttachments((prev) => [...restore.attachments, ...prev]);
        if (!conflicted) setError(requisitionAttachmentErrorDetail(err, 'The agent couldn\'t process that message. Your text and attachments are back in the box — try sending again.'));
      } else {
        // Quick-reply / no restore: leave the echo in place so it can be resent.
        setBrief((prev) => (prev
          ? { ...prev, messages: (prev.messages || []).map((m) => (m.__pending ? { ...m, __pending: false } : m)) }
          : prev));
        if (!conflicted) setError(requisitionAttachmentErrorDetail(err, 'The agent couldn\'t process that message. It\'s still shown above — try again.'));
      }
    } finally {
      setTurnInFlight(false);
    }
  }, [selectedId, turnInFlight, loadingBrief, brief, patchListRow, handleVersionConflict]);

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
      const res = await requisitionApi.answer(
        selectedId,
        gap.key,
        value,
        brief?.job?.version ?? null,
      );
      // Response is authoritative for the brief + the full message log.
      setBrief((prev) => ({
        ...(prev || {}),
        ...(res.brief || {}),
        messages: res.messages || res.brief?.messages || (prev?.messages ?? []),
        gaps: res.gaps ?? res.brief?.gaps ?? prev?.gaps,
      }));
      patchListRow(selectedId, res.brief || {}); // title/completeness may move
    } catch (err) {
      if (!(await handleVersionConflict(err))) {
        setError('Could not record that answer. Your reply is preserved above — try again.');
      }
    } finally {
      setTurnInFlight(false);
    }
  }, [selectedId, turnInFlight, loadingBrief, brief, runTurn, patchListRow, handleVersionConflict]);

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
    if (!selectedId) return false;
    setSavingKey(key);
    setError('');
    try {
      // Custom fields share one JSON dict, so merge rather than replace —
      // sending just { [key]: value } would wipe sibling custom fields.
      // Column fields PATCH directly.
      const payload = isCustom
        ? { custom_fields: { ...(brief?.custom_fields || {}), [key]: value } }
        : { [key]: value };
      const updated = await requisitionApi.update(
        selectedId,
        payload,
        brief?.job?.version ?? null,
      );
      setBrief((prev) => ({ ...(prev || {}), ...(updated || {}) }));
      patchListRow(selectedId, updated || {}); // title/completeness may move
      return true;
    } catch (err) {
      if (!(await handleVersionConflict(err))) {
        setError(errorDetail(err, 'Could not save that field. Try again.'));
      }
      return false;
    } finally {
      setSavingKey(null);
    }
  }, [selectedId, patchListRow, brief, handleVersionConflict]);

  // ---- internal economics: assign a client / set the client rate ----
  // Both go through the EXISTING requisitionApi.update — the serialized brief
  // it returns now carries client_id/client_name/client_rate/margin/margin_pct,
  // so merging the response keeps the margin read-out in sync after each save.
  const saveEconomics = useCallback(async (payload) => {
    if (!selectedId) return;
    setSavingEconomics(true);
    setError('');
    try {
      const updated = await requisitionApi.update(
        selectedId,
        payload,
        brief?.job?.version ?? null,
      );
      setBrief((prev) => ({ ...(prev || {}), ...(updated || {}) }));
    } catch (err) {
      if (!(await handleVersionConflict(err))) {
        setError(errorDetail(err, 'Could not save the hiring-department details. Try again.'));
      }
    } finally {
      setSavingEconomics(false);
    }
  }, [selectedId, brief, handleVersionConflict]);

  // ---- per-requisition Job spec (JD) override ----
  // Same shape as the economics save: PATCH jd_override (a string to set the
  // override, or null to clear it → revert to the template-filled draft) and
  // merge the returned brief so `brief.jd_override` updates in place.
  const saveOverride = useCallback(async (textOrNull) => {
    if (!selectedId) return;
    setSavingOverride(true);
    setError('');
    try {
      const updated = await requisitionApi.update(
        selectedId,
        { jd_override: textOrNull },
        brief?.job?.version ?? null,
      );
      setBrief((prev) => ({ ...(prev || {}), ...(updated || {}) }));
    } catch (err) {
      if (!(await handleVersionConflict(err))) {
        setError(errorDetail(err, 'Could not save the job spec. Try again.'));
      }
    } finally {
      setSavingOverride(false);
    }
  }, [selectedId, brief, handleVersionConflict]);

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
      const updated = await requisitionApi.draftResponsibilities(
        selectedId,
        brief?.job?.version ?? null,
      );
      setBrief((prev) => ({ ...(prev || {}), ...(updated || {}) }));
    } catch (err) {
      if (!(await handleVersionConflict(err))) {
        setError('Could not draft responsibilities. Try again.');
      }
    } finally {
      setDraftingResponsibilities(false);
    }
  }, [selectedId, brief, handleVersionConflict]);

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
        const updated = await requisitionApi.update(
          selectedId,
          { client_id: created.id },
          brief?.job?.version ?? null,
        );
        setBrief((prev) => ({ ...(prev || {}), ...(updated || {}) }));
      }
    } catch (err) {
      if (!(await handleVersionConflict(err))) {
        setError('Could not create that hiring department. Try again.');
      }
    } finally {
      setSavingEconomics(false);
    }
  }, [selectedId, loadClients, brief, handleVersionConflict]);

  // ---- publish / create related role ----
  // Standard drafts publish a native job page. Related-role drafts use this
  // same reviewed spec as the explicit create-and-score confirmation, without
  // publishing a second candidate-facing job.
  const publish = useCallback(async (relatedRoleAuthorization = null) => {
    if (!selectedId) return;
    // Frontend gate mirrors the backend required-field validation and gives the
    // recruiter an immediate, field-oriented message before the request.
    const remainingGaps = Array.isArray(brief?.gaps) ? brief.gaps : [];
    if (remainingGaps.length > 0) {
      // Keep the control clickable: a click now takes the recruiter directly to
      // the structured Brief and names every blocker instead of leaving them at
      // a dead disabled button. The backend still enforces the same gate.
      setRightTab('brief');
      setError(requisitionPublishBlockedMessage(
        remainingGaps,
        { relatedRole: relatedRoleDraft },
      ));
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
      const res = await requisitionApi.publish(
        selectedId,
        jdMarkdown,
        brief?.job?.version ?? null,
        relatedRoleDraft ? relatedRoleAuthorization : null,
      );
      if (res?.related_role) {
        setBrief((prev) => ({
          ...(prev || {}),
          status: res.status || 'applied',
          job: res.role_id
            ? {
                role_id: res.role_id,
                version: res.version,
                name: res.role_name || prev?.title || null,
                job_status: res.job_status,
              }
            : (prev?.job || null),
        }));
        await loadList();
        if (res.role_id) onNavigate?.('job-pipeline', { roleId: res.role_id });
        return;
      }
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
              version: res.version,
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
      if (!(await handleVersionConflict(err))) {
        setError(errorDetail(
          err,
          relatedRoleDraft
            ? 'Related-role creation failed — please try again.'
            : 'Publish failed — please try again.',
        ));
      }
    } finally {
      setPublishing(false);
    }
  }, [selectedId, loadList, brief, template, handleVersionConflict, onNavigate, relatedRoleDraft]);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps -- polled updates must not restart the bounded idle budget
  }, [shouldPoll, selectedId, turnInFlight]);

  const applied = isRequisitionBriefReadOnly(brief);
  // Block sends while a switch is in flight (brief null / loadingBrief) so a
  // reply can't post to the wrong requisition, and while a turn is in flight.
  const canSend = Boolean(brief) && !applied && !loadingBrief && (composer.trim() || attachments.length > 0) && !turnInFlight;
  // Required fields still open → publish is gated inside publish(). The button
  // remains clickable so it can reveal these exact labels and open the Brief.
  const requiredGaps = Array.isArray(brief?.gaps) ? brief.gaps : [];
  const requiredRemaining = requiredGaps.length;
  const requiredLabels = requisitionGapLabels(requiredGaps);
  const requiredFieldsHint = requiredLabels.length > 0
    ? `Required: ${requiredLabels.join(', ')}`
    : '';
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
                <span className="rq-side-count">{briefs.length}{hasMoreBriefs ? '+' : ''}</span>
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
                    <span className="rq-side-title">{requisitionDisplayTitle(b)}</span>
                    <span className="rq-side-meta">
                      <span className={`rq-dot ${isPublishedRequisition(b.status) ? 'is-published' : 'is-open'}`} />
                      {isRelatedRoleBrief(b)
                        ? `${isRequisitionBriefReadOnly(b) ? 'Related role' : 'Related draft'} · ${requisitionSourceRoleReference(b)}`
                        : requisitionStatusLabel(b.status)}
                      {b.completeness != null ? ` · ${b.completeness}%` : ''}
                    </span>
                  </button>
                </li>
              ))
            )}
            {hasMoreBriefs ? (
              <li>
                <button
                  type="button"
                  className="rq-side-item"
                  onClick={loadMoreBriefs}
                  disabled={loadingMoreBriefs}
                >
                  <span className="rq-side-title">
                    {loadingMoreBriefs ? 'Loading more…' : 'Load more job drafts'}
                  </span>
                </button>
              </li>
            ) : null}
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
              <header className={`rq-main-head${relatedRoleDraft ? ' is-related' : ''}`}>
                <div className="rq-main-head-titles">
                  <h1 className="rq-main-title">{requisitionDisplayTitle(brief)}</h1>
                  <div className="rq-main-sub">
                    <span className="rq-status-chip">{requisitionHeaderStatusLabel(brief)}</span>
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
                  <RequisitionHeaderActions
                    activeAts={activeAts}
                    activeAtsLabel={activeAtsLabel}
                    applied={applied}
                    atsBridge={atsBridge}
                    atsSpec={atsSpec}
                    atsSpecCopied={atsSpecCopied}
                    brief={brief}
                    careersCopied={careersCopied}
                    careersUrl={careersUrl}
                    clientCopied={clientCopied}
                    clientLink={clientLink}
                    clientLinkUrl={clientLinkUrl}
                    clientLinking={clientLinking}
                    copied={copied}
                    jobPage={jobPage}
                    jobPageUrl={jobPageUrl}
                    linkedExternalJobId={linkedExternalJobId}
                    linkedExternalJobLive={linkedExternalJobLive}
                    linkedExternalJobState={linkedExternalJobState}
                    linkedJob={linkedJob}
                    linkedJobOpen={linkedJobOpen}
                    onCopyAtsSpec={copyAtsSpec}
                    onCopyCareersUrl={copyCareersUrl}
                    onCopyClientUrl={copyClientUrl}
                    onCopyJobUrl={copyJobUrl}
                    onMakeClientLink={makeClientLink}
                    onNavigate={onNavigate}
                    onPublish={publish}
                    preview={relatedRolePreview}
                    publishing={publishing}
                    refCode={refCode}
                    relatedRoleDraft={relatedRoleDraft}
                    relatedRoleReference={relatedRoleReference}
                    requiredFieldsHint={requiredFieldsHint}
                    requiredRemaining={requiredRemaining}
                    sourceRoleReference={sourceRoleReference}
                  />
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

              {/* Legacy fully-applied intake records remain archived. A normal
                  published/linked requisition stays draft/submitted and does
                  not enter this read-only branch. */}
              {applied ? (
                <div className="rq-applied-note" role="note">
                  {relatedRoleDraft
                    ? `${relatedRoleReference} has been created from ${sourceRoleReference} and is now read-only.`
                    : 'This job brief has been applied to a live role, so it is now read-only.'}
                </div>
              ) : null}

              <div className="rq-split">
                {/* Conversation */}
                <RequisitionConversation
                  applied={applied}
                  attachmentAccept={REQUISITION_ATTACHMENT_ACCEPT}
                  attachments={attachments}
                  canSend={canSend}
                  composer={composer}
                  fileInputRef={fileInputRef}
                  messages={messages}
                  onComposerChange={setComposer}
                  onComposerSubmit={onComposerSubmit}
                  onFilePick={onFilePick}
                  onPaste={onPaste}
                  onQuickReply={sendQuickReply}
                  onRemoveAttachment={removeAttachment}
                  onSendAttachments={() => sendTurn()}
                  quickReplies={quickReplies}
                  relatedRoleDraft={relatedRoleDraft}
                  relatedRoleReference={relatedRoleReference}
                  sourceRoleReference={sourceRoleReference}
                  threadEndRef={threadEndRef}
                  turnInFlight={turnInFlight}
                />

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
                      // Only legacy fully-applied intake records are archived;
                      // linked published requisitions keep these edit handlers.
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
