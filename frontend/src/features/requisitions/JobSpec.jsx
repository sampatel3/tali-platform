import React, { useMemo } from 'react';

// The live Job spec (JD) panel — the recruiter-facing job-description DOCUMENT.
//
// Renders the org's `jd_template` (a markdown string with {{placeholder}}
// tokens, templated once per org in Settings) and fills the role-specific
// parts on the fly from the captured brief. It re-renders live as the agent
// extracts fields — it's derived purely from props (no extra fetch) — and
// reuses the shared chat-kit markdown renderer so the JD reads like every
// other rendered-markdown surface. Lives inside the same .rq-brief scroll
// shell as the Brief panel.
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

export function JobSpec({ template, brief }) {
  const jdMarkdown = useMemo(() => {
    const tpl = template?.jd_template;
    if (!tpl) return '';
    return substitute(tpl, brief);
  }, [template, brief]);

  return (
    <div className="rq-brief">
      <div className="rq-brief-scroll">
        {jdMarkdown.trim() === '' ? (
          <div className="rq-side-empty">
            No job-spec template configured. Set one up in Settings → Requisition template.
          </div>
        ) : (
          <div className="rq-jobspec">
            <ChatMarkdown>{jdMarkdown}</ChatMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}

export default JobSpec;
