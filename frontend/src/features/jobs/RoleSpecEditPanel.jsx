import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Eye, Lock, PencilLine, Save, Sparkles } from 'lucide-react';

import { MotionStagger, PresenceSwap } from '../../shared/motion';
import { Button, Input, TabBar, Textarea } from '../../shared/ui/TaaliPrimitives';
import { ConfirmActionDialog } from '../../shared/ui/ConfirmActionDialog';
import { FormattedJobSpecSection, parseJobSpec } from './jobSpecFormatting';

const MIN_SPEC_LENGTH = 60;
const MAX_SPEC_LENGTH = 100000;

const ROLE_SPEC_VIEW_TABS = [
  {
    id: 'write',
    tabId: 'role-spec-write-tab',
    panelId: 'role-spec-write-panel',
    label: <><PencilLine size={13} aria-hidden="true" /> Write</>,
  },
  {
    id: 'preview',
    tabId: 'role-spec-preview-tab',
    panelId: 'role-spec-preview-panel',
    label: <><Eye size={13} aria-hidden="true" /> Preview</>,
  },
];

const roleSeed = (role) => ({
  id: role?.id ?? null,
  name: String(role?.name || ''),
  jobSpecText: String(role?.job_spec_text || role?.description || ''),
});

const sourceLabel = (source) => {
  const normalized = String(source || '').trim().toLowerCase();
  if (normalized === 'workable') return 'Workable';
  if (normalized === 'bullhorn') return 'Bullhorn';
  return 'Taali';
};

/** Focused document editor for the role's authoritative job specification. */
export const RoleSpecEditPanel = ({
  role,
  saving = false,
  error = '',
  conflict = null,
  onSubmit,
  onCancel,
  onDirtyChange,
  onResolveConflict,
}) => {
  const initialSeed = roleSeed(role);
  const baselineRef = useRef(initialSeed);
  const dirtyRef = useRef(false);
  const [name, setName] = useState(initialSeed.name);
  const [jobSpecText, setJobSpecText] = useState(initialSeed.jobSpecText);
  const [mode, setMode] = useState('write');
  const [nameTouched, setNameTouched] = useState(false);
  const [specTouched, setSpecTouched] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  const normalizedSource = String(role?.source || '').toLowerCase();
  const externallyManagedTitle = normalizedSource === 'workable' || normalizedSource === 'bullhorn';
  const atsName = sourceLabel(role?.source);
  const hasLocalOverride = Boolean(role?.job_spec_manually_edited_at);
  const cleanName = name.trim();
  const cleanSpec = jobSpecText.trim();
  const nameValid = externallyManagedTitle || cleanName.length > 0;
  const specValid = cleanSpec.length >= MIN_SPEC_LENGTH && cleanSpec.length <= MAX_SPEC_LENGTH;
  const dirty = (
    cleanName !== baselineRef.current.name.trim()
    || cleanSpec !== baselineRef.current.jobSpecText.trim()
  );
  dirtyRef.current = dirty;

  // A background refresh may replace the role object while the editor is open.
  // Refresh a pristine form, but never overwrite a draft the recruiter is typing.
  useEffect(() => {
    const incoming = roleSeed(role);
    const switchedRole = incoming.id !== baselineRef.current.id;
    if (!switchedRole && dirtyRef.current) return;
    baselineRef.current = incoming;
    setName(incoming.name);
    setJobSpecText(incoming.jobSpecText);
    setNameTouched(false);
    setSpecTouched(false);
    setMode('write');
  }, [role?.description, role?.id, role?.job_spec_text, role?.name]);

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  useEffect(() => {
    if (!dirty) return undefined;
    const warnBeforeUnload = (event) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warnBeforeUnload);
    return () => window.removeEventListener('beforeunload', warnBeforeUnload);
  }, [dirty]);

  const parsedPreview = useMemo(
    () => parseJobSpec(jobSpecText, name || role?.name || ''),
    [jobSpecText, name, role?.name],
  );

  const markTouched = () => {
    setNameTouched(true);
    setSpecTouched(true);
  };

  const handleSave = async (event) => {
    event?.preventDefault();
    markTouched();
    if (!dirty || !nameValid || !specValid || saving || conflict) return;
    await onSubmit?.({
      name: externallyManagedTitle ? undefined : cleanName,
      jobSpecText: cleanSpec,
    });
  };

  const resolveConflict = (keepDraft) => {
    const latest = roleSeed(role);
    baselineRef.current = latest;
    if (!keepDraft) {
      dirtyRef.current = false;
      setName(latest.name);
      setJobSpecText(latest.jobSpecText);
      setNameTouched(false);
      setSpecTouched(false);
      setMode('write');
    }
    onResolveConflict?.(keepDraft ? 'review-draft' : 'reload-latest');
  };

  const requestCancel = () => {
    if (saving) return;
    if (dirty) {
      setConfirmDiscard(true);
      return;
    }
    onCancel?.();
  };

  const handleEditorKeyDown = (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
      void handleSave(event);
    }
  };

  const specError = specTouched && !specValid
    ? cleanSpec.length < MIN_SPEC_LENGTH
      ? `Add at least ${MIN_SPEC_LENGTH - cleanSpec.length} more character${MIN_SPEC_LENGTH - cleanSpec.length === 1 ? '' : 's'} so the agent has enough context.`
      : `Keep the specification under ${MAX_SPEC_LENGTH.toLocaleString()} characters.`
    : '';

  return (
    <>
      <form className="role-spec-editor" onSubmit={handleSave} onKeyDown={handleEditorKeyDown} noValidate>
        <div className="role-spec-editor-intro">
          <div className="role-spec-editor-source-note">
            <span className="role-spec-editor-source-icon" aria-hidden="true"><Sparkles size={16} /></span>
            <div>
              <strong>{hasLocalOverride ? 'Taali override active' : `${atsName} source connected`}</strong>
              <span>
                {normalizedSource === 'workable' || normalizedSource === 'bullhorn'
                  ? hasLocalOverride
                    ? `${atsName} stays connected, but future syncs will not replace this Taali version.`
                    : `Saving creates a Taali version for screening; future ${atsName} syncs will not overwrite it.`
                  : 'This is the job specification the agent uses to screen and assess candidates.'}
              </span>
            </div>
          </div>
        </div>

        {conflict ? (
          <div className="role-spec-editor-conflict" role="alert">
            <div>
              <strong>A newer job specification is available</strong>
              <span>
                {conflict.changedBy
                  ? `${conflict.changedBy} saved changes while you were editing. `
                  : `${conflict.message || 'Someone saved changes while you were editing.'} `}
                {Number.isInteger(conflict.currentVersion) ? `Version ${conflict.currentVersion} is now current. ` : ''}
                Your draft is safe. Review it, then keep it deliberately or load the latest saved version.
              </span>
            </div>
            <div className="role-spec-editor-conflict-actions">
              <Button type="button" variant="ghost" size="sm" onClick={() => resolveConflict(false)}>
                Discard draft &amp; load latest
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={() => resolveConflict(true)}>
                Keep draft for review
              </Button>
            </div>
          </div>
        ) : null}
        {error ? <div className="role-spec-editor-error" role="alert">{error}</div> : null}

        <div className="role-spec-editor-actions">
          <div className={`role-spec-editor-save-state${dirty ? ' is-dirty' : ''}`} aria-live="polite">
            <span aria-hidden="true" />
            {dirty ? 'Unsaved changes' : 'No unsaved changes'}
          </div>
          <div className="role-spec-editor-action-buttons">
            <Button type="button" variant="ghost" size="sm" onClick={requestCancel} disabled={saving}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              loading={saving}
              loadingLabel="Saving…"
              disabled={!dirty || !nameValid || !specValid || Boolean(conflict)}
            >
              <Save size={14} aria-hidden="true" /> Save job spec
            </Button>
          </div>
        </div>

        <div className="role-spec-editor-field">
          <div className="role-spec-editor-label-row">
            {externallyManagedTitle
              ? <span className="role-spec-editor-label">Role title</span>
              : <label htmlFor="role-spec-name">Role title</label>}
            {externallyManagedTitle ? (
              <span className="role-spec-editor-managed"><Lock size={11} aria-hidden="true" /> Managed in {atsName}</span>
            ) : null}
          </div>
          {externallyManagedTitle ? (
            <div id="role-spec-name" className="role-spec-editor-readonly" aria-label={`Role title, managed in ${atsName}`}>
              {role?.name || 'Untitled role'}
            </div>
          ) : (
            <>
              <Input
                id="role-spec-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                onBlur={() => setNameTouched(true)}
                aria-invalid={nameTouched && !nameValid ? 'true' : undefined}
                aria-describedby={nameTouched && !nameValid ? 'role-spec-name-error' : undefined}
                autoComplete="off"
              />
              {nameTouched && !nameValid ? (
                <p id="role-spec-name-error" className="role-spec-editor-field-error" role="alert">Enter a role title.</p>
              ) : null}
            </>
          )}
        </div>

        <div className="role-spec-editor-document">
          <div className="role-spec-editor-document-head">
            <div>
              <label htmlFor="role-spec-text">Job description</label>
              <p>Use headings and bullet points to make responsibilities and requirements easy to scan.</p>
            </div>
            <TabBar
              tabs={ROLE_SPEC_VIEW_TABS}
              activeTab={mode}
              onChange={setMode}
              ariaLabel="Job description view"
              className="role-spec-editor-tabs"
              density="compact"
              variant="segmented"
            />
          </div>

          <PresenceSwap presenceKey={mode} className="role-spec-editor-mode">
            {mode === 'write' ? (
              <div
                id="role-spec-write-panel"
                role="tabpanel"
                aria-labelledby="role-spec-write-tab"
                className="role-spec-editor-write"
              >
                <Textarea
                  id="role-spec-text"
                  value={jobSpecText}
                  onChange={(event) => setJobSpecText(event.target.value)}
                  onBlur={() => setSpecTouched(true)}
                  placeholder={'## About the role\nDescribe the role, responsibilities, and what success looks like.\n\n## Requirements\n- Add the essential skills and experience'}
                  minLength={MIN_SPEC_LENGTH}
                  maxLength={MAX_SPEC_LENGTH}
                  aria-invalid={specError ? 'true' : undefined}
                  aria-describedby={`role-spec-format-help${specError ? ' role-spec-text-error' : ''}`}
                />
                <div className="role-spec-editor-meta">
                  <span id="role-spec-format-help">Formatting: ## heading · - bullet · **bold**</span>
                  <span className={jobSpecText.length > MAX_SPEC_LENGTH * 0.9 ? 'near-limit' : ''}>
                    {jobSpecText.length.toLocaleString()} / {MAX_SPEC_LENGTH.toLocaleString()}
                  </span>
                </div>
                {specError ? <p id="role-spec-text-error" className="role-spec-editor-field-error" role="alert">{specError}</p> : null}
              </div>
            ) : (
              <div
                id="role-spec-preview-panel"
                role="tabpanel"
                aria-labelledby="role-spec-preview-tab"
                className="role-spec-editor-preview"
              >
                {parsedPreview.sections.length ? (
                  <MotionStagger className="role-sections expanded" data-motion-stagger="job-spec-editor-preview">
                    {parsedPreview.sections.map((section, index) => (
                      <FormattedJobSpecSection
                        key={`${section.title}-${index}`}
                        section={section}
                        marker={String(index + 1).padStart(2, '0')}
                      />
                    ))}
                  </MotionStagger>
                ) : (
                  <div className="role-spec-editor-preview-empty">
                    <Eye size={20} aria-hidden="true" />
                    <strong>Your formatted preview will appear here.</strong>
                    <span>Add a job description in Write mode to get started.</span>
                  </div>
                )}
              </div>
            )}
          </PresenceSwap>
        </div>

      </form>

      <ConfirmActionDialog
        open={confirmDiscard}
        title="Discard your changes?"
        description="Your unsaved job specification edits will be lost."
        warning="This cannot be undone."
        confirmLabel="Discard changes"
        variant="danger"
        onClose={() => setConfirmDiscard(false)}
        onConfirm={() => {
          setConfirmDiscard(false);
          onCancel?.();
        }}
      />
    </>
  );
};

export default RoleSpecEditPanel;
