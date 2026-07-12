import React, { useCallback, useState } from 'react';
import { ChevronDown, Copy, Loader2 } from 'lucide-react';

import { roles as rolesApi } from '../../shared/api';
import { useToast } from '../../context/ToastContext';

// "Source candidates" panel on the role detail page. Everything here produces
// copy-paste artefacts for the recruiter to run by hand on LinkedIn / Google —
// there is NO LinkedIn API, scraping, or automation.
//
// Two tools:
//  - Generate search strings: deterministic Google X-ray + LinkedIn boolean,
//    plus metered "refined" alternates (fail-open — a warning shows if the
//    refinement call fails, the base strings still render).
//  - Draft outreach: paste a profile, pick tone/channel, get a first-touch
//    message grounded in the overlap. No-fabrication warnings surface when the
//    profile is too thin.

function CopyRow({ label, hint, value }) {
  const { showToast } = useToast();
  const handleCopy = useCallback(async () => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      showToast('Copied to clipboard.', 'success');
    } catch (err) {
      showToast('Copy failed — select and copy the text manually.', 'error');
    }
  }, [value, showToast]);

  if (!value) return null;
  return (
    <div className="src-string">
      <div className="src-string-head">
        <span className="src-string-label">{label}</span>
        {hint ? <span className="src-string-hint">{hint}</span> : null}
        <button type="button" className="btn btn-outline btn-sm src-copy" onClick={handleCopy}>
          <Copy size={12} /> Copy
        </button>
      </div>
      <code className="src-string-value">{value}</code>
    </div>
  );
}

function SearchStrings({ data }) {
  if (!data) return null;
  const { deterministic, refined, title_synonyms: synonyms, warning } = data;
  return (
    <div className="src-results">
      {warning ? <div className="src-warn">{warning}</div> : null}
      <CopyRow label="Google X-ray" hint="paste into Google" value={deterministic?.xray} />
      <CopyRow label="LinkedIn boolean" hint="LinkedIn search box" value={deterministic?.boolean} />
      {synonyms?.length ? (
        <div className="src-synonyms">
          <span className="src-string-label">Title synonyms</span>
          <span>{synonyms.join(' · ')}</span>
        </div>
      ) : null}
      {(refined || []).map((alt, i) => (
        <div key={`${alt.label}-${i}`} className="src-refined">
          <div className="src-refined-label">{alt.label}</div>
          <CopyRow label="Google X-ray" hint="paste into Google" value={alt.xray} />
          <CopyRow label="LinkedIn boolean" hint="LinkedIn search box" value={alt.boolean} />
        </div>
      ))}
    </div>
  );
}

function OutreachResult({ draft }) {
  const { showToast } = useToast();
  const handleCopy = useCallback(async () => {
    if (!draft?.body) return;
    const text = draft.subject ? `${draft.subject}\n\n${draft.body}` : draft.body;
    try {
      await navigator.clipboard.writeText(text);
      showToast('Draft copied to clipboard.', 'success');
    } catch (err) {
      showToast('Copy failed — select and copy the text manually.', 'error');
    }
  }, [draft, showToast]);

  if (!draft) return null;
  return (
    <div className="src-draft">
      {(draft.warnings || []).map((w, i) => (
        <div key={i} className="src-warn">{w}</div>
      ))}
      {draft.subject ? <div className="src-draft-subject"><strong>Subject:</strong> {draft.subject}</div> : null}
      {draft.body ? (
        <>
          <pre className="src-draft-body">{draft.body}</pre>
          <button type="button" className="btn btn-outline btn-sm src-copy" onClick={handleCopy}>
            <Copy size={12} /> Copy draft
          </button>
        </>
      ) : null}
    </div>
  );
}

export function SourceCandidatesPanel({ roleId, defaultOpen = false }) {
  const { showToast } = useToast();
  const [open, setOpen] = useState(defaultOpen);

  const [searches, setSearches] = useState(null);
  const [loadingSearches, setLoadingSearches] = useState(false);

  const [profileText, setProfileText] = useState('');
  const [tone, setTone] = useState('warm');
  const [channel, setChannel] = useState('linkedin');
  const [draft, setDraft] = useState(null);
  const [loadingDraft, setLoadingDraft] = useState(false);

  const generateSearches = useCallback(async () => {
    setLoadingSearches(true);
    try {
      const { data } = await rolesApi.sourcingSearches(roleId);
      setSearches(data);
    } catch (err) {
      showToast('Could not generate search strings.', 'error');
    } finally {
      setLoadingSearches(false);
    }
  }, [roleId, showToast]);

  const generateDraft = useCallback(async () => {
    if (!profileText.trim()) {
      showToast('Paste a profile first.', 'error');
      return;
    }
    setLoadingDraft(true);
    try {
      const { data } = await rolesApi.outreachDraft(roleId, {
        profile_text: profileText.trim(),
        tone,
        channel,
      });
      setDraft(data);
    } catch (err) {
      showToast('Could not draft outreach.', 'error');
    } finally {
      setLoadingDraft(false);
    }
  }, [roleId, profileText, tone, channel, showToast]);

  return (
    <div className="role-sec src-panel">
      <button
        type="button"
        className={`src-panel-toggle ${open ? 'open' : ''}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <div className="role-sec-title">
          <span className="marker">SR</span>
          Source candidates
        </div>
        <ChevronDown className="caret" size={12} />
      </button>

      {open ? (
        <div className="src-panel-body">
          <p className="src-help">
            Copy-paste helpers for sourcing on LinkedIn by hand — nothing is sent or automated.
          </p>

          <div className="src-tool">
            <div className="src-tool-head">
              <span className="src-tool-title">Search strings</span>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={generateSearches}
                disabled={loadingSearches}
              >
                {loadingSearches ? <Loader2 className="animate-spin" size={12} /> : null}
                {searches ? 'Regenerate' : 'Generate search strings'}
              </button>
            </div>
            <SearchStrings data={searches} />
          </div>

          <div className="src-tool">
            <div className="src-tool-head">
              <span className="src-tool-title">Draft outreach</span>
            </div>
            <textarea
              className="taali-input src-profile"
              rows={5}
              maxLength={8000}
              placeholder="Paste a candidate's LinkedIn profile or CV text here…"
              value={profileText}
              onChange={(e) => setProfileText(e.target.value)}
            />
            <div className="src-draft-controls">
              <label className="src-select">
                Tone
                <select value={tone} onChange={(e) => setTone(e.target.value)}>
                  <option value="warm">Warm</option>
                  <option value="direct">Direct</option>
                </select>
              </label>
              <label className="src-select">
                Channel
                <select value={channel} onChange={(e) => setChannel(e.target.value)}>
                  <option value="linkedin">LinkedIn</option>
                  <option value="email">Email</option>
                </select>
              </label>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={generateDraft}
                disabled={loadingDraft}
              >
                {loadingDraft ? <Loader2 className="animate-spin" size={12} /> : null}
                {draft ? 'Regenerate' : 'Draft outreach'}
              </button>
            </div>
            <OutreachResult draft={draft} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default SourceCandidatesPanel;
