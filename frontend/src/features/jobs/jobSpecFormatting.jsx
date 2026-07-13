// Job-spec parsing + formatted rendering for the role detail page.
//
// Workable (and uploaded) job specs arrive as one flattened, markdown-ish
// blob. parseJobSpec() normalizes that into { title, meta, summary, sections }
// with canonical Description / Requirements / Benefits buckets, and
// FormattedJobSpecSection renders a single parsed section as headed paragraphs
// and bullet lists. Extracted verbatim from JobPipelinePage.jsx to keep the
// page file under the frontend architecture line cap.
import React from 'react';

const SPEC_META_LABELS = {
  application: 'applyUrl',
  location: 'location',
  department: 'department',
  'employment type': 'employmentType',
  employment: 'employmentType',
  apply: 'applyUrl',
  'application url': 'applyUrl',
  'job application': 'applyUrl',
  state: 'state',
  'full title': 'fullTitle',
  title: 'fullTitle',
};

const WORKABLE_SECTION_LABELS = [
  'about',
  'benefits',
  'benefits & growth opportunities',
  'candidate profile',
  'candidate requirements',
  'description',
  'experience',
  'full description',
  'job description',
  'key responsibilities',
  'must have',
  'nice to have',
  'qualifications',
  'requirements',
  'responsibilities',
  'role overview',
  'skills',
  'what we offer',
  'what you will do',
  'who you are',
  'you will',
  'your responsibilities',
];

const SPEC_SECTION_ORDER = ['Description', 'Requirements', 'Benefits'];

const SPEC_SECTION_ALIASES = new Map([
  ['about', 'Description'],
  ['about the role', 'Description'],
  ['description', 'Description'],
  ['full description', 'Description'],
  ['job description', 'Description'],
  ['overview', 'Description'],
  ['role overview', 'Description'],
  ['candidate profile', 'Requirements'],
  ['candidate requirements', 'Requirements'],
  ['experience', 'Requirements'],
  ['must have', 'Requirements'],
  ['nice to have', 'Requirements'],
  ['qualifications', 'Requirements'],
  ['requirements', 'Requirements'],
  ['skills', 'Requirements'],
  ['what you bring', 'Requirements'],
  ['who you are', 'Requirements'],
  ['key responsibilities', 'Description'],
  ['responsibilities', 'Description'],
  ['what you will do', 'Description'],
  ['you will', 'Description'],
  ['your responsibilities', 'Description'],
  ['benefits', 'Benefits'],
  ['benefits & growth opportunities', 'Benefits'],
  ['perks', 'Benefits'],
  ['what we offer', 'Benefits'],
]);

const stripMarkdownSyntax = (value = '') => String(value || '')
  .replace(/^#{1,6}\s+/gm, '')
  .replace(/\*\*([^*]+)\*\*/g, '$1')
  .replace(/\*([^*]+)\*/g, '$1')
  .replace(/^\*+/, '')
  .replace(/\*+$/, '')
  .replace(/\s+/g, ' ')
  .trim();

const normalizeSectionTitle = (value = '') => stripMarkdownSyntax(value)
  .replace(/[:：]+$/g, '')
  .replace(/\s+/g, ' ')
  .trim();

const canonicalSpecSectionTitle = (value = '') => SPEC_SECTION_ALIASES.get(normalizeSectionTitle(value).toLowerCase()) || '';

const safeExternalUrl = (value = '') => {
  const candidate = stripMarkdownSyntax(value);
  if (!candidate) return '';
  try {
    const url = new URL(candidate);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
  } catch {
    return '';
  }
};

const setSpecMeta = (meta, key, value) => {
  const cleanValue = stripMarkdownSyntax(value);
  if (!key || !cleanValue) return;
  if (key === 'applyUrl') {
    const safeUrl = safeExternalUrl(cleanValue);
    if (safeUrl) meta[key] = safeUrl;
    return;
  }
  meta[key] = cleanValue;
};

const isLikelySectionTitle = (value = '') => {
  const label = normalizeSectionTitle(value);
  if (!label) return false;
  const lowered = label.toLowerCase();
  if (SPEC_META_LABELS[lowered]) return false;
  if (WORKABLE_SECTION_LABELS.some((hint) => lowered.includes(hint))) return true;
  const words = label.split(/\s+/).filter(Boolean);
  if (words.length > 12) return false;
  if (/^(http|https|www)\b/i.test(label)) return false;
  return /^[A-Z0-9]/.test(label) && !/[.!?]$/.test(label);
};

const splitKnownSectionHeading = (headingText = '') => {
  const cleanHeading = normalizeSectionTitle(headingText);
  const lowered = cleanHeading.toLowerCase();
  const match = WORKABLE_SECTION_LABELS
    .map((label) => ({ label, index: lowered.indexOf(label) }))
    .filter(({ index }) => index === 0)
    .sort((a, b) => b.label.length - a.label.length)[0];

  if (!match) return null;

  const title = cleanHeading.slice(0, match.label.length).trim();
  const rest = cleanHeading.slice(match.label.length).trim();
  return { title: canonicalSpecSectionTitle(title) || title, rest };
};

const truncateSentence = (value = '', maxLength = 430) => {
  const text = stripMarkdownSyntax(value);
  if (text.length <= maxLength) return text;
  const truncated = text.slice(0, maxLength).replace(/\s+\S*$/, '').trim();
  return `${truncated}…`;
};

const SUMMARY_STOP_WORDS = new Set([
  'and',
  'for',
  'from',
  'into',
  'of',
  'our',
  'the',
  'this',
  'through',
  'to',
  'with',
  'within',
  'you',
  'your',
]);

const tokenizeSummaryText = (value = '') => stripMarkdownSyntax(value)
  .toLowerCase()
  .replace(/[^a-z0-9]+/g, ' ')
  .split(/\s+/)
  .filter((token) => token.length > 2 && !SUMMARY_STOP_WORDS.has(token));

const isSummaryParagraph = (line = '') => {
  const text = stripMarkdownSyntax(line);
  if (!text || /^[-•]\s+/.test(line) || getSpecSectionCue(text)) return false;
  const words = text.split(/\s+/).filter(Boolean);
  if (text.length < 80 || words.length < 12) return false;
  if (/^[A-Z]{2,}\d{2,}$/i.test(text)) return false;
  return /[.!?]$/.test(text);
};

const scoreSummaryParagraph = (line = '', roleName = '', index = 0) => {
  if (!isSummaryParagraph(line)) return Number.NEGATIVE_INFINITY;

  const text = stripMarkdownSyntax(line);
  const lowered = text.toLowerCase();
  const normalizedRoleName = stripMarkdownSyntax(roleName).toLowerCase();
  const roleTokens = [...new Set(tokenizeSummaryText(roleName))];
  const lineTokens = new Set(tokenizeSummaryText(text));
  const roleTokenMatches = roleTokens.filter((token) => lineTokens.has(token)).length;

  let score = 0;
  if (normalizedRoleName && lowered.includes(normalizedRoleName)) score += 90;
  score += roleTokenMatches * 18;
  if (roleTokens.length && roleTokenMatches / roleTokens.length >= 0.5) score += 24;
  if (/\b(the|this)\s+(role|position)\b/i.test(text)) score += 26;
  if (/\b(responsible|accountability|serve|primary link|core member|leadership|delivery excellence|end-to-end)\b/i.test(text)) score += 22;
  if (/\b(company|consultancy|clients|organizations|partner|we deliver|we empower|our clients)\b/i.test(text)) score -= 16;
  return score - index;
};

const selectRoleSummary = (lines = [], roleName = '') => {
  const candidates = lines
    .map((line, index) => ({
      line,
      index,
      score: scoreSummaryParagraph(line, roleName, index),
    }))
    .filter((item) => Number.isFinite(item.score))
    .sort((a, b) => b.score - a.score);

  if (candidates.length) {
    const strongest = candidates[0];
    if (strongest.score > 0) return strongest.line;
  }

  return lines.find(isSummaryParagraph) || lines.find((line) => !/^[-•]\s+/.test(line)) || '';
};

const normalizeSpecText = (raw = '') => String(raw || '')
  .replace(/\r\n/g, '\n')
  .replace(/\\n/g, '\n')
  .replace(/\s+(#{1,6}\s+)/g, '\n$1')
  .replace(/\s+##\s+/g, '\n## ')
  .replace(/\s+\*\*([^*\n]{2,120}):\*\*/g, '\n**$1:**')
  .replace(/\s+\*\*((?:Description|Full Description|Job Description|Requirements|Candidate Requirements|Must Have|Nice to Have|Benefits|Benefits & Growth Opportunities|What We Offer))\*\*/gi, '\n**$1**')
  .replace(/\*\*((?:Description|Full Description|Job Description|Requirements|Candidate Requirements|Must Have|Nice to Have|Benefits|Benefits & Growth Opportunities|What We Offer))\*\*\s+/gi, '**$1**\n')
  .replace(/\s+[-•]\s+(?=[A-Z0-9])/g, '\n- ')
  .replace(/\s+(\d+[.)])\s+(?=[A-Z0-9])/g, '\n$1 ')
  .replace(/\n{3,}/g, '\n\n')
  .trim();

const expandSpecLine = (line = '') => String(line || '')
  .replace(/\s+\*\*([A-Z][^*]{2,90})\*\*\s*[-–—]\s+/g, '\n- **$1** — ')
  .replace(/\s+[-–—]\s+([A-Z][A-Za-z&/() ,.-]{2,80}:)/g, '\n- $1')
  .split('\n')
  .map((entry) => entry.trim())
  .filter(Boolean);

const isSpecMetadataLine = (line = '') => {
  const text = stripMarkdownSyntax(line).trim().toLowerCase();
  return /^(location|department|employment type|employment|apply|application|application url|job application|state|full title|title)\s*:/.test(text);
};

const specContentKey = (value = '') => stripMarkdownSyntax(value)
  .toLowerCase()
  .replace(/[^a-z0-9]+/g, ' ')
  .trim();

const specLineLabel = (line = '') => normalizeSectionTitle(String(line || '')
  .replace(/^[-•]\s+/, '')
  .replace(/^\*\*([^*]{2,140})\*\*$/g, '$1'));

const getSpecSectionCue = (line = '') => {
  const label = specLineLabel(line);
  const lowered = label.toLowerCase();
  const canonical = canonicalSpecSectionTitle(label);

  if (canonical) {
    return { title: canonical, keepLine: false };
  }

  if (/^(to be successful\b|you'?ll need\b|you will need\b|it would be great if\b|ideal candidate\b|essential requirements?\b|desired requirements?\b)/i.test(label)) {
    return { title: 'Requirements', keepLine: true };
  }

  if (/^(benefits?\b|benefits & growth opportunities\b|what we offer\b|perks\b|compensation\b|rewards\b)/i.test(label)) {
    return { title: 'Benefits', keepLine: !/^(benefits?|benefits & growth opportunities|what we offer|perks)$/i.test(lowered) };
  }

  return null;
};

const canonicalizeSpecSections = (sections = []) => {
  const buckets = {
    Description: [],
    Requirements: [],
    Benefits: [],
  };
  const seen = new Set();

  sections.forEach((section) => {
    let activeTitle = canonicalSpecSectionTitle(section.title) || 'Description';
    section.lines.forEach((line) => {
      if (!line || isSpecMetadataLine(line)) return;
      const sectionCue = getSpecSectionCue(line);
      if (sectionCue) {
        activeTitle = sectionCue.title;
        if (!sectionCue.keepLine) return;
      }
      const key = specContentKey(line);
      if (!key || seen.has(key)) return;
      seen.add(key);
      buckets[activeTitle].push(line);
    });
  });

  return SPEC_SECTION_ORDER.map((title) => ({
    title,
    lines: buckets[title],
  })).filter((section) => section.lines.length);
};

const parseJobSpec = (raw = '', roleName = '') => {
  const normalized = normalizeSpecText(raw);
  const lines = normalized.split('\n').map((line) => line.trim()).filter(Boolean);
  const meta = {};
  const sections = [];
  let title = '';
  let currentSection = null;

  const ensureSection = (sectionTitle = 'Description') => {
    const titleLabel = normalizeSectionTitle(sectionTitle) || 'Description';
    if (!currentSection || currentSection.title !== titleLabel) {
      currentSection = { title: titleLabel, lines: [] };
      sections.push(currentSection);
    }
    return currentSection;
  };

  lines.forEach((line) => {
    const headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const headingText = stripMarkdownSyntax(headingMatch[2]);
      if (level === 1) {
        title = headingText;
        currentSection = null;
        return;
      }

      const splitKnownHeading = splitKnownSectionHeading(headingText);
      if (splitKnownHeading?.rest) {
        currentSection = { title: splitKnownHeading.title, lines: [splitKnownHeading.rest] };
        sections.push(currentSection);
        return;
      }

      const canonicalHeadingTitle = canonicalSpecSectionTitle(headingText);
      if (canonicalHeadingTitle) {
        currentSection = { title: canonicalHeadingTitle, lines: [] };
        sections.push(currentSection);
        return;
      }

      ensureSection('Description').lines.push(`**${normalizeSectionTitle(headingText) || 'Role detail'}**`);
      return;
    }

    const boldOnlyMatch = line.match(/^\*\*([^*]{2,140})\*\*$/);
    if (boldOnlyMatch) {
      const canonicalLabel = canonicalSpecSectionTitle(boldOnlyMatch[1]);
      if (canonicalLabel) {
        currentSection = { title: canonicalLabel, lines: [] };
        sections.push(currentSection);
        return;
      }
    }

    const lineSectionCue = getSpecSectionCue(line);
    if (lineSectionCue && !lineSectionCue.keepLine) {
      currentSection = { title: lineSectionCue.title, lines: [] };
      sections.push(currentSection);
      return;
    }

    const boldLabelMatch = line.match(/^\*\*([^:*]{2,120}):\*\*\s*(.*)$/);
    if (boldLabelMatch) {
      const label = normalizeSectionTitle(boldLabelMatch[1]);
      const content = boldLabelMatch[2]?.trim() || '';
      const key = SPEC_META_LABELS[label.toLowerCase()];
      if (key && content) {
        setSpecMeta(meta, key, content);
        return;
      }
      const canonicalLabel = canonicalSpecSectionTitle(label);
      if (canonicalLabel) {
        currentSection = { title: canonicalLabel, lines: content ? [content] : [] };
        sections.push(currentSection);
        return;
      }
      if (isLikelySectionTitle(label)) {
        ensureSection(currentSection?.title || 'Description').lines.push(
          content ? `**${label}** ${content}` : `**${label}**`
        );
        return;
      }
    }

    const metaMatch = line.match(/^([A-Za-z][A-Za-z\s]{2,60}):\s*(.+)$/);
    if (metaMatch) {
      const key = SPEC_META_LABELS[metaMatch[1].toLowerCase().trim()];
      if (key) {
        setSpecMeta(meta, key, metaMatch[2]);
        return;
      }
      const label = normalizeSectionTitle(metaMatch[1]);
      const content = metaMatch[2].trim();
      const canonicalLabel = canonicalSpecSectionTitle(label);
      if (canonicalLabel) {
        currentSection = { title: canonicalLabel, lines: [content] };
        sections.push(currentSection);
        return;
      }
      if (isLikelySectionTitle(label)) {
        ensureSection(currentSection?.title || 'Description').lines.push(`**${label}** ${content}`);
        return;
      }
    }

    if (!currentSection) {
      ensureSection('Description');
    }
    currentSection.lines.push(line);
  });

  const cleanedSections = sections
    .map((section) => ({
      title: canonicalSpecSectionTitle(section.title) || normalizeSectionTitle(section.title) || 'Description',
      lines: section.lines.flatMap(expandSpecLine),
    }))
    .filter((section) => section.lines.some((line) => stripMarkdownSyntax(line)));

  const canonicalSections = canonicalizeSpecSections(cleanedSections);
  const descriptionSection = canonicalSections.find((section) => section.title === 'Description') || canonicalSections[0];
  const summarySource = selectRoleSummary(descriptionSection?.lines || [], roleName || title);

  return {
    title,
    meta,
    summary: summarySource ? truncateSentence(summarySource) : '',
    sections: canonicalSections,
  };
};

const renderSpecInline = (value = '') => {
  const parts = String(value || '')
    .split(/(\*\*\*[^*\n]+\*\*\*|\*\*[^*\n]+\*\*|\*[^*\n]+\*)/g)
    .filter(Boolean);
  return parts.map((part, index) => {
    const strongEmphasis = part.match(/^\*\*\*([^*]+)\*\*\*$/);
    if (strongEmphasis) {
      return (
        <strong key={`${part}-${index}`}>
          <em>{strongEmphasis[1]}</em>
        </strong>
      );
    }
    const strong = part.match(/^\*\*([^*]+)\*\*$/);
    if (strong) {
      return <strong key={`${part}-${index}`}>{strong[1]}</strong>;
    }
    const emphasis = part.match(/^\*([^*]+)\*$/);
    if (emphasis) {
      return <em key={`${part}-${index}`}>{emphasis[1]}</em>;
    }
    return <React.Fragment key={`${part}-${index}`}>{part}</React.Fragment>;
  });
};

const FormattedJobSpecSection = ({ section, marker }) => {
  const isStandaloneSpecItem = (line = '') => {
    const text = stripMarkdownSyntax(line);
    const words = text.split(/\s+/).filter(Boolean);
    if (!text || words.length > 9 || text.length > 90) return false;
    if (/[.!?:;]$/.test(text)) return false;
    if (getSpecSectionCue(text)) return false;
    if (/^[A-Z0-9]/.test(text)) return true;
    const titleCasedWords = words.filter((word) => /^[A-Z0-9&/()+-]/.test(word));
    return titleCasedWords.length >= Math.max(1, Math.ceil(words.length * 0.7));
  };

  const isStandaloneSpecSubheading = (line = '') => {
    const match = String(line || '').match(/^(\*{1,3})(.+)\1$/);
    if (!match) return false;
    const text = stripMarkdownSyntax(match[2]);
    const words = text.split(/\s+/).filter(Boolean);
    return Boolean(text) && words.length <= 12 && text.length <= 120 && !/[.!?:;]$/.test(text);
  };

  const classifiedItems = section.lines
    .map((line) => {
      const bulletMatch = line.match(/^(?:[-•]|\d+[.)])\s+(.+)$/);
      if (bulletMatch) {
        return { type: 'bullet', text: bulletMatch[1].trim() };
      }
      if (isStandaloneSpecSubheading(line)) {
        return { type: 'subheading', text: line };
      }
      return {
        type: isStandaloneSpecItem(line) ? 'bulletCandidate' : 'paragraph',
        text: line,
      };
    })
    .filter((item) => stripMarkdownSyntax(item.text));

  // Flattened specs sometimes lose list markers entirely. Only infer a list
  // when there is evidence of one: a run of multiple short items, or a short
  // item immediately following a colon/semicolon lead-in. This keeps codes and
  // isolated subheads such as "DL34" out of accidental one-item lists.
  const items = [];
  for (let index = 0; index < classifiedItems.length;) {
    const item = classifiedItems[index];
    if (item.type !== 'bulletCandidate') {
      items.push(item);
      index += 1;
      continue;
    }
    let end = index;
    while (classifiedItems[end]?.type === 'bulletCandidate') end += 1;
    const candidates = classifiedItems.slice(index, end);
    const previous = items[items.length - 1];
    const followsListLeadIn = previous?.type === 'paragraph' && /[:;]\s*$/.test(stripMarkdownSyntax(previous.text));
    const candidateType = candidates.length > 1 || followsListLeadIn ? 'bullet' : 'paragraph';
    candidates.forEach((candidate) => items.push({ ...candidate, type: candidateType }));
    index = end;
  }

  const blocks = [];
  let pendingBullets = [];
  const flushBullets = () => {
    if (!pendingBullets.length) return;
    blocks.push({ type: 'bullets', items: pendingBullets });
    pendingBullets = [];
  };

  items.forEach((item) => {
    if (item.type === 'bullet') {
      pendingBullets.push(item);
      return;
    }
    flushBullets();
    blocks.push(item);
  });
  flushBullets();

  return (
    <div className="role-sec">
      <div className="role-sec-title"><span className="marker">{marker}</span>{section.title}</div>
      {blocks.map((block, index) => {
        if (block.type === 'bullets') {
          return (
            <ul key={`b-${index}`} className="role-spec-list">
              {block.items.map((item, itemIndex) => (
                <li key={`${item.text}-${itemIndex}`}>{renderSpecInline(item.text)}</li>
              ))}
            </ul>
          );
        }
        if (block.type === 'subheading') {
          return <h4 key={`h-${index}`} className="role-spec-subheading">{renderSpecInline(block.text)}</h4>;
        }
        return <p key={`p-${index}`}>{renderSpecInline(block.text)}</p>;
      })}
    </div>
  );
};

export { parseJobSpec, FormattedJobSpecSection };
