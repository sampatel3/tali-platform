import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  ArrowLeft,
  BriefcaseBusiness,
  Check,
  ChevronDown,
  Edit3,
  Loader2,
  MapPin,
  Settings2,
  Share2,
  Sparkles,
  X,
} from 'lucide-react';

import * as apiClient from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import { useJobStatus } from '../../contexts/JobStatusContext';
import {
  Spinner,
  Textarea,
} from '../../shared/ui/TaaliPrimitives';
import { CandidateSheet } from '../candidates/CandidateSheet';
import { CandidatesDirectoryPage } from '../candidates/CandidatesDirectoryPage';
import { candidateReportHref } from '../candidates/CandidateTriageDrawer';
import { RoleSheet } from '../candidates/RoleSheet';
import { getErrorMessage, trimOrUndefined } from '../candidates/candidatesUiUtils';

const EMPTY_PROGRESS = { status: 'idle', total: 0, scored: 0, errors: 0, include_scored: false };
const EMPTY_FETCH_PROGRESS = { status: 'idle', total: 0, fetched: 0, errors: 0 };
const PIPELINE_STAGE_ORDER = [
  { key: 'applied', label: 'Applied', countLabel: 'new' },
  { key: 'invited', label: 'Invited', countLabel: 'awaiting' },
  { key: 'in_assessment', label: 'In assessment', countLabel: 'live' },
  { key: 'review', label: 'Review', countLabel: 'decision' },
];

const normalizeThreshold = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '';
  return String(Math.max(0, Math.min(100, Math.round(numeric))));
};

const formatRelativeShort = (value) => {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  const diffMs = Date.now() - parsed.getTime();
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 60) return `${Math.max(1, minutes)}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
};

const buildApplicationTitle = (application) => (
  application?.candidate_name
  || application?.candidate_email
  || `Candidate #${application?.candidate_id || application?.id || '—'}`
);

const resolveAssessmentId = (application) => (
  application?.score_summary?.assessment_id
  || application?.valid_assessment_id
  || null
);

const resolveOptionalPercent = (value) => {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return Math.max(0, Math.min(100, Math.round(numeric)));
};

const resolvePipelineReviewScore = (application) => {
  const score = Number(
    application?.taali_score
    ?? application?.score_summary?.taali_score
    ?? application?.score_summary?.assessment_score
    ?? application?.pre_screen_score
  );
  return Number.isFinite(score) ? score : null;
};

const resolvePipelineCardSignal = (application) => {
  const stage = String(application?.pipeline_stage || '').toLowerCase();
  if (stage === 'review') {
    const reviewScore = resolvePipelineReviewScore(application);
    return {
      label: reviewScore != null ? formatScore(reviewScore) : '—',
      toneClass: reviewScore != null && reviewScore >= 80 ? 'hi' : '',
    };
  }
  if (stage === 'in_assessment') {
    const normalizedStatus = String(application?.status || '').toLowerCase();
    return {
      label: normalizedStatus.includes('pause') ? '⏸' : '🟢',
      toneClass: '',
    };
  }
  return {
    label: '—',
    toneClass: '',
  };
};

const resolvePipelineCardFooterStatus = (application) => {
  const stage = String(application?.pipeline_stage || '').toLowerCase();
  if (stage === 'applied') return 'Not invited';
  if (stage === 'invited') return 'Awaiting start';
  if (stage === 'in_assessment') return 'Assessment live';
  if (stage === 'review') return 'Decision';
  return resolveAssessmentId(application) ? 'Assessment linked' : 'No task yet';
};

const formatScore = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  return `${Math.round(numeric)}`;
};

const splitCriteriaDraft = (criteriaDraft = '') => String(criteriaDraft || '')
  .split('\n')
  .map((entry) => entry.replace(/^[\s*-]+/, '').trim())
  .filter(Boolean);

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
        meta[key] = stripMarkdownSyntax(content);
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
        meta[key] = stripMarkdownSyntax(metaMatch[2]);
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
  const parts = String(value || '').split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((part, index) => {
    const strong = part.match(/^\*\*([^*]+)\*\*$/);
    if (strong) {
      return <strong key={`${part}-${index}`}>{strong[1]}</strong>;
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

  const items = section.lines
    .map((line) => {
      const bulletMatch = line.match(/^(?:[-•]|\d+[.)])\s+(.+)$/);
      const inferredBullet = !bulletMatch && isStandaloneSpecItem(line);
      return {
        type: bulletMatch || inferredBullet ? 'bullet' : 'paragraph',
        text: bulletMatch ? bulletMatch[1].trim() : line,
      };
    })
    .filter((item) => stripMarkdownSyntax(item.text));

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
        return <p key={`p-${index}`}>{renderSpecInline(block.text)}</p>;
      })}
    </div>
  );
};

export const JobPipelinePage = ({ onNavigate, onViewCandidate, NavComponent = null }) => {
  const { roleId } = useParams();
  const rolesApi = apiClient.roles;
  const tasksApi = 'tasks' in apiClient ? apiClient.tasks : null;
  const { showToast } = useToast();
  const { trackRole } = useJobStatus() ?? {};
  void onViewCandidate;

  const numericRoleId = Number(roleId);
  const [role, setRole] = useState(null);
  const [roleTasks, setRoleTasks] = useState([]);
  const [allTasks, setAllTasks] = useState([]);
  const [roleApplications, setRoleApplications] = useState([]);
  const [batchScoreProgress, setBatchScoreProgress] = useState(EMPTY_PROGRESS);
  const [fetchCvsProgress, setFetchCvsProgress] = useState(EMPTY_FETCH_PROGRESS);
  const [loading, setLoading] = useState(true);
  const [savingRoleConfig, setSavingRoleConfig] = useState(false);
  const [criteriaDraft, setCriteriaDraft] = useState('');
  const [criteriaEditing, setCriteriaEditing] = useState(false);
  const [thresholdDraft, setThresholdDraft] = useState('');
  const [refreshTick, setRefreshTick] = useState(0);
  const [interviewFocusGenerating, setInterviewFocusGenerating] = useState(false);
  const [detailsExpanded, setDetailsExpanded] = useState(false);
  const [activeView, setActiveView] = useState('pipeline');
  const [roleSheetOpen, setRoleSheetOpen] = useState(false);
  const [candidateSheetOpen, setCandidateSheetOpen] = useState(false);
  const [roleSheetError, setRoleSheetError] = useState('');
  const [candidateSheetError, setCandidateSheetError] = useState('');
  const [savingRoleSheet, setSavingRoleSheet] = useState(false);
  const [addingCandidate, setAddingCandidate] = useState(false);

  const loadRoleWorkspace = useCallback(async () => {
    if (!Number.isFinite(numericRoleId)) return;
    setLoading(true);
    try {
      const [roleRes, tasksRes, applicationsRes, batchStatusRes, fetchStatusRes] = await Promise.all([
        rolesApi.get(numericRoleId),
        rolesApi.listTasks(numericRoleId),
        rolesApi.listApplications(numericRoleId, { sort_by: 'pre_screen_score', sort_order: 'desc' }),
        rolesApi.batchScoreStatus(numericRoleId),
        rolesApi.fetchCvsStatus(numericRoleId),
      ]);
      const nextRole = roleRes?.data || null;
      setRole(nextRole);
      setCriteriaDraft(nextRole?.additional_requirements || '');
      setCriteriaEditing(false);
      setThresholdDraft(nextRole?.auto_reject_threshold_100 != null ? String(nextRole.auto_reject_threshold_100) : '');
      setRoleTasks(Array.isArray(tasksRes?.data) ? tasksRes.data : []);
      setRoleApplications(Array.isArray(applicationsRes?.data) ? applicationsRes.data : []);
      setBatchScoreProgress(batchStatusRes?.data || EMPTY_PROGRESS);
      setFetchCvsProgress(fetchStatusRes?.data || EMPTY_FETCH_PROGRESS);
    } catch (error) {
      setRole(null);
      setRoleTasks([]);
      setRoleApplications([]);
      showToast(getErrorMessage(error, 'Failed to load role pipeline.'), 'error');
    } finally {
      setLoading(false);
    }
  }, [numericRoleId, rolesApi, showToast]);

  useEffect(() => {
    void loadRoleWorkspace();
  }, [loadRoleWorkspace]);

  useEffect(() => {
    if (!tasksApi?.list) {
      setAllTasks([]);
      return undefined;
    }
    let cancelled = false;
    const loadAllTasks = async () => {
      try {
        const res = await tasksApi.list();
        if (!cancelled) {
          setAllTasks(Array.isArray(res?.data) ? res.data : []);
        }
      } catch {
        if (!cancelled) {
          setAllTasks([]);
        }
      }
    };
    void loadAllTasks();
    return () => {
      cancelled = true;
    };
  }, [tasksApi]);

  useEffect(() => {
    if (!Number.isFinite(numericRoleId)) return undefined;
    const batchRunning = String(batchScoreProgress?.status || '').toLowerCase() === 'running';
    const fetchRunning = String(fetchCvsProgress?.status || '').toLowerCase() === 'running';
    if (!batchRunning && !fetchRunning) return undefined;

    let cancelled = false;
    const poll = async () => {
      try {
        const [batchStatusRes, fetchStatusRes] = await Promise.all([
          rolesApi.batchScoreStatus(numericRoleId),
          rolesApi.fetchCvsStatus(numericRoleId),
        ]);
        if (cancelled) return;
        const nextBatch = batchStatusRes?.data || EMPTY_PROGRESS;
        const nextFetch = fetchStatusRes?.data || EMPTY_FETCH_PROGRESS;
        const batchWasRunning = String(batchScoreProgress?.status || '').toLowerCase() === 'running';
        const fetchWasRunning = String(fetchCvsProgress?.status || '').toLowerCase() === 'running';
        const batchNowRunning = String(nextBatch.status || '').toLowerCase() === 'running';
        const fetchNowRunning = String(nextFetch.status || '').toLowerCase() === 'running';
        setBatchScoreProgress(nextBatch);
        setFetchCvsProgress(nextFetch);
        if ((batchWasRunning && !batchNowRunning) || (fetchWasRunning && !fetchNowRunning)) {
          await loadRoleWorkspace();
          setRefreshTick((value) => value + 1);
        }
      } catch {
        if (!cancelled) {
          setBatchScoreProgress((current) => ({ ...current, status: current.status || 'failed' }));
          setFetchCvsProgress((current) => ({ ...current, status: current.status || 'failed' }));
        }
      }
    };

    const intervalId = window.setInterval(() => {
      void poll();
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [batchScoreProgress, fetchCvsProgress, loadRoleWorkspace, numericRoleId, rolesApi]);

  const activeApplications = useMemo(() => (
    roleApplications.filter((application) => application?.application_outcome === 'open')
  ), [roleApplications]);

  const unscoredApplications = useMemo(() => (
    activeApplications.filter((application) => application?.cv_match_score == null)
  ), [activeApplications]);

  const thresholdValue = useMemo(
    () => resolveOptionalPercent(role?.auto_reject_threshold_100),
    [role?.auto_reject_threshold_100]
  );
  const currentThresholdValue = useMemo(
    () => (thresholdDraft !== '' ? resolveOptionalPercent(thresholdDraft) : thresholdValue),
    [thresholdDraft, thresholdValue]
  );
  const sliderThresholdValue = currentThresholdValue ?? 60;
  const belowThresholdCount = useMemo(() => {
    if (thresholdValue == null) return 0;
    return activeApplications.filter((application) => {
      const score = Number(application?.pre_screen_score);
      return Number.isFinite(score) && score < thresholdValue;
    }).length;
  }, [activeApplications, thresholdValue]);

  const lastScoredAt = useMemo(() => {
    const scoredDates = activeApplications
      .map((application) => application?.cv_match_scored_at)
      .filter(Boolean)
      .map((value) => new Date(value))
      .filter((value) => !Number.isNaN(value.getTime()))
      .sort((a, b) => b.getTime() - a.getTime());
    return scoredDates[0] || null;
  }, [activeApplications]);

  const pipelineStats = useMemo(() => ([
    {
      key: 'active',
      label: 'In pipeline',
      value: String(role?.active_candidates_count || activeApplications.length || 0),
      description: `${role?.stage_counts?.review || 0} in review`,
      highlight: true,
    },
    {
      key: 'unscored',
      label: 'New CVs',
      value: String(unscoredApplications.length),
      description: unscoredApplications.length > 0 ? 'Ready for score-new-only' : 'All visible CVs scored',
    },
    {
      key: 'below-threshold',
      label: 'Below threshold',
      value: String(belowThresholdCount),
      description: thresholdValue != null ? `Threshold ${thresholdValue}/100` : 'Set a reject threshold',
    },
    {
      key: 'tasks',
      label: 'Assessment tasks',
      value: String(roleTasks.length),
      description: roleTasks.length ? `${roleTasks[0]?.name || 'Task'} linked` : 'Link a task before inviting',
    },
  ]), [activeApplications.length, belowThresholdCount, role, roleTasks.length, thresholdValue, unscoredApplications.length]);

  const groupedApplications = useMemo(() => PIPELINE_STAGE_ORDER.map((stage) => ({
    ...stage,
    items: activeApplications.filter((application) => String(application?.pipeline_stage || '').toLowerCase() === stage.key),
  })), [activeApplications]);

  const roleFitApplications = useMemo(() => (
    [...activeApplications].sort((a, b) => {
      const scoreA = Number(a?.pre_screen_score);
      const scoreB = Number(b?.pre_screen_score);
      return (Number.isFinite(scoreB) ? scoreB : -1) - (Number.isFinite(scoreA) ? scoreA : -1);
    })
  ), [activeApplications]);

  const activityItems = useMemo(() => (
    [...activeApplications]
      .map((application) => ({
        application,
        at: application?.updated_at || application?.created_at,
        stage: String(application?.pipeline_stage || 'applied').replace(/_/g, ' '),
      }))
      .filter((item) => item.at)
      .sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime())
      .slice(0, 12)
  ), [activeApplications]);

  const recruiterCriteria = useMemo(() => splitCriteriaDraft(criteriaDraft), [criteriaDraft]);
  const parsedJobSpec = useMemo(() => parseJobSpec(
    role?.job_spec_text || role?.description || role?.summary || role?.job_summary || '',
    role?.name || ''
  ), [role?.description, role?.job_spec_text, role?.job_summary, role?.name, role?.summary]);
  const roleSummary = useMemo(() => (
    parsedJobSpec.summary
    || String(role?.summary || role?.job_summary || '').trim()
  ), [parsedJobSpec.summary, role?.job_summary, role?.summary]);
  const roleHighlights = useMemo(() => {
    const questions = Array.isArray(role?.interview_focus?.questions) ? role.interview_focus.questions : [];
    const triggers = Array.isArray(role?.interview_focus?.manual_screening_triggers)
      ? role.interview_focus.manual_screening_triggers
      : [];
    const items = [];
    if (role?.workable_job_id) items.push({ title: 'Workable-linked role', description: 'Candidate sync and role metadata stay anchored to your ATS source of truth.' });
    if (recruiterCriteria.length) items.push({ title: 'Recruiter-specific criteria', description: `${recruiterCriteria.length} recruiter requirement${recruiterCriteria.length === 1 ? '' : 's'} shape the CV scoring pass.` });
    if (questions.length) items.push({ title: 'Interview focus ready', description: `${questions.length} generated interview prompts are ready for the hiring loop.` });
    if (triggers.length) items.push({ title: 'Screening triggers', description: triggers.slice(0, 2).join(' · ') });
    if (!items.length) {
      items.push({ title: 'Role workspace', description: 'Tune scoring, review pipeline flow, and move quickly from screening to decision.' });
    }
    return items.slice(0, 4);
  }, [recruiterCriteria.length, role?.interview_focus?.manual_screening_triggers, role?.interview_focus?.questions, role?.workable_job_id]);

  const thresholdHandleStyle = useMemo(() => ({
    left: `${Math.max(0, Math.min(100, Number(sliderThresholdValue || 0)))}%`,
    opacity: currentThresholdValue == null ? 0.72 : 1,
  }), [currentThresholdValue, sliderThresholdValue]);

  const roleFactValues = useMemo(() => ({
    location: role?.location || role?.candidate_location || parsedJobSpec.meta.location || 'Location not captured',
    department: role?.department || parsedJobSpec.meta.department || role?.organization_name || 'Hiring team',
    employment: role?.employment_type || parsedJobSpec.meta.employmentType || 'Full-time',
  }), [parsedJobSpec.meta.department, parsedJobSpec.meta.employmentType, parsedJobSpec.meta.location, role?.candidate_location, role?.department, role?.employment_type, role?.location, role?.organization_name]);

  const belowThresholdDots = useMemo(() => {
    if (!activeApplications.length) return [];
    return activeApplications.map((application) => {
      const score = Number(application?.pre_screen_score);
      const belowThreshold = thresholdValue != null && Number.isFinite(score) && score < thresholdValue;
      return belowThreshold ? 'below' : 'above';
    });
  }, [activeApplications, thresholdValue]);

  const handleBatchScore = async ({ includeScored = false } = {}) => {
    if (!Number.isFinite(numericRoleId)) return;
    try {
      const res = await rolesApi.batchScore(numericRoleId, includeScored ? { include_scored: true } : {});
      const payload = res?.data || EMPTY_PROGRESS;
      setBatchScoreProgress({
        status: payload.status || 'started',
        total: Number(payload.total || payload.total_target || 0),
        scored: Number(payload.scored || 0),
        errors: Number(payload.errors || 0),
        include_scored: Boolean(payload.include_scored || includeScored),
      });
      if (payload.status === 'nothing_to_score') {
        showToast(includeScored ? 'No CVs available to score.' : 'No newly added CVs need scoring.', 'info');
        return;
      }
      // Tell the global panel to start tracking this role immediately,
      // without waiting for the next discovery poll.
      trackRole?.(numericRoleId);
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to start CV scoring.'), 'error');
    }
  };

  const handleFetchCvs = async () => {
    if (!Number.isFinite(numericRoleId)) return;
    try {
      const res = await rolesApi.fetchCvs(numericRoleId);
      setFetchCvsProgress({
        status: res?.data?.status || 'started',
        total: Number(res?.data?.total || 0),
        fetched: Number(res?.data?.fetched || 0),
        errors: Number(res?.data?.errors || 0),
      });
      // No success toast — the persistent BackgroundJobsToaster surfaces
      // the fetch progress in the bottom-right.
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to fetch CVs from Workable.'), 'error');
    }
  };

  const handleSaveRoleConfig = async () => {
    if (!Number.isFinite(numericRoleId)) return;
    setSavingRoleConfig(true);
    try {
      await rolesApi.update(numericRoleId, {
        additional_requirements: criteriaDraft.trim() || null,
        auto_reject_threshold_100: thresholdDraft === '' ? null : Number(normalizeThreshold(thresholdDraft)),
      });
      await loadRoleWorkspace();
      setRefreshTick((value) => value + 1);
      setCriteriaEditing(false);
      showToast('Role scoring guidance updated.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save role scoring guidance.'), 'error');
    } finally {
      setSavingRoleConfig(false);
    }
  };

  const handleRoleSheetSubmit = async ({
    name,
    description,
    additionalRequirements,
    autoRejectEnabled,
    autoRejectThreshold100,
    autoRejectNoteTemplate,
    jobSpecFile,
    taskIds,
  }) => {
    if (!Number.isFinite(numericRoleId)) return;
    setSavingRoleSheet(true);
    setRoleSheetError('');
    try {
      await rolesApi.update(numericRoleId, {
        name,
        description: trimOrUndefined(description),
        additional_requirements: trimOrUndefined(additionalRequirements),
        auto_reject_enabled: autoRejectEnabled ? true : null,
        auto_reject_threshold_100: autoRejectEnabled ? autoRejectThreshold100 : null,
        auto_reject_note_template: autoRejectEnabled ? trimOrUndefined(autoRejectNoteTemplate) : null,
      });

      if (jobSpecFile && rolesApi.uploadJobSpec) {
        await rolesApi.uploadJobSpec(numericRoleId, jobSpecFile);
      }

      const nextTaskIds = new Set((taskIds || []).map((value) => Number(value)));
      const currentTaskIds = new Set((roleTasks || []).map((task) => Number(task.id)));

      if (rolesApi.addTask) {
        for (const taskId of nextTaskIds) {
          if (!currentTaskIds.has(taskId)) {
            await rolesApi.addTask(numericRoleId, taskId);
          }
        }
      }
      if (rolesApi.removeTask) {
        for (const taskId of currentTaskIds) {
          if (!nextTaskIds.has(taskId)) {
            await rolesApi.removeTask(numericRoleId, taskId);
          }
        }
      }

      if (jobSpecFile && rolesApi.regenerateInterviewFocus) {
        try {
          await rolesApi.regenerateInterviewFocus(numericRoleId);
        } catch {
          // Keep edit flow resilient if interview-focus generation is temporarily unavailable.
        }
      }

      setRoleSheetOpen(false);
      await loadRoleWorkspace();
      setRefreshTick((value) => value + 1);
      showToast('Role updated.', 'success');
    } catch (error) {
      setRoleSheetError(getErrorMessage(error, 'Failed to save role.'));
    } finally {
      setSavingRoleSheet(false);
    }
  };

  const handleCandidateSubmit = async ({ email, name, position, cvFile }) => {
    if (!Number.isFinite(numericRoleId) || !rolesApi.createApplication) return;
    setAddingCandidate(true);
    setCandidateSheetError('');
    try {
      const res = await rolesApi.createApplication(numericRoleId, {
        candidate_email: email,
        candidate_name: name,
        candidate_position: trimOrUndefined(position),
      });
      if (cvFile && rolesApi.uploadApplicationCv && res?.data?.id) {
        await rolesApi.uploadApplicationCv(res.data.id, cvFile);
      }
      setCandidateSheetOpen(false);
      setActiveView('table');
      await loadRoleWorkspace();
      setRefreshTick((value) => value + 1);
      showToast('Candidate added to this role.', 'success');
    } catch (error) {
      setCandidateSheetError(getErrorMessage(error, 'Failed to add candidate.'));
    } finally {
      setAddingCandidate(false);
    }
  };

  const handleShareRole = async () => {
    const shareUrl = `${window.location.origin}/jobs/${numericRoleId}`;
    try {
      await navigator.clipboard.writeText(shareUrl);
      showToast('Role pipeline link copied.', 'success');
    } catch {
      showToast('Copy failed. Copy the URL from your browser instead.', 'error');
    }
  };

  const handleOpenRoleSettings = () => {
    document.getElementById('role-scoring-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const viewCandidateReport = useCallback((application) => {
    if (!application?.id) return;
    const navOptions = { candidateApplicationId: application.id };
    if (Number.isFinite(numericRoleId)) {
      navOptions.fromRoleId = numericRoleId;
    }
    onNavigate('candidate-report', navOptions);
  }, [numericRoleId, onNavigate]);

  const handlePipelineReportClick = useCallback((event, application) => {
    if (
      event.defaultPrevented
      || event.metaKey
      || event.ctrlKey
      || event.shiftKey
      || event.altKey
      || event.button !== 0
    ) {
      return;
    }
    event.preventDefault();
    viewCandidateReport(application);
  }, [viewCandidateReport]);

  const handleRegenerateInterviewFocus = async () => {
    if (!Number.isFinite(numericRoleId)) return;
    setInterviewFocusGenerating(true);
    try {
      await rolesApi.regenerateInterviewFocus(numericRoleId);
      await loadRoleWorkspace();
      showToast('Interview focus regenerated.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to regenerate interview focus.'), 'error');
    } finally {
      setInterviewFocusGenerating(false);
    }
  };

  if (loading && !role) {
    return (
      <div>
        {NavComponent ? <NavComponent currentPage="jobs" onNavigate={onNavigate} /> : null}
        <div className="page">
          <div className="flex min-h-[280px] items-center justify-center">
            <Spinner size={22} />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="jobs" onNavigate={onNavigate} /> : null}
      <div className="page">
        <button type="button" className="pipeline-back" onClick={() => onNavigate('jobs')}>
          <ArrowLeft size={10} />
          All roles
        </button>

        <div className="role-hero">
          <div className="watermark">{String(role?.name || 'ROLE').replace(/[^A-Z0-9]/gi, '').slice(0, 3).toUpperCase() || 'ROL'}</div>
          <div className="role-hero-top">
            <div>
              <div className="role-meta-line">
                <span className="kicker">ROLE · #{role?.id || '—'}</span>
                <span className="chip green">Active · hiring</span>
                <span className="chip">{role?.source === 'workable' ? 'Synced from Workable' : 'Created in Taali'}</span>
              </div>
              <h1>{role?.name}<em>.</em></h1>
              <div className="role-facts">
                <div className="f"><span className="k">Location</span><span className="v">{roleFactValues.location}</span></div>
                <div className="f"><span className="k">Department</span><span className="v">{roleFactValues.department}</span></div>
                <div className="f"><span className="k">Employment</span><span className="v">{roleFactValues.employment}</span></div>
                <div className="f"><span className="k">Assessment</span><span className="v purple">{roleTasks[0]?.name || 'Task not linked'}</span></div>
                <div className="f"><span className="k">Updated</span><span className="v">{formatRelativeShort(role?.updated_at || role?.created_at)}</span></div>
              </div>
            </div>

            <div className="role-actions">
              <div className="row">
                <button type="button" className="icon-btn" title="Share role" onClick={handleShareRole}>
                  <Share2 size={15} />
                </button>
                <button type="button" className="icon-btn" title="Role settings" onClick={handleOpenRoleSettings}>
                  <Settings2 size={15} />
                </button>
              </div>
              <div className="row">
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => {
                    setRoleSheetError('');
                    setRoleSheetOpen(true);
                  }}
                >
                  Edit role
                </button>
                <button
                  type="button"
                  className="btn btn-purple btn-sm"
                  onClick={() => {
                    setCandidateSheetError('');
                    setCandidateSheetOpen(true);
                  }}
                >
                  Invite candidate <span className="arrow">→</span>
                </button>
              </div>
            </div>
          </div>

          <div className="role-desc">
            <div className="role-desc-main">
              {!detailsExpanded && roleSummary ? (
                <p className="role-desc-summary">{roleSummary}</p>
              ) : null}

              <button
                type="button"
                className={`desc-toggle ${detailsExpanded ? 'open' : ''}`}
                onClick={() => setDetailsExpanded((current) => !current)}
              >
                <span>{detailsExpanded ? 'Hide full description' : 'Read full description'}</span>
                <ChevronDown className="caret" size={10} />
              </button>

              <div className={`role-sections ${detailsExpanded ? 'expanded' : ''}`}>
                <div className="role-spec-source">
                  {role?.source === 'workable' ? 'Workable ingested job spec' : 'Role job spec'}
                  {parsedJobSpec.meta.applyUrl ? (
                    <a href={parsedJobSpec.meta.applyUrl} target="_blank" rel="noreferrer">Open source posting</a>
                  ) : null}
                </div>
                {parsedJobSpec.sections.length ? parsedJobSpec.sections.map((section, index) => (
                  <FormattedJobSpecSection
                    key={`${section.title}-${index}`}
                    section={section}
                    marker={String(index + 1).padStart(2, '0')}
                  />
                )) : (
                  <div className="role-sec">
                    <div className="role-sec-title"><span className="marker">01</span>About the role</div>
                    <p>{roleSummary || 'This recruiter workspace mirrors the job spec, scoring guidance, and active pipeline for the role.'}</p>
                  </div>
                )}
                {recruiterCriteria.length ? (
                  <div className="role-sec">
                    <div className="role-sec-title">
                      <span className="marker">{String((parsedJobSpec.sections.length || 1) + 1).padStart(2, '0')}</span>
                      Recruiter requirements
                    </div>
                    <ul>
                      {recruiterCriteria.map((criterion, index) => (
                        <li key={`${criterion}-${index}`}>{criterion}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            </div>

            <div className="role-highlights">
              <h4>At a glance</h4>
              {roleHighlights.map((item) => (
                <div key={item.title} className="hi">
                  <div className="icon"><BriefcaseBusiness size={13} /></div>
                  <div>
                    <div className="t">{item.title}</div>
                    <div className="d">{item.description}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="score-panel" id="role-scoring-panel">
          <div className="score-action">
            <div className="sa-icon">
              <Sparkles size={20} />
            </div>
            <div>
              <div className="sa-label">CV scoring · manual trigger</div>
              <div className="sa-headline">Score {activeApplications.length} CVs against this role&apos;s criteria</div>
              <div className="sa-meta">
                Last run: {lastScoredAt ? lastScoredAt.toLocaleString() : 'not yet scored'} ·
                {' '}
                {batchScoreProgress?.status === 'running'
                  ? `${batchScoreProgress.scored}/${batchScoreProgress.total} scored`
                  : `${unscoredApplications.length} new since last run`}
              </div>
            </div>
            <div className="sa-actions">
              {role?.source === 'workable' ? (
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={handleFetchCvs}
                  disabled={String(fetchCvsProgress?.status || '').toLowerCase() === 'running'}
                  title="Fetch CVs from Workable for any candidates missing one. Required before scoring works."
                >
                  {String(fetchCvsProgress?.status || '').toLowerCase() === 'running' ? (
                    <>
                      <Loader2 size={13} className="animate-spin" />
                      Fetching {fetchCvsProgress.fetched}/{fetchCvsProgress.total}
                    </>
                  ) : (
                    <>Fetch CVs</>
                  )}
                </button>
              ) : null}
              {unscoredApplications.length > 0 ? (
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => handleBatchScore({ includeScored: false })}
                  disabled={String(batchScoreProgress?.status || '').toLowerCase() === 'running'}
                >
                  Score {unscoredApplications.length} new only
                </button>
              ) : null}
              <button
                type="button"
                className="btn btn-purple btn-sm"
                onClick={() => handleBatchScore({ includeScored: true })}
                disabled={String(batchScoreProgress?.status || '').toLowerCase() === 'running'}
              >
                {String(batchScoreProgress?.status || '').toLowerCase() === 'running' ? (
                  <>
                    <Loader2 size={13} className="animate-spin" />
                    Re-scoring {batchScoreProgress.scored}/{batchScoreProgress.total}
                  </>
                ) : (
                  <>
                    Re-score all {activeApplications.length} CVs
                  </>
                )}
              </button>
            </div>
          </div>

          <div className="score-grid">
            <div className="sp-col">
              <div className="criteria-head">
                <div>
                  <h3>Scoring <em>criteria</em></h3>
                  <p className="sp-sub">Every CV on this role is scored against the job spec plus the recruiter guidance saved here.</p>
                </div>
                <div className="criteria-actions">
                  {criteriaEditing ? (
                    <>
                      <button
                        type="button"
                        className="btn btn-outline btn-sm"
                        onClick={() => {
                          setCriteriaDraft(role?.additional_requirements || '');
                          setCriteriaEditing(false);
                        }}
                      >
                        <X size={13} />
                        Cancel
                      </button>
                      <button
                        type="button"
                        className="btn btn-purple btn-sm"
                        onClick={handleSaveRoleConfig}
                        disabled={savingRoleConfig}
                      >
                        <Check size={13} />
                        {savingRoleConfig ? 'Saving…' : 'Save criteria'}
                      </button>
                    </>
                  ) : (
                    <button type="button" className="btn btn-outline btn-sm" onClick={() => setCriteriaEditing(true)}>
                      <Edit3 size={13} />
                      Edit criteria
                    </button>
                  )}
                </div>
              </div>
              <div className="req-list">
                {recruiterCriteria.length ? recruiterCriteria.map((criterion, index) => (
                  <div key={`${criterion}-${index}`} className="req-item source-rec">
                    <span className="num">{String(index + 1).padStart(2, '0')}</span>
                    <div>{criterion}</div>
                    <span className="tag-src">Recruiter</span>
                  </div>
                )) : (
                  <div className="req-item source-jd">
                    <span className="num">01</span>
                    <div>Use the job spec and linked task as the default scoring source until recruiter criteria are added.</div>
                    <span className="tag-src">Job spec</span>
                  </div>
                )}
              </div>
              <div className={`criteria-input ${criteriaEditing ? 'editing' : ''}`}>
                <div className="criteria-input-label">
                  Recruiter scoring requirements
                  <span>{criteriaEditing ? 'Editable' : 'Saved guidance'}</span>
                </div>
                <Textarea
                  value={criteriaDraft}
                  onChange={(event) => setCriteriaDraft(event.target.value)}
                  readOnly={!criteriaEditing}
                  className="criteria-textarea min-h-[180px]"
                  placeholder={`One requirement per line. Prefix with the priority so the AI weighs it correctly.

Examples:
Must have: 5+ years building data pipelines on AWS
Preferred: Banking or fintech background
Nice to have: AWS Solutions Architect certification
Constraint: Based in UAE (no remote)
Disqualifying: No experience with regulated financial data`}
                />
                <div className="criteria-input-foot">
                  {criteriaEditing
                    ? 'Saved criteria are sent to the next CV scoring or re-score run.'
                    : 'Use Edit criteria to add role-specific requirements before scoring CVs.'}
                </div>
              </div>
            </div>

            <div className="sp-col">
              <h3>Reject <em>threshold</em></h3>
              <p className="sp-sub">Below-threshold candidates are flagged for faster review and bulk rejection. Nothing is auto-rejected.</p>
              <div className={`thr-wrap ${currentThresholdValue == null ? 'unset' : ''}`}>
                <div className="thr-label">Below this → flag for rejection</div>
                <div className="thr-big">
                  <span>{currentThresholdValue != null ? currentThresholdValue : '—'}</span>
                  <span className="sign">%</span>
                </div>
                <div className="thr-slider">
                  <div className="thr-track" aria-hidden="true">
                    <div className="track" />
                    <div className="handle" style={thresholdHandleStyle} />
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={sliderThresholdValue}
                    onChange={(event) => setThresholdDraft(normalizeThreshold(event.target.value))}
                    className="thr-range"
                    aria-label="Reject threshold"
                  />
                </div>
                <div className="thr-ticks">
                  <span>0%</span><span>25%</span><span>50%</span><span>75%</span><span>100%</span>
                </div>
                <div style={{ marginTop: '18px' }}>
                  <div className="thr-label">Pipeline distribution</div>
                  <div className="thr-dots">
                    {belowThresholdDots.map((dot, index) => (
                      <div key={`${dot}-${index}`} className={`d ${dot}`} />
                    ))}
                  </div>
                </div>
                <p className="thr-caption">
                  <b>{belowThresholdCount} of {activeApplications.length} candidates</b> in the pipeline currently score below
                  {' '}
                  {thresholdValue != null ? `${thresholdValue}%` : 'the saved threshold'}.
                </p>
                <div className="row" style={{ marginTop: '12px', justifyContent: 'space-between' }}>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => setActiveView('table')}>
                    View below-threshold candidates →
                  </button>
                  <button
                    type="button"
                    className="btn btn-outline btn-sm"
                    onClick={handleSaveRoleConfig}
                    disabled={savingRoleConfig}
                  >
                    {savingRoleConfig ? 'Saving…' : 'Save threshold'}
                  </button>
                </div>
                {/* Fetch CVs button moved up next to the Re-score buttons. */}
              </div>
            </div>
          </div>
        </div>

        <div className="stat-row">
          {pipelineStats.map((item) => (
            <div key={item.key} className={`stat ${item.highlight ? 'hi' : ''}`}>
              <div className="k">{item.label}</div>
              <div className="v">{item.value}</div>
              <div className="d">{item.description}</div>
            </div>
          ))}
          <div className="stat">
            <div className="k">Interview focus</div>
            <div className="v">{Array.isArray(role?.interview_focus?.questions) ? role.interview_focus.questions.length : 0}</div>
            <div className="d">Generated prompts</div>
          </div>
        </div>

        <div className="sub-tabs">
          <div className="seg">
            <button type="button" className={activeView === 'pipeline' ? 'active' : ''} onClick={() => setActiveView('pipeline')}>Pipeline</button>
            <button type="button" className={activeView === 'table' ? 'active' : ''} onClick={() => setActiveView('table')}>Candidates table</button>
            <button type="button" className={activeView === 'role-fit' ? 'active' : ''} onClick={() => setActiveView('role-fit')}>Role fit</button>
            <button type="button" className={activeView === 'activity' ? 'active' : ''} onClick={() => setActiveView('activity')}>Activity</button>
          </div>
          <div className="row">
            <div className="filter-chip"><span className="mono">Filter</span> · All stages</div>
            <div className="filter-chip"><span className="mono">Sort</span> · Composite</div>
            <div className="pipeline-invite-hint">Use the candidates table to select a candidate and send a Taali assessment.</div>
            <button type="button" className="btn btn-outline btn-sm" onClick={() => setActiveView('table')}>
              Open candidates table
            </button>
          </div>
        </div>

        {activeView === 'pipeline' ? (
          <div className="pipeline-layout">
            <div className="kanban">
              {groupedApplications.map((stage) => {
                const visibleItems = stage.items.slice(0, 3);
                const hiddenCount = Math.max(0, stage.items.length - visibleItems.length);
                return (
                  <div key={stage.key} className="kanban-col" data-stage={stage.key}>
                    <div className="kanban-col-head">
                      <div className="title"><span className="dot" />{stage.label}</div>
                      <div className="count">{stage.items.length} · {stage.countLabel}</div>
                    </div>
                    {visibleItems.map((application) => {
                      const cardSignal = resolvePipelineCardSignal(application);
                      return (
                        <a
                          key={application.id}
                          className="kanban-card text-left"
                          href={candidateReportHref(application, numericRoleId)}
                          onClick={(event) => handlePipelineReportClick(event, application)}
                        >
                          <div className="cc-top">
                            <div className="av">{buildApplicationTitle(application).slice(0, 2).toUpperCase()}</div>
                            <div>
                              <div className="n">{buildApplicationTitle(application)}</div>
                              <div className="e">{application?.candidate_email || 'No email captured'}</div>
                            </div>
                            <div className={`sc ${cardSignal.toneClass}`}>{cardSignal.label}</div>
                          </div>
                          <div className="cc-meta">
                            <span className={`tag ${application?.workable_sourced ? 'a' : ''}`}>{application?.workable_sourced ? 'Workable' : 'Taali'}</span>
                            {application?.candidate_location ? (
                              <span className="tag"><MapPin size={10} />{application.candidate_location}</span>
                            ) : null}
                          </div>
                          <div className="cc-foot">
                            <span>{formatRelativeShort(application?.created_at || application?.updated_at)}</span>
                            <span>{resolvePipelineCardFooterStatus(application)}</span>
                          </div>
                        </a>
                      );
                    })}
                    {hiddenCount > 0 ? (
                      <button type="button" className="kanban-card more" onClick={() => setActiveView('table')}>
                        + {hiddenCount} more
                      </button>
                    ) : null}
                  </div>
                );
              })}
            </div>

            {/* Role-level interview focus panel removed — interview guidance is per-candidate now,
                surfaced in the candidate score sheet (kit + screening pack). */}
          </div>
        ) : activeView === 'role-fit' ? (
          <div className="role-fit-view">
            <div className="role-fit-list">
              <div className="role-fit-head">
                <div>
                  <h3>Role <em>fit</em></h3>
                  <p>CV match sorted against this role&apos;s job spec and saved recruiter criteria.</p>
                </div>
                <button type="button" className="btn btn-outline btn-sm" onClick={() => handleBatchScore({ includeScored: true })}>
                  Re-score role fit
                </button>
              </div>
              {roleFitApplications.slice(0, 12).map((application) => {
                const score = Number(application?.pre_screen_score);
                const scoreLabel = Number.isFinite(score) ? Math.round(score) : null;
                const belowThreshold = thresholdValue != null && scoreLabel != null && scoreLabel < thresholdValue;
                return (
                  <button
                    key={application.id}
                    type="button"
                    className="role-fit-row"
                    onClick={() => onNavigate('candidate-report', {
                      candidateApplicationId: application.id,
                      ...(Number.isFinite(numericRoleId) ? { fromRoleId: numericRoleId } : {}),
                    })}
                  >
                    <div className="av">{buildApplicationTitle(application).slice(0, 2).toUpperCase()}</div>
                    <div className="rf-main">
                      <div className="n">{buildApplicationTitle(application)}</div>
                      <div className="e">{application?.candidate_email || application?.candidate_position || 'No email captured'}</div>
                    </div>
                    <div className="rf-score">
                      <span>{scoreLabel != null ? `${scoreLabel}%` : '—'}</span>
                      <div className="mini-bar"><i style={{ width: `${scoreLabel != null ? Math.max(3, Math.min(100, scoreLabel)) : 0}%` }} /></div>
                    </div>
                    <div className={`rf-status ${belowThreshold ? 'warn' : 'ok'}`}>
                      {belowThreshold ? 'Below threshold' : 'In range'}
                    </div>
                  </button>
                );
              })}
            </div>
            <div className="role-fit-side">
              <h4>Scoring source</h4>
              <p>The role fit score uses the ingested job spec plus the recruiter-specific requirements saved above.</p>
              <div className="req-list">
                {(recruiterCriteria.length ? recruiterCriteria : ['No recruiter-specific requirements saved yet.']).slice(0, 4).map((criterion, index) => (
                  <div key={`${criterion}-${index}`} className={`req-item ${recruiterCriteria.length ? 'source-rec' : 'source-jd'}`}>
                    <span className="num">{String(index + 1).padStart(2, '0')}</span>
                    <div>{criterion}</div>
                    <span className="tag-src">{recruiterCriteria.length ? 'Recruiter' : 'Job spec'}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : activeView === 'activity' ? (
          <div className="activity-view">
            <div className="activity-head">
              <div>
                <h3>Pipeline <em>activity</em></h3>
                <p>Recent candidate movement and scoring updates for this role.</p>
              </div>
              <button type="button" className="btn btn-outline btn-sm" onClick={loadRoleWorkspace}>
                Refresh activity
              </button>
            </div>
            <div className="activity-timeline">
              {activityItems.length ? activityItems.map((item, index) => (
                <button
                  key={`${item.application.id}-${item.at}-${index}`}
                  type="button"
                  className="activity-item"
                  onClick={() => onNavigate('candidate-report', {
                    candidateApplicationId: item.application.id,
                    ...(Number.isFinite(numericRoleId) ? { fromRoleId: numericRoleId } : {}),
                  })}
                >
                  <span className="dot" />
                  <span className="when">{formatRelativeShort(item.at)}</span>
                  <span className="what">
                    <b>{buildApplicationTitle(item.application)}</b>
                    {' moved in '}
                    {item.stage}
                  </span>
                  <span className="score">
                    {Number.isFinite(Number(item.application?.pre_screen_score))
                      ? `${formatScore(item.application.pre_screen_score)}/100`
                      : 'Not scored'}
                  </span>
                </button>
              )) : (
                <div className="activity-empty">No activity captured for this role yet.</div>
              )}
            </div>
          </div>
        ) : (
          <CandidatesDirectoryPage
            onNavigate={onNavigate}
            NavComponent={null}
            lockRoleId={roleId || null}
            useRolePipelineEndpoint
            navCurrentPage="jobs"
            title=""
            subtitle=""
            externalRefreshKey={refreshTick}
            embedded
          />
        )}

        <RoleSheet
          open={roleSheetOpen}
          mode="edit"
          role={role}
          roleTasks={roleTasks}
          allTasks={allTasks}
          saving={savingRoleSheet}
          error={roleSheetError}
          onClose={() => setRoleSheetOpen(false)}
          onSubmit={handleRoleSheetSubmit}
        />

        <CandidateSheet
          open={candidateSheetOpen}
          role={role}
          saving={addingCandidate}
          error={candidateSheetError}
          onClose={() => setCandidateSheetOpen(false)}
          onSubmit={handleCandidateSubmit}
        />
      </div>
    </div>
  );
};

export default JobPipelinePage;
