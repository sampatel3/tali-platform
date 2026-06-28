import React, { useEffect, useMemo, useState } from 'react';
import { Pencil, RotateCcw, Sparkles } from 'lucide-react';

// The live Job spec (JD) panel — the recruiter-facing job-description DOCUMENT.
//
// Renders the org's `jd_template` (a markdown string with {{placeholder}}
// tokens, templated once per org in Settings) and fills the role-specific
// parts on the fly from the captured brief. It re-renders live as the agent
// extracts fields — it's derived purely from props (no extra fetch) — and
// reuses the shared chat-kit markdown renderer so the JD reads like every
// other rendered-markdown surface. Lives inside the same .rq-brief scroll
// shell as the Brief panel.
//
// The recruiter can also OVERRIDE the auto-generated JD per requisition: when
// `brief.jd_override` is a non-empty string we render that verbatim instead of
// the template-filled draft (an "Edited" badge marks it). "Edit" opens a
// full-height markdown editor seeded with the current rendered text; "Reset to
// auto" clears the override and reverts to the live template-filled draft.
import { ChatMarkdown } from '../../shared/chat';

// Markdown shown in place of an empty / missing value so gaps are visible in
// the live draft.
const TBC = '_(to be captured)_';

const isEmpty = (v) => (
  v == null
  || v === ''
  || (Array.isArray(v) && v.length === 0)
);

// Thousands separator for salary numbers; leaves non-numeric input untouched.
const fmtNum = (n) => {
  const num = Number(n);
  return Number.isFinite(num) ? num.toLocaleString('en-US') : String(n);
};

// "AED 20,000–28,000 / year" / "Up to AED 28,000 / year" / "From AED 20,000"
// — currency defaults to AED, the "/ period" suffix is dropped when absent.
const formatSalary = (brief) => {
  const min = brief?.salary_min;
  const max = brief?.salary_max;
  if (isEmpty(min) && isEmpty(max)) return '';
  const currency = (brief?.salary_currency || 'AED');
  const period = brief?.salary_period;
  const suffix = isEmpty(period) ? '' : ` / ${period}`;
  let body;
  if (!isEmpty(min) && !isEmpty(max)) body = `${currency} ${fmtNum(min)}–${fmtNum(max)}`;
  else if (!isEmpty(max)) body = `Up to ${currency} ${fmtNum(max)}`;
  else body = `From ${currency} ${fmtNum(min)}`;
  return `${body}${suffix}`;
};

// Render a list (array of strings) as markdown bullet lines.
const formatList = (value) => {
  if (!Array.isArray(value)) return '';
  const items = value.map((v) => String(v ?? '').trim()).filter(Boolean);
  if (items.length === 0) return '';
  return items.map((it) => `- ${it}`).join('\n');
};

// Build the {{key}} → string resolvers off the current brief. Each resolver
// returns the substituted markdown for that token (already TBC-filled where
// empty); the regex pass below decides empty-handling per token.
const buildResolvers = (brief) => {
  const b = brief || {};
  const custom = b.custom_fields || {};
  const location = [b.location_city, b.location_country].filter(Boolean).join(', ');
  return {
    // scalars
    title: b.title,
    summary: b.summary,
    department: b.department,
    seniority: b.seniority,
    location,
    workplace_type: b.workplace_type,
    employment_type: b.employment_type,
    openings: b.openings,
    salary: formatSalary(b),
    salary_min: b.salary_min,
    salary_max: b.salary_max,
    salary_currency: b.salary_currency,
    urgency: custom.urgency,
    success_profile: b.success_profile,
    target_start: b.target_start,
    // lists → markdown bullet lines
    must_haves: formatList(b.must_haves),
    preferred: formatList(b.preferred),
    dealbreakers: formatList(b.dealbreakers),
    assessment_focus: formatList(b.assessment_focus),
    evp: formatList(b.evp),
    // CUSTOM list field — AI-drafted responsibilities live under
    // custom_fields.responsibilities (NOT a top-level brief column), but render
    // exactly like the other list placeholders (bulleted, TBC when empty).
    responsibilities: formatList(custom.responsibilities),
    // Role-agnostic benefits (custom list field, auto-standardised from recent
    // roles). Tolerate a legacy string value too.
    benefits: Array.isArray(custom.benefits) ? formatList(custom.benefits) : custom.benefits,
    // Role-agnostic "About the company" blurb (auto-derived once per org, copied
    // onto each requisition). Plain text.
    company_description: custom.company_description,
  };
};

// Substitute every {{token}} in the template in a SINGLE regex pass:
//   - known key with a non-empty value → the value (lists already bulleted)
//   - known key that's empty/missing   → TBC marker (gaps stay visible)
//   - unknown token                    → blanked (so it doesn't read as a gap)
// `openings === 0` is a real value, not empty. Robust against odd input.
const substitute = (templateStr, brief) => {
  const resolvers = buildResolvers(brief);
  return String(templateStr || '').replace(/\{\{\s*([\w]+)\s*\}\}/g, (_match, rawKey) => {
    const key = String(rawKey);
    if (!Object.prototype.hasOwnProperty.call(resolvers, key)) return ''; // unknown → blank
    const value = resolvers[key];
    // 0 is meaningful (e.g. openings); only null/''/[] count as empty.
    if (value === 0) return '0';
    return isEmpty(value) ? TBC : String(value);
  });
};

// A non-empty override string?
const hasOverride = (brief) => {
  const o = brief?.jd_override;
  return typeof o === 'string' && o.trim() !== '';
};

// Render the org's JD template against a brief into a substituted markdown
// string — the same template-filled draft the recruiter sees in this panel,
// exported as a pure function so other surfaces (e.g. the Publish handler that
// snapshots the rendered JD onto the public job page) can produce identical
// output without mounting <JobSpec>. Returns '' when there's no template.
export const renderJobSpec = (template, brief) => {
  const tpl = template?.jd_template;
  if (!tpl) return '';
  return substitute(tpl, brief);
};

export function JobSpec({
  template,
  brief,
  onSaveOverride,
  savingOverride = false,
  onDraftResponsibilities,
  draftingResponsibilities = false,
}) {
  // The template-filled draft (live, derived from the brief). Shares the same
  // pure renderer the Publish handler uses, so the panel and the snapshot match.
  const autoMarkdown = useMemo(() => renderJobSpec(template, brief), [template, brief]);

  const override = hasOverride(brief) ? brief.jd_override : null;
  // What's actually shown in view mode: the override if present, else the draft.
  const displayMarkdown = override != null ? override : autoMarkdown;
  const editable = typeof onSaveOverride === 'function';
  const canDraft = typeof onDraftResponsibilities === 'function';

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  // Leave edit mode whenever we switch requisitions (the brief identity
  // changes) so a stale draft can't bleed across documents.
  useEffect(() => { setEditing(false); }, [brief?.id]);

  const startEdit = () => {
    // Seed with the current rendered markdown — the override if set, else the
    // template-filled draft (so editing "starts from" what's on screen).
    setDraft(displayMarkdown);
    setEditing(true);
  };

  const saveEdit = async () => {
    if (savingOverride) return;
    await onSaveOverride(draft);
    setEditing(false);
  };

  const resetToAuto = async () => {
    if (savingOverride) return;
    await onSaveOverride(null);
    setEditing(false);
  };

  const empty = !editing && displayMarkdown.trim() === '';

  return (
    <div className="rq-brief">
      <div className="rq-brief-scroll">
        {editing ? (
          <div className="rq-jobspec-edit">
            <div className="rq-jobspec-bar">
              <span className="rq-jobspec-bar-label">Editing job spec — markdown</span>
              <div className="rq-jobspec-bar-actions">
                <button
                  type="button"
                  className="rq-btn-sm is-ghost"
                  onClick={() => setEditing(false)}
                  disabled={savingOverride}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="rq-btn-sm is-primary"
                  onClick={saveEdit}
                  disabled={savingOverride}
                >
                  {savingOverride ? <span className="rq-spinner" /> : null} Save
                </button>
              </div>
            </div>
            <textarea
              className="rq-jobspec-textarea"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
              autoFocus
              aria-label="Job spec markdown"
            />
          </div>
        ) : empty ? (
          <div className="rq-side-empty">
            No job-spec template configured. Set one up in Settings → Requisition template.
          </div>
        ) : (
          <>
            {editable || canDraft ? (
              <div className="rq-jobspec-bar">
                <div className="rq-jobspec-bar-label">
                  {override != null ? <span className="rq-jobspec-badge">Edited</span> : null}
                  {canDraft ? (
                    <button
                      type="button"
                      className="rq-btn-sm is-ghost rq-draft-btn"
                      onClick={() => onDraftResponsibilities()}
                      disabled={draftingResponsibilities || savingOverride}
                      title="Let the AI draft the “What you’ll do” responsibilities from the brief"
                    >
                      {draftingResponsibilities ? <span className="rq-spinner" /> : <Sparkles size={13} />} Draft responsibilities (AI)
                    </button>
                  ) : null}
                </div>
                {editable ? (
                  <div className="rq-jobspec-bar-actions">
                    {override != null ? (
                      <button
                        type="button"
                        className="rq-btn-sm is-ghost"
                        onClick={resetToAuto}
                        disabled={savingOverride || draftingResponsibilities}
                      >
                        {savingOverride ? <span className="rq-spinner" /> : <RotateCcw size={13} />} Reset to auto
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="rq-btn-sm is-ghost"
                      onClick={startEdit}
                      disabled={savingOverride || draftingResponsibilities}
                    >
                      <Pencil size={13} /> Edit
                    </button>
                  </div>
                ) : null}
              </div>
            ) : null}
            <div className="rq-jobspec">
              <ChatMarkdown>{displayMarkdown}</ChatMarkdown>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default JobSpec;
