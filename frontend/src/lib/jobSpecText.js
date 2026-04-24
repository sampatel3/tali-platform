const decodeEntities = (value) => String(value || '')
  .replace(/&nbsp;/gi, ' ')
  .replace(/&amp;/gi, '&')
  .replace(/&lt;/gi, '<')
  .replace(/&gt;/gi, '>')
  .replace(/&quot;/gi, '"');

export const stripHtml = (value) => decodeEntities(String(value || '')
  .replace(/<br\s*\/?>/gi, '\n')
  .replace(/<\/p>/gi, '\n\n')
  .replace(/<\/div>/gi, '\n')
  .replace(/<li[^>]*>/gi, '\n- ')
  .replace(/<\/li>/gi, '')
  .replace(/<[^>]+>/g, ' '));

export const stripEmbeddedReprs = (value) => {
  let next = String(value || '');
  let previous = '';

  while (next !== previous) {
    previous = next;
    next = next
      .replace(/\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}/g, '')
      .replace(/\[[^[\]]*(?:\[[^[\]]*\][^[\]]*)*\]/g, '');
  }

  return next;
};

const stripMarkdownDecorators = (value) => String(value || '')
  .replace(/^#{1,6}\s*/gm, '')
  .replace(/\*\*(.*?)\*\*/g, '$1')
  .replace(/__(.*?)__/g, '$1')
  .replace(/`([^`]+)`/g, '$1')
  .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1')
  .replace(/^\s*[-*]\s+/gm, '- ')
  .replace(/^\s*\d+\.\s+/gm, '')
  .replace(/\r/g, '')
  .replace(/[ \t]+/g, ' ')
  .replace(/\n{3,}/g, '\n\n')
  .trim();

export const normalizeJobSpecText = (value) => {
  const raw = String(value || '').trim();
  if (!raw) return '';

  const withoutHtml = raw.includes('<') ? stripHtml(raw) : raw;
  const withoutReprs = stripEmbeddedReprs(withoutHtml);

  return stripMarkdownDecorators(withoutReprs)
    .replace(/https?:\/\/\S+/gi, '')
    .replace(/\s+\n/g, '\n')
    .replace(/\n\s+/g, '\n')
    .replace(/\bApply:\s*/gi, '')
    .replace(/\bState:\s*/gi, '')
    .trim();
};

export const splitJobSpecParagraphs = (value) => normalizeJobSpecText(value)
  .split(/\n{2,}/)
  .map((paragraph) => paragraph.replace(/\n+/g, ' ').trim())
  .filter(Boolean);

export const extractJobSpecFacts = (value) => {
  const text = normalizeJobSpecText(value);
  if (!text) return [];

  const matchValue = (label) => {
    const pattern = new RegExp(`${label}\\s*:?\\s*([^\\n]+)`, 'i');
    const match = text.match(pattern);
    return match?.[1]?.split(/\s{2,}|(?:\s+-\s+)|(?:\s+\*\*)/)[0]?.trim() || '';
  };

  return [
    ['Location', matchValue('location')],
    ['Department', matchValue('department')],
    ['Employment', matchValue('employment type')],
  ].filter(([, content]) => content);
};

export const buildJobSpecPreview = (role) => {
  const sourceText = String(role?.job_spec_text || role?.description || '').trim();
  const normalized = normalizeJobSpecText(sourceText);
  const paragraphs = splitJobSpecParagraphs(sourceText);
  const facts = extractJobSpecFacts(sourceText);
  const metadataLabels = ['location:', 'department:', 'employment type:', 'apply:', 'state:'];
  const descriptiveParagraphs = paragraphs.filter((paragraph) => {
    const lower = paragraph.toLowerCase();
    if (!lower) return false;
    if (metadataLabels.some((label) => lower.startsWith(label))) return false;
    if (lower.includes('workable.com')) return false;
    if (lower.length < 40 && facts.some(([, value]) => value && lower.includes(String(value).toLowerCase()))) return false;
    return true;
  });
  const lead = descriptiveParagraphs[0] || paragraphs[0] || '';

  return {
    text: normalized,
    facts,
    lead,
    supporting: descriptiveParagraphs.slice(1, 3),
  };
};
