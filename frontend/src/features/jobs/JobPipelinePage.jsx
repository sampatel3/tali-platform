import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  BriefcaseBusiness,
  Check,
  ChevronDown,
  Edit3,
  Loader2,
  Share2,
  Sparkles,
  X,
} from 'lucide-react';

import * as apiClient from '../../shared/api';
import { prefetchDocumentBlob } from '../../shared/api/documentCache';
import { useToast } from '../../context/ToastContext';
import { useJobStatus } from '../../contexts/JobStatusContext';
import { Spinner } from '../../shared/ui/TaaliPrimitives';
import { BreadcrumbsRow } from '../../shared/ui/Breadcrumbs';
import { CopyLinkButton } from '../../shared/ui/CopyLinkButton';
import { readCache, writeCache } from '../../shared/api/resourceCache';
import { RoleViewTabs, useRoleView } from './RoleViewTabs';
import { useRoleProgressPolling } from './useRoleProgressPolling';
import { ConfirmActionDialog } from '../../shared/ui/ConfirmActionDialog';
import CriteriaEditor from '../../shared/ui/CriteriaEditor';
import RecruiterAnswersLog from './RecruiterAnswersLog';
import RoleFeedbackNotes from './RoleFeedbackNotes';
import { ProcessCandidatesDialog } from './ProcessCandidatesDialog';
import AgentActivityLog from './AgentActivityLog';
import { useAgentStatus } from '../../shared/layout/AgentBar';
import { AgentHeader, buildAgentPropFromStatus } from '../../shared/layout/AgentHeader';
// AgentRail (the legacy left "cockpit rail") was retired with the v3
// role detail layout — top AgentBar replaces it. Component file stays
// in the tree until any other surface that may import it is also
// migrated; remove that import here to avoid unused-import warnings.
import { BackgroundJobsToaster } from '../candidates/BackgroundJobsToaster';
import { CandidateSheet } from '../candidates/CandidateSheet';
// CandidatesDirectoryPage is no longer embedded on the role detail —
// the Candidates tab now renders a canvas-spec inline ctable directly.
// Standalone /candidates route still uses the directory.
import { CandidateTriageDrawer, candidateReportHref } from '../candidates/CandidateTriageDrawer';
import { useCandidateTriage } from './useCandidateTriage';
import { RoleSheet } from '../candidates/RoleSheet';
import { getErrorMessage, trimOrUndefined, formatStatusLabel, renderJobPipelineScoreCell } from '../candidates/candidatesUiUtils';

const EMPTY_PROGRESS = { status: 'idle', total: 0, scored: 0, errors: 0, include_scored: false };
const EMPTY_FETCH_PROGRESS = { status: 'idle', total: 0, fetched: 0, errors: 0 };
const EMPTY_PRE_SCREEN_PROGRESS = { status: 'idle', total: 0, processed: 0, errors: 0, refresh: false };
const EMPTY_CONFIRM = { open: false, action: null, bullets: [], loading: false, dryRunLoading: false };
const PIPELINE_STAGE_ORDER = [
  { key: 'applied', label: 'Applied', countLabel: 'new' },
  { key: 'invited', label: 'Invited', countLabel: 'awaiting' },
  { key: 'in_assessment', label: 'In assessment', countLabel: 'live' },
  { key: 'review', label: 'Review', countLabel: 'decision' },
  { key: 'advanced', label: 'Advanced', countLabel: 'with recruiter' },
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

const resolvePipelineCardFooterStatus = (application) => {
  const stage = String(application?.pipeline_stage || '').toLowerCase();
  if (stage === 'applied') return 'Not invited';
  if (stage === 'invited') return 'Awaiting start';
  if (stage === 'in_assessment') return 'Assessment live';
  if (stage === 'review') return 'Decision';
  return resolveAssessmentId(application) ? 'Assessment linked' : 'No task yet';
};

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

// RoleAgentSettingsTab — merged Agent settings panel per HANDOFF v2 §4.3.
// Hero banner with ON/OFF + CV scoring criteria editor + reject threshold +
// pipeline-distribution dot grid + autonomy toggles, with a sticky sidebar
// for budget / must-haves / pause threshold / audit footer.
const RoleAgentSettingsTab = ({
  role,
  agentStatus = null,
  roleCriteria,
  workspaceCriteria,
  criteriaBusy,
  criteriaSyncing,
  criteriaResetting,
  onCreateCriterion,
  onUpdateCriterion,
  onDeleteCriterion,
  onSyncCriteria,
  onResetCriteria,
  onRestoreHiddenCriterion,
  thresholdDraft,
  setThresholdDraft,
  thresholdValue,
  recruiterCriteria,
  activeApplications,
  belowThresholdCount,
  savingRoleConfig,
  usageBreakdown,
  onSave,
  onScrollToReview,
  onSaveBudget,
  onAutonomyChange,
  thresholdMode,
  onThresholdModeChange,
  suggestedThreshold,
  savingThresholdMode,
}) => {
  const total = activeApplications.length;
  const above = Math.max(0, total - belowThresholdCount);
  const sliderValue = thresholdDraft !== '' ? Number(thresholdDraft) : (thresholdValue ?? 55);
  const thresholdDisplay = Math.max(0, Math.min(100, sliderValue));
  const mustHaves = Array.isArray(role?.must_haves) ? role.must_haves : [];
  // Budget cap is the role.monthly_usd_budget_cents column on the role
  // record; live spend comes from /roles/{id}/agent/status. Default cap
  // ($50) only applies when the role hasn't set one yet.
  const monthlyBudgetCents = Number(
    agentStatus?.monthly_budget_cents
    ?? role?.monthly_usd_budget_cents
    ?? 5000
  );
  const monthlySpentCents = Number(agentStatus?.monthly_spent_cents ?? 0);
  const budgetPct = monthlyBudgetCents > 0
    ? Math.min(100, Math.round((monthlySpentCents / monthlyBudgetCents) * 100))
    : 0;
  const fmtUsd = (cents) => `$${Math.round((Number(cents) || 0) / 100)}`;
  const dayOfMonth = new Date().getDate();
  const daysInMonth = new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0).getDate();
  const projectedCents = dayOfMonth ? Math.round((monthlySpentCents * daysInMonth) / dayOfMonth) : monthlySpentCents;
  // Two real HITL toggles, persisted on the role record. Default off
  // (= every candidate-affecting decision goes to the Decision Hub for
  // human approval). Flipping on lets the agent execute that family of
  // actions immediately and audit-log the result.
  const autoReject = Boolean(role?.auto_reject);
  const autoPromote = Boolean(role?.auto_promote);
  const handleAutonomyToggle = (key, value) => {
    if (typeof onAutonomyChange === 'function') onAutonomyChange(key, value);
  };

  // Per-role monthly budget editor — HANDOFF v2 §4.3 wants
  // "Monthly cap $50 · Edit" in the budget sidebar. Falls back to
  // the org default of $50 when the role hasn't set one.
  const [budgetEditing, setBudgetEditing] = React.useState(false);
  const [budgetDraftDollars, setBudgetDraftDollars] = React.useState('');
  const [budgetSaving, setBudgetSaving] = React.useState(false);
  const monthlyBudgetDollars = Math.round(monthlyBudgetCents / 100);
  const startBudgetEdit = () => {
    setBudgetDraftDollars(String(monthlyBudgetDollars));
    setBudgetEditing(true);
  };
  const cancelBudgetEdit = () => {
    setBudgetEditing(false);
    setBudgetDraftDollars('');
  };
  const submitBudgetEdit = async () => {
    if (!onSaveBudget) {
      setBudgetEditing(false);
      return;
    }
    const parsed = Number(budgetDraftDollars);
    if (!Number.isFinite(parsed) || parsed < 0) return;
    setBudgetSaving(true);
    try {
      await onSaveBudget(parsed);
      setBudgetEditing(false);
    } finally {
      setBudgetSaving(false);
    }
  };

  return (
    <div className="mc-agent-settings">
      <div className="mc-agent-settings-main">
        {/* Configure-only header. The on/off toggle and live state live
            in the AgentHeader banner at the top of every role page —
            having a second toggle here was a confusing duplicate. This
            tab is purely "configure how the agent runs when it's on." */}
        <section className="mc-agent-settings-intro">
          <div className="mc-kicker">HOW THE AGENT RUNS THIS ROLE</div>
          <p className="mc-agent-settings-intro-help">
            Overrides your <a href="#org-defaults" style={{ color: 'var(--purple)' }}>org defaults</a> for this role only. Configure scoring, threshold, autonomy, and budget here. To turn the agent on, off, or pause it, use the agent panel at the top of this page.
          </p>
        </section>

        {/* Recruiter intent for this role */}
        <section className="mc-agent-settings-card">
          <div className="mc-agent-settings-card-head">
            <div>
              <h2 className="mc-agent-settings-card-title">
                Role <em>criteria</em>
              </h2>
              <p className="mc-agent-settings-card-help">
                Add, edit, or remove chips freely — this role inherits from workspace defaults and you can customize per role. <strong>Sync workspace</strong> pulls in workspace updates without losing chips you've added here. <strong>Reset</strong> drops your customizations and re-snapshots workspace.
              </p>
            </div>
          </div>
          <CriteriaEditor
            mode="role"
            criteria={roleCriteria}
            workspaceCriteria={workspaceCriteria}
            suppressedIds={Array.isArray(role?.suppressed_org_criterion_ids) ? role.suppressed_org_criterion_ids : []}
            busy={criteriaBusy}
            syncing={criteriaSyncing}
            resetting={criteriaResetting}
            onCreate={onCreateCriterion}
            onUpdate={onUpdateCriterion}
            onDelete={onDeleteCriterion}
            onSync={onSyncCriteria}
            onReset={onResetCriteria}
            onRestoreHidden={onRestoreHiddenCriterion}
          />
        </section>

        {/* Standing recruiter feedback to the agent — append-only log;
            recent entries inline into the agent's system prompt. */}
        <RoleFeedbackNotes roleId={role?.id} />

        {/* Q&A history with the agent — recent answers to the agent's
            role-config questions (must-haves, threshold, budget). Hidden
            entirely when there's no history. */}
        <RecruiterAnswersLog roleId={role?.id} />

        {/* Reject threshold */}
        <section className="mc-agent-settings-card">
          <div className="mc-agent-settings-card-head">
            <div>
              <h2 className="mc-agent-settings-card-title">
                Reject <em>threshold</em>
              </h2>
              <p className="mc-agent-settings-card-help">
                Below this CV score, candidates are flagged for bulk reject. Nothing auto-rejects without your approval unless autonomy is enabled below.
              </p>
            </div>
            <div className="mc-agent-settings-threshold-display">
              {thresholdMode === 'auto' && suggestedThreshold?.value != null ? suggestedThreshold.value : thresholdDisplay}
              <span className="mc-agent-settings-threshold-pct">%</span>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--ink)' }}>
              <span className="kicker mute">MODE</span>
              <select
                className="rq-select"
                value={thresholdMode}
                onChange={(event) => onThresholdModeChange?.(event.target.value)}
                aria-label="Threshold mode"
                disabled={savingThresholdMode}
              >
                <option value="manual">Manual</option>
                <option value="auto">Auto · agent-optimised</option>
              </select>
            </label>
            {thresholdMode === 'auto' && suggestedThreshold?.rationale ? (
              <span style={{ fontSize: 12, color: 'var(--mute)', flex: 1, minWidth: 0 }}>
                {suggestedThreshold.rationale}
              </span>
            ) : null}
          </div>
          <div className="mc-agent-settings-slider">
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={thresholdMode === 'auto' && suggestedThreshold?.value != null ? suggestedThreshold.value : thresholdDisplay}
              onChange={(event) => setThresholdDraft(event.target.value)}
              aria-label="Reject threshold percent"
              className="ce-range mc-agent-settings-slider-input"
              style={{ '--ce-range-val': thresholdMode === 'auto' && suggestedThreshold?.value != null ? suggestedThreshold.value : thresholdDisplay }}
              disabled={thresholdMode === 'auto'}
            />
            <div className="mc-agent-settings-slider-scale">
              <span>0%</span><span>25%</span><span>50%</span><span>75%</span><span>100%</span>
            </div>
          </div>
          {total > 0 ? (
            <>
              <div className="mc-kicker is-mute" style={{ marginTop: 18, marginBottom: 12 }}>
                PIPELINE DISTRIBUTION · {total} SCORED
              </div>
              <div className="mc-agent-settings-dotgrid">
                {Array.from({ length: total }).map((_, i) => (
                  <span
                    key={i}
                    className={`mc-agent-settings-dot ${i < belowThresholdCount ? 'is-below' : 'is-above'}`}
                    aria-hidden="true"
                  />
                ))}
              </div>
              <div className="mc-agent-settings-distribution-summary">
                <span>
                  <b style={{ color: '#dc2626' }}>{belowThresholdCount}</b> below threshold ·{' '}
                  <b style={{ color: '#16a34a' }}>{above}</b> above
                </span>
                {belowThresholdCount > 0 ? (
                  <button type="button" className="btn btn-ghost btn-sm" onClick={onScrollToReview}>
                    Review the {belowThresholdCount} →
                  </button>
                ) : null}
              </div>
            </>
          ) : (
            <p className="mc-agent-settings-card-help" style={{ marginTop: 18 }}>
              Pipeline distribution will populate once candidates are scored.
            </p>
          )}
        </section>

        {/* Autonomy rules */}
        <section className="mc-agent-settings-card">
          <h2 className="mc-agent-settings-card-title">
            Autonomy <em>rules</em>
          </h2>
          <p className="mc-agent-settings-card-help" style={{ marginBottom: 14 }}>
            By default every candidate-affecting decision the agent makes goes to your Decision Hub for approval. Flip these on to let the agent act without asking.
          </p>
          {[
            {
              key: 'auto_reject',
              value: autoReject,
              title: 'Auto-reject',
              sub: 'Below-threshold candidates are rejected immediately (pre-screen, scoring, and assessment stages). Off: every reject lands in the Decision Hub for one-click approval.',
            },
            {
              key: 'auto_promote',
              value: autoPromote,
              title: 'Auto-promote',
              sub: 'Sending an assessment and advancing to interview happen without approval. Off: each invite/advance queues as a Decision Hub card.',
            },
          ].map((rule, idx) => (
            <label key={rule.key} className={`mc-agent-settings-rule ${idx === 0 ? '' : 'is-divided'}`}>
              <button
                type="button"
                className={`mc-switch ${rule.value ? 'on' : ''}`}
                onClick={() => handleAutonomyToggle(rule.key, !rule.value)}
                aria-pressed={Boolean(rule.value)}
                aria-label={rule.title}
              />
              <div>
                <div className="mc-agent-settings-rule-title">{rule.title}</div>
                <div className="mc-agent-settings-rule-sub">{rule.sub}</div>
              </div>
            </label>
          ))}
        </section>

        {role?.id ? <AgentActivityLog roleId={role.id} /> : null}

        {/* Save bar */}
        <div className="mc-agent-settings-savebar">
          <span>
            Changes apply to this role only. Org defaults stay intact —{' '}
            <a href="#org-defaults" style={{ color: 'var(--purple)' }}>edit org defaults →</a>
          </span>
          <button type="button" className="btn btn-purple btn-sm" onClick={onSave} disabled={savingRoleConfig}>
            {savingRoleConfig ? 'Saving…' : 'Save role settings'}
          </button>
        </div>
      </div>

      {/* Sidebar */}
      <aside className="mc-agent-settings-side">
        <div className="mc-agent-settings-side-card">
          <div className="mc-kicker is-mute" style={{ marginBottom: 8 }}>ROLE BUDGET · THIS MONTH</div>
          <p className="mc-agent-settings-card-help" style={{ marginTop: 0, marginBottom: 10 }}>
            One pot for everything we do on this role: pre-screen, scoring, semantic search, assessments, and the agent.
          </p>
          <div className="mc-agent-settings-budget-amount">
            <span className="big">{fmtUsd(monthlySpentCents)}</span>
            <span className="of">of {fmtUsd(monthlyBudgetCents)}</span>
          </div>
          <div className="mc-agent-settings-budget-bar">
            <i style={{ width: `${budgetPct}%` }} />
          </div>
          <div className="mc-agent-settings-budget-foot">
            EOM PROJECTION ≈ {fmtUsd(projectedCents)} ·{' '}
            {projectedCents > monthlyBudgetCents ? 'over budget' : 'paced under budget'}
          </div>
          {Array.isArray(usageBreakdown?.by_feature) && usageBreakdown.by_feature.length > 0 ? (
            <ul className="mc-agent-settings-budget-breakdown">
              {(() => {
                // Roll up the per-feature lines into the recruiter-facing
                // labels (Scoring, Pre-screen, Semantic search, etc.) the
                // backend already grouped by, then render one row each.
                const grouped = new Map();
                for (const line of usageBreakdown.by_feature) {
                  const key = line.label || line.feature;
                  const prev = grouped.get(key) || { label: key, cost_cents: 0, event_count: 0 };
                  prev.cost_cents += Number(line.cost_cents || 0);
                  prev.event_count += Number(line.event_count || 0);
                  grouped.set(key, prev);
                }
                return [...grouped.values()]
                  .sort((a, b) => b.cost_cents - a.cost_cents)
                  .map((row) => (
                    <li key={row.label}>
                      <span className="mc-agent-settings-budget-breakdown-label">{row.label}</span>
                      <span className="mc-agent-settings-budget-breakdown-amt">{fmtUsd(row.cost_cents)}</span>
                    </li>
                  ));
              })()}
            </ul>
          ) : monthlySpentCents > 0 ? null : (
            <div className="mc-agent-settings-card-help" style={{ marginTop: 12 }}>
              No spend yet this month.
            </div>
          )}
          {/* HANDOFF v2 §4.3 — Monthly cap $X · Edit. Recruiters can
              raise / lower the per-role cap inline; saved value is
              persisted on the role record (monthly_usd_budget_cents),
              not a session-only override. */}
          {budgetEditing ? (
            <div className="mc-agent-settings-budget-edit">
              <label className="mc-agent-settings-budget-edit-label">
                Monthly cap (USD)
                <div className="mc-agent-settings-budget-edit-input">
                  <span className="prefix">$</span>
                  <input
                    type="number"
                    min={0}
                    step={5}
                    value={budgetDraftDollars}
                    onChange={(event) => setBudgetDraftDollars(event.target.value)}
                    aria-label="Monthly budget in dollars"
                    autoFocus
                  />
                </div>
              </label>
              <div className="mc-agent-settings-budget-edit-actions">
                <button
                  type="button"
                  className="btn btn-outline btn-xs"
                  onClick={cancelBudgetEdit}
                  disabled={budgetSaving}
                >
                  <X size={11} />
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn btn-purple btn-xs"
                  onClick={submitBudgetEdit}
                  disabled={budgetSaving || budgetDraftDollars === ''}
                >
                  <Check size={11} />
                  {budgetSaving ? 'Saving…' : 'Save cap'}
                </button>
              </div>
            </div>
          ) : (
            <div className="mc-agent-settings-budget-cap-row">
              <span>Monthly cap {fmtUsd(monthlyBudgetCents)}</span>
              <button
                type="button"
                className="mc-agent-settings-budget-edit-link"
                onClick={startBudgetEdit}
              >
                <Edit3 size={11} />
                Edit
              </button>
            </div>
          )}
        </div>

        <div className="mc-agent-settings-side-card">
          <div className="mc-kicker is-mute" style={{ marginBottom: 8 }}>MUST-HAVE REQUIREMENTS</div>
          <p className="mc-agent-settings-card-help" style={{ marginBottom: 10 }}>The agent rejects below these — no exceptions.</p>
          {mustHaves.length ? (
            <ul className="mc-agent-settings-mustlist">
              {mustHaves.map((item, idx) => (
                <li key={`${item}-${idx}`}>· {item}</li>
              ))}
            </ul>
          ) : (
            <div className="mc-agent-settings-card-help">No must-haves set yet.</div>
          )}
        </div>

        <div className="mc-agent-settings-side-card">
          <div className="mc-kicker is-mute" style={{ marginBottom: 8 }}>PAUSE THRESHOLD</div>
          <p className="mc-agent-settings-card-help" style={{ marginBottom: 10 }}>Agent pauses itself when budget reaches this %.</p>
          <select className="mc-agent-settings-select" defaultValue={80}>
            <option value={70}>70%</option>
            <option value={80}>80%</option>
            <option value={90}>90%</option>
          </select>
        </div>

        <div className="mc-agent-settings-audit-callout">
          Inherits from <a href="#org-defaults" style={{ color: 'var(--purple)' }}>org defaults</a>. Changes here apply to this role only.
        </div>
      </aside>
    </div>
  );
};

export const JobPipelinePage = ({ onNavigate, onViewCandidate, NavComponent = null }) => {
  const { roleId } = useParams();
  const rolesApi = apiClient.roles;
  const tasksApi = 'tasks' in apiClient ? apiClient.tasks : null;
  const { showToast } = useToast();
  const {
    jobs,
    processJobs,
    trackRole,
    trackRoleFetchCvs,
    trackRolePreScreen,
    trackRoleProcess,
  } = useJobStatus() ?? {};
  void onViewCandidate;

  const numericRoleId = Number(roleId);
  // Batch progress is owned by the global JobStatusContext — single source of truth.
  const batchScoreProgress = jobs?.[numericRoleId] ?? EMPTY_PROGRESS;
  // Live agent status for THIS role — backend serves /roles/{id}/agent/status
  // with monthly_spent_cents + monthly_budget_cents + pending_decisions +
  // last_activity. Polled every 30s, paused when the tab is hidden.
  const { status: agentStatus } = useAgentStatus(Number.isFinite(numericRoleId) ? numericRoleId : null);
  // Per-feature spend breakdown for the role budget panel. Refetched
  // whenever the role's monthly spend ticks (a coarse proxy for "new
  // usage events landed"); cheap enough to call inline.
  const [usageBreakdown, setUsageBreakdown] = useState(null);
  useEffect(() => {
    if (!Number.isFinite(numericRoleId)) return undefined;
    if (!apiClient?.agent?.usageBreakdown) return undefined;
    let cancelled = false;
    apiClient.agent.usageBreakdown(numericRoleId)
      .then((res) => { if (!cancelled) setUsageBreakdown(res?.data || null); })
      .catch(() => { if (!cancelled) setUsageBreakdown(null); });
    return () => { cancelled = true; };
  }, [numericRoleId, agentStatus?.monthly_spent_cents]);
  // Pending agent decisions for this role, keyed by application_id so the
  // Pipeline-tab kanban cards can render the real Approve/Override flow
  // inline (HANDOFF v2 §4 / canvas jobs-detail-pipeline). Polls every 30s.
  const [pendingAgentDecisions, setPendingAgentDecisions] = useState({});
  const [resolvingDecisionId, setResolvingDecisionId] = useState(null);
  const fetchPendingDecisions = useCallback(async () => {
    if (!Number.isFinite(numericRoleId)) return;
    try {
      const res = await apiClient.agent.listDecisions({
        role_id: numericRoleId,
        status: 'pending',
        limit: 50,
      });
      const list = Array.isArray(res?.data) ? res.data : [];
      setPendingAgentDecisions(
        list.reduce((acc, decision) => {
          const appId = Number(decision?.application_id);
          if (Number.isFinite(appId)) acc[appId] = decision;
          return acc;
        }, {}),
      );
    } catch {
      // Quiet failure — the kanban cards just fall back to the
      // score-based decision verb until next poll succeeds.
    }
  }, [numericRoleId]);
  useEffect(() => {
    void fetchPendingDecisions();
    const handle = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      void fetchPendingDecisions();
    }, 30_000);
    return () => window.clearInterval(handle);
  }, [fetchPendingDecisions]);
  const handleApproveDecision = useCallback(async (decisionId) => {
    if (!decisionId) return;
    setResolvingDecisionId(decisionId);
    try {
      await apiClient.agent.approveDecision(decisionId);
      showToast(`Approved agent recommendation #${decisionId}`, 'success');
      await fetchPendingDecisions();
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to approve recommendation.'), 'error');
    } finally {
      setResolvingDecisionId(null);
    }
  }, [fetchPendingDecisions, showToast]);
  const handleOverrideDecision = useCallback(async (decisionId) => {
    if (!decisionId) return;
    setResolvingDecisionId(decisionId);
    try {
      await apiClient.agent.overrideDecision(decisionId, { override_action: 'manual_review' });
      showToast(`Overrode agent recommendation #${decisionId}`, 'info');
      await fetchPendingDecisions();
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to override recommendation.'), 'error');
    } finally {
      setResolvingDecisionId(null);
    }
  }, [fetchPendingDecisions, showToast]);
  const [role, setRole] = useState(null);
  // Workspace chips loaded once per role-workspace load. Used by the
  // role page chip editor for the "Show hidden" suppressed-chips view
  // (we need the workspace text/bucket for chips the recruiter has
  // hidden from this role).
  const [workspaceCriteria, setWorkspaceCriteria] = useState([]);
  const [criteriaBusy, setCriteriaBusy] = useState(false);
  const [criteriaSyncing, setCriteriaSyncing] = useState(false);
  const [criteriaResetting, setCriteriaResetting] = useState(false);
  const [roleTasks, setRoleTasks] = useState([]);
  const [allTasks, setAllTasks] = useState([]);
  const [roleApplications, setRoleApplications] = useState([]);
  const [fetchCvsProgress, setFetchCvsProgress] = useState(EMPTY_FETCH_PROGRESS);
  const [preScreenProgress, setPreScreenProgress] = useState(EMPTY_PRE_SCREEN_PROGRESS);
  const [confirmAction, setConfirmAction] = useState(EMPTY_CONFIRM);
  const [processDialogOpen, setProcessDialogOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [savingRoleConfig, setSavingRoleConfig] = useState(false);
  const [thresholdDraft, setThresholdDraft] = useState('');
  const [suggestedThreshold, setSuggestedThreshold] = useState(null);
  const [savingThresholdMode, setSavingThresholdMode] = useState(false);
  const handleThresholdModeChange = useCallback(async (nextMode) => {
    if (!Number.isFinite(numericRoleId)) return;
    if (nextMode !== 'auto' && nextMode !== 'manual') return;
    setSavingThresholdMode(true);
    setRole((cur) => (cur ? { ...cur, auto_reject_threshold_mode: nextMode } : cur));
    try {
      await rolesApi.update(numericRoleId, { auto_reject_threshold_mode: nextMode });
      if (nextMode === 'auto') {
        try {
          const res = await rolesApi.suggestedAutoRejectThreshold(numericRoleId);
          setSuggestedThreshold(res?.data || null);
        } catch { /* leave previous suggestion */ }
      }
      showToast(nextMode === 'auto' ? 'Threshold mode set to auto — agent will pick the cut-off.' : 'Threshold mode set to manual.', 'success');
    } catch (error) {
      setRole((cur) => (cur ? { ...cur, auto_reject_threshold_mode: nextMode === 'auto' ? 'manual' : 'auto' } : cur));
      showToast(getErrorMessage(error, 'Failed to update threshold mode.'), 'error');
    } finally {
      setSavingThresholdMode(false);
    }
  }, [numericRoleId, rolesApi, showToast]);
  const [refreshTick, setRefreshTick] = useState(0);
  const [interviewFocusGenerating, setInterviewFocusGenerating] = useState(false);
  const [detailsExpanded, setDetailsExpanded] = useState(false);
  const [activeView, setActiveView] = useRoleView();
  // HANDOFF v2 §4 / canvas jobs-detail-candidates — primary stage filter
  // for the Candidates tab. The segmented row above the table toggles
  // this; the embedded directory re-mounts via key so its internal
  // `stageFilters` re-seeds from the new initial value.
  const [tableStageFilter, setTableStageFilter] = useState('all');
  // Candidates-table sort: which column (`tableSortField`) and direction
  // (`tableSortBy`, default desc → strongest score / most-recent first).
  const [tableSortBy, setTableSortBy] = useState('desc');
  const [tableSortField, setTableSortField] = useState('score');
  // Click a sortable header → sort on it (desc), or flip direction if active.
  const handleTableSort = useCallback((field) => {
    setTableSortBy((dir) => (tableSortField === field ? (dir === 'asc' ? 'desc' : 'asc') : 'desc'));
    setTableSortField(field);
  }, [tableSortField]);
  // Per-row Process selection. Non-empty → Process sends just these IDs
  // and ignores stage_filter. Reset on tab switch so off-screen ticks
  // don't silently fire when the recruiter jumps tabs.
  const [selectedAppIds, setSelectedAppIds] = useState(() => new Set());
  useEffect(() => { setSelectedAppIds(new Set()); }, [tableStageFilter]);
  const [roleSheetOpen, setRoleSheetOpen] = useState(false);
  const [candidateSheetOpen, setCandidateSheetOpen] = useState(false);
  const [roleSheetError, setRoleSheetError] = useState('');
  const [candidateSheetError, setCandidateSheetError] = useState('');
  // The legacy slide-out <AgentSettingsPanel> drawer state has been
  // retired — the canvas-spec Agent settings tab on this page owns
  // the same controls inline. See the AgentBar onPause handler below.
  const [savingRoleSheet, setSavingRoleSheet] = useState(false);
  const [addingCandidate, setAddingCandidate] = useState(false);

  const loadRoleWorkspace = useCallback(async () => {
    if (!Number.isFinite(numericRoleId)) return;
    // Stale-while-revalidate: if we've loaded this role before, paint the
    // cached workspace immediately and revalidate silently in the background.
    // Only show the full-page spinner on a true cold load. This makes
    // navigating away and back feel instant instead of re-spinning.
    const cacheKey = `role-workspace:${numericRoleId}`;
    const cached = readCache(cacheKey);
    if (cached?.data) {
      const c = cached.data;
      setRole(c.role || null);
      setRoleTasks(Array.isArray(c.roleTasks) ? c.roleTasks : []);
      setRoleApplications(Array.isArray(c.roleApplications) ? c.roleApplications : []);
      setWorkspaceCriteria(Array.isArray(c.workspaceCriteria) ? c.workspaceCriteria : []);
      setLoading(false);
    } else {
      setLoading(true);
    }
    try {
      // Two separate fetches (open + rejected) at the backend's 2000-row
      // ceiling — splits the budget so a long reject history can't crowd
      // open candidates out, and avoids the 500-row default that would
      // silently truncate thousand-applicant roles.
      const appsQuery = (outcome) => ({ sort_by: 'pre_screen_score', sort_order: 'desc', application_outcome: outcome, limit: 2000 });
      const [roleRes, tasksRes, openAppsRes, rejectedAppsRes, batchStatusRes, fetchStatusRes, preScreenStatusRes, orgCriteriaRes] = await Promise.all([
        rolesApi.get(numericRoleId),
        rolesApi.listTasks(numericRoleId),
        rolesApi.listApplications(numericRoleId, appsQuery('open')),
        rolesApi.listApplications(numericRoleId, appsQuery('rejected')),
        rolesApi.batchScoreStatus(numericRoleId),
        rolesApi.fetchCvsStatus(numericRoleId),
        rolesApi.batchPreScreenStatus(numericRoleId).catch(() => ({ data: EMPTY_PRE_SCREEN_PROGRESS })),
        // Workspace chips for the suppressed-chips ("hidden from this
        // role") view in the chip editor. Defensive: optional-chained
        // call + .catch so a missing API client or transient failure
        // doesn't blow up the whole role workspace load.
        Promise.resolve(apiClient.organizations?.listCriteria?.() ?? { data: [] })
          .catch(() => ({ data: [] })),
      ]);
      const nextRole = roleRes?.data || null;
      setRole(nextRole);
      setWorkspaceCriteria(Array.isArray(orgCriteriaRes?.data) ? orgCriteriaRes.data : []);
      setThresholdDraft(nextRole?.score_threshold != null ? String(nextRole.score_threshold) : '');
      // Fetch the agent's threshold recommendation when the role is
      // in auto mode so the panel shows it without waiting for click.
      if (nextRole?.auto_reject_threshold_mode === 'auto' && Number.isFinite(numericRoleId)) {
        rolesApi.suggestedAutoRejectThreshold(numericRoleId)
          .then((res) => setSuggestedThreshold(res?.data || null))
          .catch(() => setSuggestedThreshold(null));
      } else setSuggestedThreshold(null);
      const nextTasks = Array.isArray(tasksRes?.data) ? tasksRes.data : [];
      setRoleTasks(nextTasks);
      // Dedupe by id — defensive against any backend overlap.
      const byId = new Map();
      for (const a of [...(openAppsRes?.data || []), ...(rejectedAppsRes?.data || [])]) {
        if (a?.id != null && !byId.has(a.id)) byId.set(a.id, a);
      }
      const nextApps = [...byId.values()];
      setRoleApplications(nextApps);
      const nextCriteria = Array.isArray(orgCriteriaRes?.data) ? orgCriteriaRes.data : [];
      // Refresh the SWR cache so the next visit paints instantly.
      writeCache(cacheKey, {
        role: nextRole,
        roleTasks: nextTasks,
        roleApplications: nextApps,
        workspaceCriteria: nextCriteria,
      });
      // Hand off batch status to the global context — it owns display state.
      // If a batch is already running when this page loads, make the context
      // track it immediately (no waiting for the next 10s discovery poll).
      const initBatchStatus = String(batchStatusRes?.data?.status || '').toLowerCase();
      if (['running', 'cancelling', 'cancelled', 'completed'].includes(initBatchStatus)) {
        trackRole?.(numericRoleId);
      }
      setFetchCvsProgress(fetchStatusRes?.data || EMPTY_FETCH_PROGRESS);
      setPreScreenProgress(preScreenStatusRes?.data || EMPTY_PRE_SCREEN_PROGRESS);
    } catch (error) {
      // Don't wipe a cached paint if a background revalidate fails — only
      // surface a hard failure when there was nothing to show in the first
      // place (cold load).
      if (!cached?.data) {
        setRole(null);
        setRoleTasks([]);
        setRoleApplications([]);
        showToast(getErrorMessage(error, 'Failed to load role pipeline.'), 'error');
      }
    } finally {
      setLoading(false);
    }
  }, [numericRoleId, rolesApi, showToast, trackRole]);

  useEffect(() => {
    void loadRoleWorkspace();
  }, [loadRoleWorkspace]);

  // The org-wide task list only feeds the role-edit drawer's task picker
  // (<RoleSheet>). It's not needed for the candidate table, so defer the
  // fetch until the drawer is first opened — one fewer request on every
  // role-page load. `loadedAllTasksRef` keeps it to a single fetch.
  const loadedAllTasksRef = useRef(false);
  useEffect(() => {
    if (!roleSheetOpen || loadedAllTasksRef.current || !tasksApi?.list) return undefined;
    let cancelled = false;
    const loadAllTasks = async () => {
      try {
        const res = await tasksApi.list();
        if (!cancelled) {
          setAllTasks(Array.isArray(res?.data) ? res.data : []);
          loadedAllTasksRef.current = true;
        }
      } catch {
        if (!cancelled) setAllTasks([]);
      }
    };
    void loadAllTasks();
    return () => {
      cancelled = true;
    };
  }, [roleSheetOpen, tasksApi]);

  // ── Reload applications when the global context tells us a batch finished ──
  // batchScoreProgress is read from JobStatusContext (single source of truth).
  // We track the previous status in a ref so we detect the running→terminal
  // transition and trigger a workspace reload to refresh candidate scores.
  const prevBatchStatusRef = useRef('');
  useEffect(() => {
    const current = String(batchScoreProgress?.status || '').toLowerCase();
    const prev = prevBatchStatusRef.current;
    prevBatchStatusRef.current = current;
    if (prev === 'running' && (current === 'completed' || current === 'cancelled')) {
      void loadRoleWorkspace();
      setRefreshTick((value) => value + 1);
    }
  }, [batchScoreProgress?.status, loadRoleWorkspace]);

  // Poll fetchCvs + pre-screen progress while a job runs (pauses when the tab
  // is hidden, reloads the workspace on completion). Extracted to a hook.
  useRoleProgressPolling({
    numericRoleId,
    rolesApi,
    fetchCvsProgress,
    preScreenProgress,
    setFetchCvsProgress,
    setPreScreenProgress,
    loadRoleWorkspace,
    bumpRefreshTick: () => setRefreshTick((value) => value + 1),
  });

  const rejectedApplications = useMemo(() => (
    roleApplications.filter((application) => application?.application_outcome === 'rejected')
  ), [roleApplications]);
  const activeApplications = useMemo(() => (
    roleApplications.filter((application) => application?.application_outcome === 'open')
  ), [roleApplications]);

  const unscoredApplications = useMemo(() => (
    activeApplications.filter((application) => application?.cv_match_score == null)
  ), [activeApplications]);

  const thresholdValue = useMemo(
    () => resolveOptionalPercent(role?.score_threshold),
    [role?.score_threshold]
  );
  const belowThresholdCount = useMemo(() => {
    if (thresholdValue == null) return 0;
    return activeApplications.filter((application) => {
      const score = Number(application?.pre_screen_score);
      return Number.isFinite(score) && score < thresholdValue;
    }).length;
  }, [activeApplications, thresholdValue]);

  // HANDOFF v2 §4 — Candidates tab KPI row matches canvas exactly:
  // In pipeline · New CVs · Below threshold · Agent spend (with bar).
  // The legacy "Assessment tasks" + "Interview focus" tiles are gone —
  // task linkage shows in the role hero, interview focus is per-candidate.
  // Spend pulls from /roles/{id}/agent/status (live), budget cap is the
  // role.monthly_usd_budget_cents column.
  const pipelineStats = useMemo(() => {
    const monthlySpentCents = Number(agentStatus?.monthly_spent_cents || 0);
    const monthlyBudgetCents = Number(
      agentStatus?.monthly_budget_cents
      ?? role?.monthly_usd_budget_cents
      ?? 0
    );
    const spentDollars = Math.round(monthlySpentCents / 100);
    const budgetDollars = Math.round(monthlyBudgetCents / 100);
    const spendPct = monthlyBudgetCents > 0
      ? Math.min(100, Math.round((monthlySpentCents / monthlyBudgetCents) * 100))
      : null;
    const eomProjection = spendPct != null && spendPct > 0
      ? Math.round((spentDollars * 30) / Math.max(1, new Date().getDate()))
      : null;
    return [
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
        description: unscoredApplications.length > 0 ? 'Ready to score' : 'All visible CVs scored',
      },
      {
        key: 'below-threshold',
        label: 'Below threshold',
        value: String(belowThresholdCount),
        description: thresholdValue != null ? `Auto-flagged at <${thresholdValue}` : 'Set a reject threshold',
      },
      {
        key: 'spend',
        label: 'Agent spend',
        // value rendered specially below — we still emit a string fallback
        // so the .v cell isn't empty when budget is 0.
        value: budgetDollars > 0 ? `$${spentDollars}` : '—',
        valueSuffix: budgetDollars > 0 ? `/ $${budgetDollars}` : null,
        budgetPct: spendPct,
        description: spendPct != null
          ? `${spendPct}%${eomProjection != null && budgetDollars > 0 ? ` · projected $${eomProjection} EOM` : ''}`
          : 'Cap not set',
      },
    ];
  }, [activeApplications.length, agentStatus, belowThresholdCount, role, thresholdValue, unscoredApplications.length]);

  const groupedApplications = useMemo(() => [
    ...PIPELINE_STAGE_ORDER.map((stage) => ({
      ...stage,
      items: activeApplications.filter((application) => String(application?.pipeline_stage || '').toLowerCase() === stage.key),
    })),
    { key: 'rejected', label: 'Rejected', countLabel: 'closed', items: rejectedApplications },
  ], [activeApplications, rejectedApplications]);

  // Recruiter chips on this role (excludes derived_from_spec entries — those
  // come from the job spec parser and are managed separately).
  const roleCriteria = useMemo(() => (
    Array.isArray(role?.criteria)
      ? role.criteria.filter((c) => !c.deleted_at && c.source !== 'derived_from_spec')
      : []
  ), [role]);
  const recruiterCriteria = useMemo(() => roleCriteria.map((c) => c.text).filter(Boolean), [roleCriteria]);
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

  const roleFactValues = useMemo(() => ({
    location: role?.location || role?.candidate_location || parsedJobSpec.meta.location || 'Location not captured',
    department: role?.department || parsedJobSpec.meta.department || role?.organization_name || 'Hiring team',
    employment: role?.employment_type || parsedJobSpec.meta.employmentType || 'Full-time',
  }), [parsedJobSpec.meta.department, parsedJobSpec.meta.employmentType, parsedJobSpec.meta.location, role?.candidate_location, role?.department, role?.employment_type, role?.location, role?.organization_name]);

  // ---------------------------------------------------------------------------
  // Confirmation flow for batch actions
  //
  // Each batch action (fetch CVs, pre-screen, score, rescore, refresh
  // pre-screen) goes through the same 3-step flow:
  //   1. User clicks button → openConfirm({ action: 'pre_screen_new' })
  //   2. We fire the action's dry_run, populate `bullets`, show the dialog
  //   3. On confirm → call the action without dry_run, close the dialog
  // ---------------------------------------------------------------------------
  const openConfirm = async (action) => {
    if (!Number.isFinite(numericRoleId)) return;
    setConfirmAction({
      open: true,
      action,
      bullets: [],
      loading: false,
      dryRunLoading: true,
    });
    try {
      let bullets = [];
      let title = '';
      let description = '';
      let warning = null;
      let confirmLabel = 'Run';
      let variant = 'primary';
      if (action === 'fetch_cvs') {
        const dr = await rolesApi.fetchCvs(numericRoleId, { dry_run: true });
        const willFetch = Number(dr?.data?.will_fetch || 0);
        title = 'Fetch CVs from Workable';
        description = `Pull missing CVs for candidates in this role.`;
        bullets = [{ label: 'Will fetch', value: willFetch }];
        confirmLabel = `Fetch ${willFetch} CV${willFetch === 1 ? '' : 's'}`;
        if (willFetch === 0) confirmLabel = 'Nothing to do';
      } else if (action === 'pre_screen_new' || action === 'pre_screen_refresh') {
        const refresh = action === 'pre_screen_refresh';
        const dr = await rolesApi.batchPreScreen(numericRoleId, { dry_run: true, refresh });
        const willProcess = Number(dr?.data?.will_process || 0);
        const noCv = Number(dr?.data?.total_without_cv || 0);
        title = refresh ? 'Refresh pre-screen' : 'Pre-screen new candidates';
        description = refresh
          ? 'Re-run pre-screen on every candidate with a CV. Existing scores remain.'
          : 'Run pre-screen on candidates that have a CV but have not been pre-screened yet (or whose CV was uploaded after the last pre-screen).';
        bullets = [
          { label: 'Will pre-screen', value: willProcess },
          ...(noCv ? [{ label: 'Skipped (no CV)', value: noCv }] : []),
        ];
        if (refresh) warning = 'Existing pre-screen results will be overwritten.';
        confirmLabel = willProcess
          ? `Pre-screen ${willProcess}`
          : 'Nothing to do';
      } else if (action === 'score_new' || action === 'score_rescore') {
        const includeScored = action === 'score_rescore';
        const dr = await rolesApi.batchScore(numericRoleId, { include_scored: includeScored, dry_run: true });
        const willFetch = Number(dr?.data?.will_fetch_cv || 0);
        const willPre = Number(dr?.data?.will_pre_screen || 0);
        const willScore = Number(dr?.data?.will_score || 0);
        title = includeScored ? 'Re-score all candidates' : 'Score new candidates';
        description = includeScored
          ? 'Re-score every candidate with a CV. Pre-screen runs again only for candidates whose CV has changed.'
          : 'For each candidate: fetch CV if missing, pre-screen if not yet done, then score. Skips candidates already scored or marked Below threshold.';
        bullets = [
          { label: 'Will fetch CV', value: willFetch },
          { label: 'Will pre-screen', value: willPre },
          { label: 'Will score', value: willScore },
        ];
        if (includeScored) warning = 'Existing scores will be overwritten.';
        variant = includeScored ? 'danger' : 'primary';
        confirmLabel = willScore
          ? (includeScored ? `Re-score ${willScore}` : `Score ${willScore}`)
          : 'Nothing to do';
      } else {
        title = 'Confirm';
        description = 'Confirm this action.';
      }
      setConfirmAction({
        open: true,
        action,
        bullets,
        title,
        description,
        warning,
        confirmLabel,
        variant,
        loading: false,
        dryRunLoading: false,
      });
    } catch (error) {
      setConfirmAction(EMPTY_CONFIRM);
      showToast(getErrorMessage(error, 'Failed to preview action.'), 'error');
    }
  };

  const closeConfirm = () => setConfirmAction(EMPTY_CONFIRM);

  const runConfirmedAction = async () => {
    if (!Number.isFinite(numericRoleId)) return;
    const action = confirmAction.action;
    setConfirmAction((s) => ({ ...s, loading: true }));
    try {
      if (action === 'fetch_cvs') {
        const res = await rolesApi.fetchCvs(numericRoleId);
        const payload = res?.data || EMPTY_FETCH_PROGRESS;
        setFetchCvsProgress({
          status: payload.status || 'started',
          total: Number(payload.total || 0),
          fetched: Number(payload.fetched || 0),
          errors: Number(payload.errors || 0),
        });
        if (payload.status !== 'already_running' && Number(payload.total || 0) > 0) {
          // Hand off to the global toaster — it polls /fetch-cvs/status and
          // shows the row in the bottom-right.
          trackRoleFetchCvs?.(numericRoleId);
        }
      } else if (action === 'pre_screen_new' || action === 'pre_screen_refresh') {
        const refresh = action === 'pre_screen_refresh';
        const res = await rolesApi.batchPreScreen(numericRoleId, { refresh });
        const payload = res?.data || EMPTY_PRE_SCREEN_PROGRESS;
        setPreScreenProgress({
          status: payload.status || 'started',
          total: Number(payload.total || 0),
          processed: Number(payload.processed || 0),
          errors: Number(payload.errors || 0),
          refresh: Boolean(payload.refresh || refresh),
        });
        if (payload.status === 'nothing_to_pre_screen') {
          showToast('No candidates need pre-screening right now.', 'info');
        } else {
          trackRolePreScreen?.(numericRoleId);
        }
      } else if (action === 'score_new' || action === 'score_rescore') {
        const includeScored = action === 'score_rescore';
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
        } else {
          // Hand off to the global job-status context — it owns the polling
          // and renders progress in the BackgroundJobsToaster.
          trackRole?.(numericRoleId);
        }
      }
      setConfirmAction(EMPTY_CONFIRM);
    } catch (error) {
      setConfirmAction((s) => ({ ...s, loading: false }));
      showToast(getErrorMessage(error, 'Action failed.'), 'error');
    }
  };

  const handleSaveRoleConfig = async () => {
    if (!Number.isFinite(numericRoleId)) return;
    setSavingRoleConfig(true);
    try {
      await rolesApi.update(numericRoleId, {
        score_threshold: thresholdDraft === '' ? null : Number(normalizeThreshold(thresholdDraft)),
      });
      await loadRoleWorkspace();
      setRefreshTick((value) => value + 1);
      showToast('Reject threshold updated.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save reject threshold.'), 'error');
    } finally {
      setSavingRoleConfig(false);
    }
  };

  // Per-role chip CRUD + sync/reset. Merge the returned chip into role.criteria —
  // a full role-workspace refetch would drag in 2× 2000-row application lists per edit.
  const handleCreateRoleCriterion = useCallback(async ({ text, bucket }) => {
    if (!Number.isFinite(numericRoleId)) return;
    setCriteriaBusy(true);
    try {
      const { data } = await rolesApi.createCriterion(numericRoleId, { text, bucket });
      if (data) setRole((cur) => cur && ({
        ...cur,
        criteria: [...(cur.criteria || []).filter((c) => c.id !== data.id), data],
      }));
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to add criterion.'), 'error');
    } finally {
      setCriteriaBusy(false);
    }
  }, [numericRoleId, rolesApi, showToast]);

  const handleUpdateRoleCriterion = useCallback(async (criterionId, updates) => {
    if (!Number.isFinite(numericRoleId)) return;
    setCriteriaBusy(true);
    try {
      const { data } = await rolesApi.updateCriterion(numericRoleId, criterionId, updates);
      if (data) setRole((cur) => cur && ({
        ...cur,
        criteria: (cur.criteria || []).map((c) => (c.id === criterionId ? data : c)),
      }));
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to update criterion.'), 'error');
    } finally {
      setCriteriaBusy(false);
    }
  }, [numericRoleId, rolesApi, showToast]);

  const handleDeleteRoleCriterion = useCallback(async (criterionId) => {
    if (!Number.isFinite(numericRoleId)) return;
    // Optimistic remove. If the chip is workspace-derived, mirror the backend
    // and append its org_criterion_id to the suppressed list.
    let previousRole = null;
    setRole((cur) => {
      previousRole = cur;
      if (!cur) return cur;
      const target = (cur.criteria || []).find((c) => c.id === criterionId);
      const orgId = target?.org_criterion_id;
      const suppressed = cur.suppressed_org_criterion_ids || [];
      return {
        ...cur,
        criteria: (cur.criteria || []).filter((c) => c.id !== criterionId),
        suppressed_org_criterion_ids: orgId != null
          ? Array.from(new Set([...suppressed, Number(orgId)]))
          : suppressed,
      };
    });
    try {
      await rolesApi.deleteCriterion(numericRoleId, criterionId);
    } catch (error) {
      if (previousRole) setRole(previousRole);
      showToast(getErrorMessage(error, 'Failed to remove criterion.'), 'error');
    }
  }, [numericRoleId, rolesApi, showToast]);

  const handleSyncRoleCriteria = useCallback(async () => {
    if (!Number.isFinite(numericRoleId)) return;
    setCriteriaSyncing(true);
    try {
      const res = await rolesApi.syncCriteriaWithWorkspace(numericRoleId);
      if (res?.data) setRole(res.data);
      showToast('Workspace updates pulled in.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to sync workspace criteria.'), 'error');
    } finally {
      setCriteriaSyncing(false);
    }
  }, [numericRoleId, rolesApi, showToast]);

  const handleResetRoleCriteria = useCallback(async () => {
    if (!Number.isFinite(numericRoleId)) return;
    setCriteriaResetting(true);
    try {
      const res = await rolesApi.resetCriteriaToWorkspace(numericRoleId);
      if (res?.data) setRole(res.data);
      showToast('Criteria reset to workspace defaults.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to reset criteria.'), 'error');
    } finally {
      setCriteriaResetting(false);
    }
  }, [numericRoleId, rolesApi, showToast]);

  // Restore a hidden (suppressed) workspace chip on this role: re-add it
  // by calling create with the workspace text + bucket. The backend
  // doesn't drop the suppressed_org_criterion_ids entry automatically
  // here — Sync workspace would still skip the chip — so we additionally
  // remove it from the suppressed list via PATCH.
  const handleRestoreHiddenCriterion = useCallback(async (workspaceChip) => {
    if (!Number.isFinite(numericRoleId) || !workspaceChip) return;
    setCriteriaBusy(true);
    try {
      const remainingSuppressed = (role?.suppressed_org_criterion_ids || [])
        .filter((id) => Number(id) !== Number(workspaceChip.id));
      // First, drop the suppression so Sync would also re-add it next time.
      await rolesApi.update(numericRoleId, { suppressed_org_criterion_ids: remainingSuppressed });
      // Then sync to bring the chip back with full provenance (org_criterion_id set).
      const res = await rolesApi.syncCriteriaWithWorkspace(numericRoleId);
      if (res?.data) setRole(res.data);
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to restore criterion.'), 'error');
    } finally {
      setCriteriaBusy(false);
    }
  }, [numericRoleId, role, rolesApi, showToast]);

  const handleRoleSheetSubmit = async ({
    name,
    description,
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

  // Triage drawer state, handlers and Workable-stage fetch live in the
  // useCandidateTriage hook so this page stays under the architecture
  // gate's line cap. Plain row click opens the drawer; modifier-click
  // keeps the anchor's default behaviour so the standing-report escape
  // hatch still works in a new tab.
  const {
    triageApplication,
    drawerProps: triageDrawerProps,
    handleRowClick: handlePipelineReportClick,
  } = useCandidateTriage({
    role,
    roleApplications,
    roleTasks,
    loadRoleWorkspace,
    showToast,
    rolesApi,
    viewCandidateReport,
  });

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

  // HANDOFF unified-headers.md §2-§4 — Role detail uses the single
  // AgentHeader with a role-scoped agent panel on the right. Builds the
  // panel agent prop from the polled /agent/status payload, with the
  // role's own `agentic_mode_enabled` flag deciding whether it's ON or
  // OFF. The previous role-hero + AgentBar duo collapses into this hero.
  const roleAgent = useMemo(() => {
    const enabled = Boolean(role?.agentic_mode_enabled);
    if (!agentStatus) {
      return {
        on: enabled,
        paused: false,
        pending: 0,
        spentCents: 0,
        budgetCents: Number(role?.monthly_usd_budget_cents || 0) || 5000,
        tick: enabled ? 'Loading agent status…' : null,
        inFlight: false,
      };
    }
    return buildAgentPropFromStatus(agentStatus, { isEnabled: enabled });
  }, [agentStatus, role]);

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

  const goToAgentSettings = () => {
    setActiveView('role-fit');
    const tabsEl = document.querySelector('.sub-tabs-sticky');
    if (tabsEl && typeof tabsEl.scrollIntoView === 'function') {
      tabsEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  // OFF → ON / ON → OFF / AUTO-PAUSED → ON.
  // All three flows are optimistic + fire-and-forget: we flip the local
  // role state synchronously so the panel + hero swap in one frame, and
  // the PATCH runs in the background. On failure we revert the state and
  // surface an error toast — the success path is silent because the panel
  // itself communicates the new state.
  const patchAgentMode = (nextRoleFields, errorFallback) => {
    if (!Number.isFinite(numericRoleId)) return;
    const prevRole = role;
    setRole((cur) => (cur ? { ...cur, ...nextRoleFields } : cur));
    rolesApi
      .update(numericRoleId, nextRoleFields)
      .then(() => { void loadRoleWorkspace(); })
      .catch((error) => {
        setRole(prevRole);
        showToast(getErrorMessage(error, errorFallback), 'error');
      });
  };

  const handleActivateAgent = (monthlyBudgetCents) => {
    if (!Number.isFinite(monthlyBudgetCents) || monthlyBudgetCents <= 0) {
      showToast('Set a monthly cap greater than $0 before activating.', 'error');
      return;
    }
    patchAgentMode(
      { agentic_mode_enabled: true, monthly_usd_budget_cents: monthlyBudgetCents },
      'Failed to turn on agent mode.',
    );
  };

  const handlePauseAgent = () => {
    patchAgentMode({ agentic_mode_enabled: false }, 'Failed to pause agent mode.');
  };

  // AUTO-PAUSED → ON. The role is still agentic_mode_enabled=true but
  // paused_at was set when the budget cap was reached. Re-PATCHing
  // agentic_mode_enabled=true clears paused_at server-side.
  const handleResumeAgent = () => {
    patchAgentMode({ agentic_mode_enabled: true }, 'Failed to resume agent mode.');
  };

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="jobs" onNavigate={onNavigate} /> : null}
      <BreadcrumbsRow
        items={[{ label: 'Jobs', page: 'jobs' }, { label: role?.name || 'Role' }]}
        actions={<CopyLinkButton label="Copy link to role" successMessage="Role link copied." />}
      />
      <AgentHeader
        kicker={`${role?.name || 'Role'} · #${role?.id || '—'}`}
        title={role?.name || 'Role'}
        backLink={{ label: 'All roles', onClick: () => onNavigate('jobs') }}
        actions={(
          <>
            {/* Reverse deep-link to the Hub: when this role has pending
                agent decisions, surface a one-click jump to the Home
                review queue filtered to this role. Hidden when zero. */}
            {(roleAgent?.pending || 0) > 0 ? (
              <button
                type="button"
                className="btn btn-outline btn-sm"
                title={`${roleAgent.pending} pending agent decisions for this role`}
                onClick={() => {
                  const params = new URLSearchParams({
                    role: String(role?.id || ''),
                    status: 'pending',
                  });
                  window.location.assign(`/home?${params.toString()}`);
                }}
              >
                {roleAgent.pending} pending → Home
              </button>
            ) : null}
            <button type="button" className="btn btn-outline btn-sm" title="Share role" onClick={handleShareRole}>
              <Share2 size={13} />
              Share
            </button>
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
          </>
        )}
        postTitle={(
          <div className="ah-facts">
            <div className="f"><span className="k">Location</span><span className="v">{roleFactValues.location}</span></div>
            <div className="f"><span className="k">Department</span><span className="v">{roleFactValues.department}</span></div>
            <div className="f"><span className="k">Employment</span><span className="v">{roleFactValues.employment}</span></div>
            <div className="f"><span className="k">Linked task</span><span className="v purple">{roleTasks[0]?.name || 'Task not linked'}</span></div>
          </div>
        )}
        agent={roleAgent}
        onActivateAgent={handleActivateAgent}
        onPauseAgent={handlePauseAgent}
        onResumeAgent={handleResumeAgent}
        onAgentSettings={goToAgentSettings}
      />
      <div className="page">
        <div className="mc-cockpit-main">
        <RoleViewTabs activeView={activeView} />

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
                    {/* HANDOFF v2 §4 / canvas jobs-detail-pipeline — kanban
                        card per v3:
                          avatar · name + position
                          CV n% · score · ago [· LIVE]
                          (review stage only) agent recommendation block:
                            Advance / Reject + reasoning + Approve · Override
                        Approve/Override are surfaced in the
                        PendingAgentDecisionsPanel above the table for now;
                        the in-card buttons are deep-link entry points. */}
                    {visibleItems.map((application) => {
                      const cvPct = Number.isFinite(Number(application?.cv_match_score))
                        ? Math.round(Number(application.cv_match_score))
                        : null;
                      const compositeRaw = application?.score_summary?.taali_score
                        ?? application?.taali_score
                        ?? application?.assessment_score
                        ?? null;
                      const compositeScore = Number.isFinite(Number(compositeRaw))
                        ? Math.round(Number(compositeRaw))
                        : null;
                      const isLive = String(application?.pipeline_stage || '').toLowerCase() === 'in_assessment';
                      const isReview = stage.key === 'review';
                      // Real pending agent decision (if any) for this candidate.
                      // When present, the agent block surfaces the actual
                      // recommendation verb + reasoning + wires Approve /
                      // Override to apiClient.agent.{approve,override}Decision.
                      const pendingDecision = pendingAgentDecisions[application?.id] || null;
                      const decisionResolving = pendingDecision?.id != null
                        && resolvingDecisionId === pendingDecision.id;
                      const decisionVerb = pendingDecision?.recommendation
                        || (compositeScore != null && compositeScore >= 75
                          ? 'Advance to interview'
                          : compositeScore != null && compositeScore < 50
                            ? 'Reject'
                            : 'Awaiting decision');
                      return (
                        <a
                          key={application.id}
                          className={`kanban-card text-left ${isReview ? 'is-review' : ''}`}
                          href={candidateReportHref(application, numericRoleId)}
                          onClick={(event) => handlePipelineReportClick(event, application)}
                          onMouseEnter={() => prefetchDocumentBlob({ applicationId: application.id, docType: 'cv' })}
                        >
                          <div className="cc-top">
                            <div className="av">{buildApplicationTitle(application).slice(0, 2).toUpperCase()}</div>
                            <div className="cc-id">
                              <div className="n">{buildApplicationTitle(application)}</div>
                              <div className="pos">
                                {application?.candidate_position
                                  || application?.candidate_email
                                  || 'No position captured'}
                              </div>
                            </div>
                          </div>
                          <div className="cc-line">
                            {cvPct != null ? <span>CV {cvPct}%</span> : <span className="mute">No CV score</span>}
                            {compositeScore != null ? <>
                              <span className="dot-sep">·</span>
                              <span className="score-pip">{compositeScore}</span>
                            </> : null}
                            <span className="cc-line-grow" />
                            <span>
                              {formatRelativeShort(application?.updated_at || application?.created_at)}
                              {isLive ? <span className="live-pip"> · LIVE</span> : null}
                            </span>
                          </div>
                          {isReview ? (
                            <div className="cc-agent">
                              <div className="cc-agent-glyph" aria-hidden="true">
                                <Sparkles size={11} strokeWidth={2} />
                              </div>
                              <div className="cc-agent-body">
                                <div className="cc-agent-action">{decisionVerb}</div>
                                <div className="cc-agent-why">
                                  {pendingDecision?.reasoning
                                    || resolvePipelineCardFooterStatus(application)}
                                </div>
                                {pendingDecision ? (
                                  <div className="cc-agent-actions">
                                    <button
                                      type="button"
                                      className="btn btn-purple btn-xs"
                                      onClick={(event) => {
                                        event.preventDefault();
                                        event.stopPropagation();
                                        void handleApproveDecision(pendingDecision.id);
                                      }}
                                      disabled={decisionResolving}
                                    >
                                      {decisionResolving ? '…' : 'Approve'}
                                    </button>
                                    <button
                                      type="button"
                                      className="btn btn-outline btn-xs"
                                      onClick={(event) => {
                                        event.preventDefault();
                                        event.stopPropagation();
                                        void handleOverrideDecision(pendingDecision.id);
                                      }}
                                      disabled={decisionResolving}
                                    >
                                      Override
                                    </button>
                                  </div>
                                ) : null}
                              </div>
                            </div>
                          ) : null}
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

            {triageApplication ? (
              <div className="kanban-triage-row">
                <CandidateTriageDrawer {...triageDrawerProps} />
              </div>
            ) : null}

            {/* Role-level interview focus panel removed — interview guidance is per-candidate now,
                surfaced in the candidate score sheet (kit + screening pack). */}
          </div>
        ) : activeView === 'role-fit' ? (
          <RoleAgentSettingsTab
            role={role}
            agentStatus={agentStatus}
            roleCriteria={roleCriteria}
            workspaceCriteria={workspaceCriteria}
            criteriaBusy={criteriaBusy}
            criteriaSyncing={criteriaSyncing}
            criteriaResetting={criteriaResetting}
            onCreateCriterion={handleCreateRoleCriterion}
            onUpdateCriterion={handleUpdateRoleCriterion}
            onDeleteCriterion={handleDeleteRoleCriterion}
            onSyncCriteria={handleSyncRoleCriteria}
            onResetCriteria={handleResetRoleCriteria}
            onRestoreHiddenCriterion={handleRestoreHiddenCriterion}
            thresholdDraft={thresholdDraft}
            setThresholdDraft={setThresholdDraft}
            thresholdValue={thresholdValue}
            recruiterCriteria={recruiterCriteria}
            activeApplications={activeApplications}
            belowThresholdCount={belowThresholdCount}
            savingRoleConfig={savingRoleConfig}
            usageBreakdown={usageBreakdown}
            onSave={handleSaveRoleConfig}
            onScrollToReview={() => document.getElementById('pipeline-table')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
            onSaveBudget={async (dollars) => {
              if (!Number.isFinite(numericRoleId)) return;
              const cents = Math.max(0, Math.round(Number(dollars) * 100));
              try {
                await rolesApi.update(numericRoleId, { monthly_usd_budget_cents: cents });
                showToast('Monthly budget updated.', 'success');
                await loadRoleWorkspace();
              } catch (error) {
                showToast(getErrorMessage(error, 'Failed to update budget.'), 'error');
              }
            }}
            onAutonomyChange={async (key, value) => {
              if (!Number.isFinite(numericRoleId)) return;
              if (key !== 'auto_reject' && key !== 'auto_promote') return;
              setRole((cur) => (cur ? { ...cur, [key]: value } : cur));
              try {
                await rolesApi.update(numericRoleId, { [key]: value });
                showToast(
                  value
                    ? `${key === 'auto_reject' ? 'Auto-reject' : 'Auto-promote'} on — agent will execute without approval.`
                    : `${key === 'auto_reject' ? 'Auto-reject' : 'Auto-promote'} off — every decision goes to the Decision Hub.`,
                  'success',
                );
              } catch (error) {
                setRole((cur) => (cur ? { ...cur, [key]: !value } : cur));
                showToast(getErrorMessage(error, 'Failed to update autonomy setting.'), 'error');
              }
            }}
            thresholdMode={role?.auto_reject_threshold_mode || 'manual'}
            suggestedThreshold={suggestedThreshold}
            savingThresholdMode={savingThresholdMode}
            onThresholdModeChange={handleThresholdModeChange}
          />
        ) : activeView === 'activity' ? (
          // HANDOFF v2 §4.4 / canvas jobs-detail-spec — Job spec tab is the
          // dedicated spec view: workable-ingested description with formatted
          // sections + recruiter requirements + an "At a glance" sidebar.
          // The pipeline-activity timeline that previously rendered here was
          // a leftover from the v1 "Activity" tab; v2 only has 4 tabs and
          // this one is "Job spec".
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
        ) : (
          <>
            {/* HANDOFF v2 §4 / canvas jobs-detail-candidates — KPI row
                (In pipeline · New CVs · Below threshold · Agent spend) is
                the first thing inside the Candidates tab, mirroring the
                CandidatesTab artboard in tali-pages.jsx. Other tabs do not
                show these KPIs. */}
            <div className="stat-row">
              {pipelineStats.map((item) => (
                <div
                  key={item.key}
                  className={`stat ${item.highlight ? 'hi' : ''} ${item.budgetPct != null ? 'has-bar' : ''}`.trim()}
                >
                  <div className="k">{item.label}</div>
                  <div className="v">
                    {item.value}
                    {item.valueSuffix ? (
                      <span style={{ color: 'var(--mute)', fontSize: 14, marginLeft: 4 }}>{item.valueSuffix}</span>
                    ) : null}
                  </div>
                  {item.budgetPct != null ? (
                    <div className="stat-bar" aria-hidden="true">
                      <i style={{ width: `${item.budgetPct}%` }} />
                    </div>
                  ) : null}
                  <div className="d">{item.description}</div>
                </div>
              ))}
            </div>

            {/* HANDOFF v2 §4 / canvas jobs-detail-candidates — segmented
                stage filter + Sort + Score new toolbar above the table.
                Stage counts read off groupedApplications (already memoized).
                Sort is currently a label-only display until the directory
                exposes a controlled sort-by; "Score new" is wired to the
                same handler the score panel uses. */}
            <div className="ctable-toolbar">
              <div className="seg" role="tablist" aria-label="Filter candidates by stage">
                {[
                  { key: 'all', label: 'All', count: activeApplications.length },
                  ...PIPELINE_STAGE_ORDER.map((stage) => {
                    const items = (groupedApplications.find((g) => g.key === stage.key)?.items) || [];
                    return { key: stage.key, label: stage.label, count: items.length };
                  }),
                  // Rejected is an *outcome* not a *stage*; it lives at
                  // the right so the active-pipeline tabs (All / Applied /
                  // Invited / In assessment / Review / Advanced) read
                  // left-to-right as a recruiter would walk the funnel.
                  { key: 'rejected', label: 'Rejected', count: rejectedApplications.length },
                ].map((seg) => (
                  <button
                    key={seg.key}
                    type="button"
                    role="tab"
                    aria-selected={tableStageFilter === seg.key}
                    className={tableStageFilter === seg.key ? 'on' : ''}
                    onClick={() => setTableStageFilter(seg.key)}
                  >
                    {seg.label}
                    {seg.count > 0 ? <span className="ct"> ({seg.count})</span> : null}
                  </button>
                ))}
              </div>
              <div className="ctable-toolbar-grow" />
              {/* Sorting lives on the column headers (Score / Last updated). */}
              {/* HANDOFF v2 §4 / canvas jobs-detail-candidates — primary
                  recruiter action: cascade Process opened via
                  ProcessCandidatesDialog. Label flips live during runs. */}
              <button
                type="button"
                className="btn btn-purple btn-sm"
                onClick={() => setProcessDialogOpen(true)}
                disabled={String(processJobs?.[numericRoleId]?.status || '').toLowerCase() === 'running'}
              >
                {(() => {
                  const pj = processJobs?.[numericRoleId];
                  const status = String(pj?.status || '').toLowerCase();
                  if (status === 'running') {
                    const step = pj?.current_step;
                    const label = step === 'fetch' ? 'Fetching CVs' : step === 'pre_screen' ? 'Pre-screening' : step === 'score' ? 'Scoring' : 'Processing';
                    return (<><Loader2 size={12} className="animate-spin" />{label}…</>);
                  }
                  const selCount = selectedAppIds.size;
                  if (selCount > 0) return (<><Sparkles size={12} />Process {selCount} selected</>);
                  const tabCount = tableStageFilter === 'rejected' ? rejectedApplications.length
                    : tableStageFilter === 'all' ? activeApplications.length
                    : activeApplications.filter((a) => String(a?.pipeline_stage || '').toLowerCase() === tableStageFilter).length;
                  return (<><Sparkles size={12} />Process {tabCount} candidate{tabCount === 1 ? '' : 's'}</>);
                })()}
              </button>
            </div>
            {/* HANDOFF v2 §4 / canvas jobs-detail-candidates — clean
                ctable with Candidate / Score / Stage / Workable / Status /
                Agent / View →. Filtered by tableStageFilter, sorted client-side
                by tableSortBy. The full CandidatesDirectoryPage was too
                heavy here — it carried bulk-action chrome, pagination,
                NL-search, and filter chips that don't belong on the
                role detail page. The standalone /candidates route still
                uses the directory. */}
            {(() => {
              const activeStage = tableStageFilter;
              const filteredApps = activeStage === 'rejected'
                ? rejectedApplications
                : activeStage === 'all'
                  ? activeApplications
                  : activeApplications.filter((a) => String(a?.pipeline_stage || '').toLowerCase() === activeStage);
              const cmpScore = (a) => {
                const raw = a?.score_summary?.taali_score
                  ?? a?.taali_score
                  ?? a?.assessment_score
                  ?? a?.cv_match_score;
                // raw == null guard: Number(null) === 0 IS finite, so unscored sorts as a real zero without it.
                return raw != null && Number.isFinite(Number(raw)) ? Number(raw) : -1;
              };
              // Last-activity sort key — server-computed last_activity_at, with fallbacks.
              const cmpLastUpdated = (a) => {
                const raw = a?.last_activity_at || a?.updated_at || a?.created_at;
                const ms = raw ? new Date(raw).getTime() : NaN;
                return Number.isFinite(ms) ? ms : -Infinity;
              };
              const sortKey = tableSortField === 'last_updated' ? cmpLastUpdated : cmpScore;
              const sorted = [...filteredApps].sort((a, b) => (
                tableSortBy === 'asc' ? sortKey(a) - sortKey(b) : sortKey(b) - sortKey(a)
              ));
              if (sorted.length === 0) {
                return (
                  <div className="ctable-wrap">
                    <div className="ctable-empty">
                      No candidates match the current filter. Try widening the stage segment above.
                    </div>
                  </div>
                );
              }
              const visibleIds = sorted.map((a) => a.id);
              const allSel = visibleIds.length > 0 && visibleIds.every((id) => selectedAppIds.has(id));
              const someSel = visibleIds.some((id) => selectedAppIds.has(id));
              const toggleAll = (checked) => { const next = new Set(selectedAppIds); visibleIds.forEach((id) => { if (checked) next.add(id); else next.delete(id); }); setSelectedAppIds(next); };
              return (
                <div className="ctable-wrap">
                  <table className="ctable">
                    <thead>
                      <tr>
                        <th aria-label="Select" style={{ width: 28 }}><input type="checkbox" aria-label="Select all visible candidates" checked={allSel} ref={(el) => { if (el) el.indeterminate = !allSel && someSel; }} onChange={(e) => toggleAll(e.target.checked)} /></th>
                        <th>Candidate</th>
                        <th aria-sort={tableSortField === 'score' ? (tableSortBy === 'asc' ? 'ascending' : 'descending') : 'none'}>
                          <button type="button" className="ctable-sort" onClick={() => handleTableSort('score')} aria-label="Sort by score" title="Sort by score">Score{tableSortField === 'score' ? <span className="ctable-sort-arrow">{tableSortBy === 'asc' ? '↑' : '↓'}</span> : null}</button>
                        </th>
                        <th>Stage</th>
                        <th>Workable</th>
                        <th>Status</th>
                        <th>Agent</th>
                        <th aria-sort={tableSortField === 'last_updated' ? (tableSortBy === 'asc' ? 'ascending' : 'descending') : 'none'}>
                          <button type="button" className="ctable-sort" onClick={() => handleTableSort('last_updated')} aria-label="Sort by last updated" title="Sort by last updated">Last updated{tableSortField === 'last_updated' ? <span className="ctable-sort-arrow">{tableSortBy === 'asc' ? '↑' : '↓'}</span> : null}</button>
                        </th>
                        <th aria-label="Open" />
                      </tr>
                    </thead>
                    <tbody>
                      {sorted.map((application) => {
                        const stage = String(application?.pipeline_stage || '').toLowerCase();
                        const compositeRaw = application?.score_summary?.taali_score
                          ?? application?.taali_score
                          ?? application?.assessment_score
                          ?? application?.cv_match_score;
                        // compositeRaw == null guard: Number(null) === 0 IS finite — without this, unscored renders as a literal "0" pill instead of "—".
                        const score = compositeRaw != null && Number.isFinite(Number(compositeRaw)) ? Math.round(Number(compositeRaw)) : null;
                        const scoreClass = score == null ? '' : score >= 80 ? 'hi' : score >= 60 ? 'mid' : 'lo';
                        const stageLabel = (PIPELINE_STAGE_ORDER.find((s) => s.key === stage)?.label) || (stage ? stage.replace(/_/g, ' ') : '—');
                        const statusText = resolvePipelineCardFooterStatus(application);
                        const pendingDecision = pendingAgentDecisions[application?.id] || null;
                        const agentLabel = pendingDecision?.recommendation
                          || (stage === 'review' && score != null && score >= 75 ? 'Advance recommended'
                            : stage === 'review' && score != null && score < 50 ? 'Reject recommended'
                            : null);
                        const isAgentRow = Boolean(agentLabel) && stage === 'review';
                        const isTriageRow = (
                          triageApplication
                          && Number(triageApplication.id) === Number(application.id)
                        );
                        const isSelected = selectedAppIds.has(application.id);
                        return (
                          <React.Fragment key={application.id}>
                            <tr
                              className={isAgentRow ? 'agent-row' : ''}
                              onClick={(event) => handlePipelineReportClick(event, application)}
                              onMouseEnter={() => prefetchDocumentBlob({ applicationId: application.id, docType: 'cv' })}
                              style={{ cursor: 'pointer' }}
                            >
                              <td onClick={(e) => e.stopPropagation()} style={{ width: 28 }}><input type="checkbox" aria-label={`Select ${buildApplicationTitle(application)}`} checked={isSelected} onChange={() => { const next = new Set(selectedAppIds); if (next.has(application.id)) next.delete(application.id); else next.add(application.id); setSelectedAppIds(next); }} /></td>
                              <td>
                                <div className="name">{buildApplicationTitle(application)}</div>
                                <div className="sub">
                                  {application?.candidate_position
                                    || application?.candidate_email
                                    || 'No position captured'}
                                </div>
                              </td>
                              <td>
                                {renderJobPipelineScoreCell(score, scoreClass, application?.score_status)}
                              </td>
                              <td>
                                <span className="stage-pill">{stageLabel}</span>
                              </td>
                              <td>{application?.workable_disqualified ? (<span className="stage-pill is-disqualified" title={application?.workable_stage ? `Disqualified in Workable (was: ${formatStatusLabel(application.workable_stage)})` : 'Disqualified in Workable'}>Disqualified</span>) : application?.workable_stage ? (<span className="stage-pill" title="Current stage in Workable">{formatStatusLabel(application.workable_stage)}</span>) : (<span className="ctable-em">—</span>)}</td>
                              <td className="ctable-status">{statusText}</td>
                              <td>
                                {agentLabel ? (
                                  <span className="ai-action">
                                    <Sparkles size={11} strokeWidth={2} />
                                    {agentLabel}
                                  </span>
                                ) : (
                                  <span className="ctable-em">—</span>
                                )}
                              </td>
                              <td className="ctable-status" title={(application?.last_activity_at || application?.updated_at || application?.created_at) ? new Date(application.last_activity_at || application.updated_at || application.created_at).toLocaleString() : undefined}>{formatRelativeShort(application?.last_activity_at || application?.updated_at || application?.created_at)}</td>
                              <td>
                                <a
                                  href={candidateReportHref(application, numericRoleId)}
                                  className="btn btn-ghost btn-sm"
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    handlePipelineReportClick(event, application);
                                  }}
                                >
                                  View →
                                </a>
                              </td>
                            </tr>
                            {isTriageRow ? (
                              <tr className="ctable-triage-row">
                                <td colSpan={9} className="ctable-triage-cell">
                                  <CandidateTriageDrawer {...triageDrawerProps} />
                                </td>
                              </tr>
                            ) : null}
                          </React.Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              );
            })()}
          </>
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

        <ConfirmActionDialog
          open={confirmAction.open}
          title={confirmAction.title}
          description={confirmAction.description}
          bullets={confirmAction.bullets}
          warning={confirmAction.warning}
          confirmLabel={confirmAction.confirmLabel || 'Confirm'}
          variant={confirmAction.variant || 'primary'}
          loading={confirmAction.loading}
          loadingLabel={confirmAction.dryRunLoading ? 'Loading…' : 'Starting…'}
          disabled={confirmAction.dryRunLoading}
          onClose={closeConfirm}
          onConfirm={runConfirmedAction}
        />

        <ProcessCandidatesDialog
          open={processDialogOpen}
          roleId={numericRoleId}
          stage={tableStageFilter}
          stageLabel={tableStageFilter === 'all' ? null : tableStageFilter === 'rejected' ? 'Rejected' : (PIPELINE_STAGE_ORDER.find((s) => s.key === tableStageFilter)?.label || tableStageFilter)}
          applicationIds={selectedAppIds.size > 0 ? Array.from(selectedAppIds) : null}
          onClose={() => setProcessDialogOpen(false)}
          onConfirm={async (body) => {
            try {
              const res = await rolesApi.processRole(numericRoleId, body);
              const payload = res?.data ?? {};
              if (payload.status === 'already_running') {
                showToast('This role is already being processed.', 'info');
              } else {
                // No success toast — the persistent BackgroundJobsToaster
                // already shows the cascade progress in the bottom-right.
                // Two surfaces for the same event was visual noise.
                trackRoleProcess?.(numericRoleId);
                // Clear selection now that the cascade has been launched
                // — leaving it ticked would suggest the next click still
                // targets the same rows when actually they're now mid-run.
                setSelectedAppIds(new Set());
              }
              setProcessDialogOpen(false);
            } catch (error) {
              showToast(getErrorMessage(error, 'Failed to start.'), 'error');
            }
          }}
        />
          </div>

        {/* The legacy slide-out <AgentSettingsPanel scope="role"> drawer
            was retired — the canvas-spec Agent settings tab on this page
            owns the same controls inline (hero banner ON/OFF + budget
            sidebar + autonomy toggles + reject threshold + must-haves +
            pause threshold). Surfacing both was duplicate chrome. */}
      </div>
    </div>
  );
};

export default JobPipelinePage;
