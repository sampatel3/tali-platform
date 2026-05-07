import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  ArrowUpDown,
  BriefcaseBusiness,
  Check,
  ChevronDown,
  Edit3,
  Loader2,
  Settings2,
  Share2,
  Sparkles,
  X,
} from 'lucide-react';

import * as apiClient from '../../shared/api';
import { prefetchDocumentBlob } from '../../shared/api/documentCache';
import { useToast } from '../../context/ToastContext';
import { useJobStatus } from '../../contexts/JobStatusContext';
import { Spinner } from '../../shared/ui/TaaliPrimitives';
import { ConfirmActionDialog } from '../../shared/ui/ConfirmActionDialog';
import { ProcessCandidatesDialog } from './ProcessCandidatesDialog';
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
import { candidateReportHref } from '../candidates/CandidateTriageDrawer';
import { RoleSheet } from '../candidates/RoleSheet';
import { getErrorMessage, trimOrUndefined } from '../candidates/candidatesUiUtils';

const EMPTY_PROGRESS = { status: 'idle', total: 0, scored: 0, errors: 0, include_scored: false };
const EMPTY_FETCH_PROGRESS = { status: 'idle', total: 0, fetched: 0, errors: 0 };
const EMPTY_PRE_SCREEN_PROGRESS = { status: 'idle', total: 0, processed: 0, errors: 0, refresh: false };
const EMPTY_CONFIRM = { open: false, action: null, bullets: [], loading: false, dryRunLoading: false };
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

const resolvePipelineCardFooterStatus = (application) => {
  const stage = String(application?.pipeline_stage || '').toLowerCase();
  if (stage === 'applied') return 'Not invited';
  if (stage === 'invited') return 'Awaiting start';
  if (stage === 'in_assessment') return 'Assessment live';
  if (stage === 'review') return 'Decision';
  return resolveAssessmentId(application) ? 'Assessment linked' : 'No task yet';
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

// RoleAgentSettingsTab — merged Agent settings panel per HANDOFF v2 §4.3.
// Hero banner with ON/OFF + CV scoring criteria editor + reject threshold +
// pipeline-distribution dot grid + autonomy toggles, with a sticky sidebar
// for budget / must-haves / pause threshold / audit footer.
const RoleAgentSettingsTab = ({
  role,
  agentEnabled,
  agentStatus = null,
  criteriaDraft,
  setCriteriaDraft,
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
  const [autonomy, setAutonomy] = React.useState({
    auto_invite_above: true,
    auto_reject_below: true,
    auto_advance_high_score: false,
    passive_outbound: false,
  });
  const setAutonomyField = (key, on) => setAutonomy((prev) => ({ ...prev, [key]: on }));

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
        {/* Hero banner */}
        <section className="mc-agent-settings-hero">
          <div className="mc-agent-settings-hero-glyph">
            <Settings2 size={20} strokeWidth={2} />
          </div>
          <div className="mc-agent-settings-hero-body">
            <div className="mc-kicker">HOW THE AGENT RUNS THIS ROLE</div>
            <div className="mc-agent-settings-hero-title">
              Agent mode is{' '}
              <span style={{ color: 'var(--purple)' }}>{agentEnabled ? 'ON' : 'OFF'}</span>
            </div>
            <p className="mc-agent-settings-hero-help">
              Overrides your <a href="#org-defaults" style={{ color: 'var(--purple)' }}>org defaults</a> for this role only. Toggle off to disable autonomous actions — the agent will still surface ranked candidates for review.
            </p>
          </div>
          <div className="mc-agent-settings-hero-aside">
            <span className={`mc-switch ${agentEnabled ? 'on' : ''}`} aria-label="Agent enabled" />
            <span className="mc-agent-settings-since">
              {agentEnabled ? 'ON · since this role was created' : 'OFF'}
            </span>
          </div>
        </section>

        {/* CV scoring criteria */}
        <section className="mc-agent-settings-card">
          <div className="mc-agent-settings-card-head">
            <div>
              <h2 className="mc-agent-settings-card-title">
                CV scoring <em>criteria</em>
              </h2>
              <p className="mc-agent-settings-card-help">
                Job spec is the default. Add recruiter guidance below to refine. The agent applies this automatically as new candidates arrive.
              </p>
            </div>
          </div>
          <div className="mc-agent-settings-callout">
            <span className="mc-agent-settings-callout-num">01</span>
            <span>Default: scores against the job spec + linked task. Add recruiter requirements below to weigh additional signals.</span>
            <span className="mc-agent-settings-callout-tag">JOB SPEC</span>
          </div>
          <div className="mc-agent-settings-textwrap">
            <div className="mc-agent-settings-texthead">
              <span>RECRUITER SCORING REQUIREMENTS</span>
              <span style={{ color: 'var(--purple)' }}>
                {recruiterCriteria.length
                  ? `${recruiterCriteria.length} line${recruiterCriteria.length === 1 ? '' : 's'}`
                  : 'No lines yet'}
              </span>
            </div>
            <textarea
              rows={6}
              className="mc-agent-settings-textarea"
              value={criteriaDraft}
              onChange={(event) => setCriteriaDraft(event.target.value)}
              placeholder={'Must have: 4+ yrs production Python or Go\nMust have: Postgres internals, query planning experience\nPreferred: On-call rotation at >100k req/min scale\nNice to have: Open-source or technical writing'}
            />
          </div>
        </section>

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
              {thresholdDisplay}<span className="mc-agent-settings-threshold-pct">%</span>
            </div>
          </div>
          <div className="mc-agent-settings-slider">
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={thresholdDisplay}
              onChange={(event) => setThresholdDraft(event.target.value)}
              aria-label="Reject threshold percent"
              className="mc-agent-settings-slider-input"
            />
            <div className="mc-agent-settings-slider-track">
              <div className="mc-agent-settings-slider-thumb" style={{ left: `${thresholdDisplay}%` }} />
            </div>
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
            What the agent can do on this role without asking.
          </p>
          {[
            {
              key: 'auto_invite_above',
              title: 'Auto-invite candidates scoring ≥ 75%',
              sub: 'Sends the assessment invite within hourly limits.',
            },
            {
              key: 'auto_reject_below',
              title: `Auto-reject candidates scoring < ${thresholdDisplay}%`,
              sub: 'Sends the role-specific reject template, logs to audit.',
            },
            {
              key: 'auto_advance_high_score',
              title: 'Auto-advance assessments scoring ≥ 85%',
              sub: 'Moves to Final Review without recruiter approval.',
            },
            {
              key: 'passive_outbound',
              title: 'Outbound to passive candidates',
              sub: 'Drafts and sends initial outreach for matched profiles.',
            },
          ].map((rule, idx) => (
            <label key={rule.key} className={`mc-agent-settings-rule ${idx === 0 ? '' : 'is-divided'}`}>
              <button
                type="button"
                className={`mc-switch ${autonomy[rule.key] ? 'on' : ''}`}
                onClick={() => setAutonomyField(rule.key, !autonomy[rule.key])}
                aria-pressed={Boolean(autonomy[rule.key])}
                aria-label={rule.title}
              />
              <div>
                <div className="mc-agent-settings-rule-title">{rule.title}</div>
                <div className="mc-agent-settings-rule-sub">{rule.sub}</div>
              </div>
            </label>
          ))}
        </section>

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
  const [roleTasks, setRoleTasks] = useState([]);
  const [allTasks, setAllTasks] = useState([]);
  const [roleApplications, setRoleApplications] = useState([]);
  const [fetchCvsProgress, setFetchCvsProgress] = useState(EMPTY_FETCH_PROGRESS);
  const [preScreenProgress, setPreScreenProgress] = useState(EMPTY_PRE_SCREEN_PROGRESS);
  const [confirmAction, setConfirmAction] = useState(EMPTY_CONFIRM);
  const [processDialogOpen, setProcessDialogOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [savingRoleConfig, setSavingRoleConfig] = useState(false);
  const [criteriaDraft, setCriteriaDraft] = useState('');
  const [thresholdDraft, setThresholdDraft] = useState('');
  const [refreshTick, setRefreshTick] = useState(0);
  const [interviewFocusGenerating, setInterviewFocusGenerating] = useState(false);
  const [detailsExpanded, setDetailsExpanded] = useState(false);
  const [activeView, setActiveView] = useState('table');
  // HANDOFF v2 §4 / canvas jobs-detail-candidates — primary stage filter
  // for the Candidates tab. The segmented row above the table toggles
  // this; the embedded directory re-mounts via key so its internal
  // `stageFilters` re-seeds from the new initial value.
  const [tableStageFilter, setTableStageFilter] = useState('all');
  const [tableSortBy, setTableSortBy] = useState('composite');
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
    setLoading(true);
    try {
      const [roleRes, tasksRes, applicationsRes, batchStatusRes, fetchStatusRes, preScreenStatusRes] = await Promise.all([
        rolesApi.get(numericRoleId),
        rolesApi.listTasks(numericRoleId),
        rolesApi.listApplications(numericRoleId, { sort_by: 'pre_screen_score', sort_order: 'desc' }),
        rolesApi.batchScoreStatus(numericRoleId),
        rolesApi.fetchCvsStatus(numericRoleId),
        rolesApi.batchPreScreenStatus(numericRoleId).catch(() => ({ data: EMPTY_PRE_SCREEN_PROGRESS })),
      ]);
      const nextRole = roleRes?.data || null;
      setRole(nextRole);
      setCriteriaDraft(nextRole?.additional_requirements || '');
      setThresholdDraft(nextRole?.auto_reject_threshold_100 != null ? String(nextRole.auto_reject_threshold_100) : '');
      setRoleTasks(Array.isArray(tasksRes?.data) ? tasksRes.data : []);
      setRoleApplications(Array.isArray(applicationsRes?.data) ? applicationsRes.data : []);
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
      setRole(null);
      setRoleTasks([]);
      setRoleApplications([]);
      showToast(getErrorMessage(error, 'Failed to load role pipeline.'), 'error');
    } finally {
      setLoading(false);
    }
  }, [numericRoleId, rolesApi, showToast, trackRole]);

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

  // ── Poll fetchCvs + pre-screen progress (these live locally, not in global context) ────
  // batch-score progress now lives in the global BackgroundJobsToaster context,
  // so we only poll fetchCvs and pre-screen here.
  useEffect(() => {
    if (!Number.isFinite(numericRoleId)) return undefined;
    const fetchRunning = String(fetchCvsProgress?.status || '').toLowerCase() === 'running';
    const preScreenRunning = String(preScreenProgress?.status || '').toLowerCase() === 'running';
    if (!fetchRunning && !preScreenRunning) return undefined;

    let cancelled = false;
    const poll = async () => {
      try {
        const [fetchStatusRes, preScreenStatusRes] = await Promise.all([
          rolesApi.fetchCvsStatus(numericRoleId),
          rolesApi.batchPreScreenStatus(numericRoleId).catch(() => ({ data: EMPTY_PRE_SCREEN_PROGRESS })),
        ]);
        if (cancelled) return;
        const nextFetch = fetchStatusRes?.data || EMPTY_FETCH_PROGRESS;
        const nextPre = preScreenStatusRes?.data || EMPTY_PRE_SCREEN_PROGRESS;
        const fetchWasRunning = String(fetchCvsProgress?.status || '').toLowerCase() === 'running';
        const preWasRunning = String(preScreenProgress?.status || '').toLowerCase() === 'running';
        const fetchNowRunning = String(nextFetch.status || '').toLowerCase() === 'running';
        const preNowRunning = String(nextPre.status || '').toLowerCase() === 'running';
        setFetchCvsProgress(nextFetch);
        setPreScreenProgress(nextPre);
        if (
          (fetchWasRunning && !fetchNowRunning)
          || (preWasRunning && !preNowRunning)
        ) {
          await loadRoleWorkspace();
          setRefreshTick((value) => value + 1);
        }
      } catch {
        if (!cancelled) {
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
  }, [fetchCvsProgress, preScreenProgress, loadRoleWorkspace, numericRoleId, rolesApi]);

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

  const groupedApplications = useMemo(() => PIPELINE_STAGE_ORDER.map((stage) => ({
    ...stage,
    items: activeApplications.filter((application) => String(application?.pipeline_stage || '').toLowerCase() === stage.key),
  })), [activeApplications]);

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
        additional_requirements: criteriaDraft.trim() || null,
        auto_reject_threshold_100: thresholdDraft === '' ? null : Number(normalizeThreshold(thresholdDraft)),
      });
      await loadRoleWorkspace();
      setRefreshTick((value) => value + 1);
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

  // OFF → ON. Activate agent mode for THIS role with the budget the
  // recruiter set in the panel. Backend requires monthly_usd_budget_cents
  // to be set when flipping agentic_mode_enabled to true.
  const handleActivateAgent = async (monthlyBudgetCents) => {
    if (!Number.isFinite(numericRoleId)) return;
    if (!Number.isFinite(monthlyBudgetCents) || monthlyBudgetCents <= 0) {
      showToast('Set a monthly cap greater than $0 before activating.', 'error');
      throw new Error('Invalid budget');
    }
    try {
      await rolesApi.update(numericRoleId, {
        agentic_mode_enabled: true,
        monthly_usd_budget_cents: monthlyBudgetCents,
      });
      await loadRoleWorkspace();
      const usd = (monthlyBudgetCents / 100).toFixed(monthlyBudgetCents % 100 === 0 ? 0 : 2);
      showToast(`Agent mode is on — Taali will work this role with a $${usd}/month cap.`, 'success');
    } catch (error) {
      const message = getErrorMessage(error, 'Failed to turn on agent mode.');
      showToast(message, 'error');
      throw new Error(message);
    }
  };

  // ON → OFF. Manual pause flips agentic_mode_enabled to false. The user
  // can re-enable from the same panel later.
  const handlePauseAgent = async () => {
    if (!Number.isFinite(numericRoleId)) return;
    try {
      await rolesApi.update(numericRoleId, { agentic_mode_enabled: false });
      await loadRoleWorkspace();
      showToast('Agent mode paused for this role.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to pause agent mode.'), 'error');
    }
  };

  // AUTO-PAUSED → ON. The role is still agentic_mode_enabled=true but
  // paused_at was set when the budget cap was reached. Re-PATCHing
  // agentic_mode_enabled=true clears paused_at server-side.
  const handleResumeAgent = async () => {
    if (!Number.isFinite(numericRoleId)) return;
    try {
      await rolesApi.update(numericRoleId, { agentic_mode_enabled: true });
      await loadRoleWorkspace();
      showToast('Agent mode resumed. Raise the monthly cap if you want it to keep going.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to resume agent mode.'), 'error');
    }
  };

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="jobs" onNavigate={onNavigate} /> : null}
      <AgentHeader
        kicker={`ROLE · #${role?.id || '—'}`}
        title={role?.name || 'Role'}
        backLink={{ label: 'All roles', onClick: () => onNavigate('jobs') }}
        actions={(
          <>
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
        <div className="sub-tabs sub-tabs-sticky">
          <div className="seg">
            <button type="button" className={activeView === 'table' ? 'active' : ''} onClick={() => setActiveView('table')}>Candidates</button>
            <button type="button" className={activeView === 'pipeline' ? 'active' : ''} onClick={() => setActiveView('pipeline')}>Pipeline</button>
            <button type="button" className={activeView === 'activity' ? 'active' : ''} onClick={() => setActiveView('activity')}>Job spec</button>
            <button type="button" className={activeView === 'role-fit' ? 'active' : ''} onClick={() => setActiveView('role-fit')}>Agent settings</button>
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

            {/* Role-level interview focus panel removed — interview guidance is per-candidate now,
                surfaced in the candidate score sheet (kit + screening pack). */}
          </div>
        ) : activeView === 'role-fit' ? (
          <RoleAgentSettingsTab
            role={role}
            agentEnabled={role?.agentic_mode_enabled !== false}
            agentStatus={agentStatus}
            criteriaDraft={criteriaDraft}
            setCriteriaDraft={setCriteriaDraft}
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
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() => {
                  // Toggle between composite (Taali score desc) and CV-match
                  // recency. `tableSortBy` is the directory's sort key —
                  // changing it bumps the table key so the embedded
                  // CandidatesDirectoryPage remounts with the new
                  // initialSortOption and re-fetches.
                  setTableSortBy((prev) => (prev === 'composite' ? 'cv' : 'composite'));
                }}
                aria-label="Sort table"
                title="Sort"
              >
                <ArrowUpDown size={12} />
                Sort: {tableSortBy === 'composite' ? 'Taali score' : 'CV match'}
              </button>
              {/* HANDOFF v2 §4 / canvas jobs-detail-candidates — primary
                  recruiter action is the cascade Process flow (Fetch CVs
                  → Pre-screen → Score → score), opened via
                  ProcessCandidatesDialog. The label flips to a live
                  status while the cascade is in flight, mirroring the
                  BackgroundJobsToaster state. */}
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
                    const label = step === 'fetch' ? 'Fetching CVs'
                      : step === 'pre_screen' ? 'Pre-screening'
                      : step === 'score' ? 'Scoring'
                      : 'Processing';
                    return (
                      <>
                        <Loader2 size={12} className="animate-spin" />
                        {label}…
                      </>
                    );
                  }
                  return (
                    <>
                      <Sparkles size={12} />
                      Process {activeApplications.length} candidate{activeApplications.length === 1 ? '' : 's'}
                    </>
                  );
                })()}
              </button>
            </div>
            {/* HANDOFF v2 §4 / canvas jobs-detail-candidates — clean
                ctable with Candidate / Score / Stage / Status / Agent /
                View →. Filtered by tableStageFilter, sorted client-side
                by tableSortBy. The full CandidatesDirectoryPage was too
                heavy here — it carried bulk-action chrome, pagination,
                NL-search, and filter chips that don't belong on the
                role detail page. The standalone /candidates route still
                uses the directory. */}
            {(() => {
              const activeStage = tableStageFilter;
              const filteredApps = activeStage === 'all'
                ? activeApplications
                : activeApplications.filter((a) => String(a?.pipeline_stage || '').toLowerCase() === activeStage);
              const cmpScore = (a) => {
                const raw = a?.score_summary?.taali_score
                  ?? a?.taali_score
                  ?? a?.assessment_score
                  ?? a?.cv_match_score;
                return Number.isFinite(Number(raw)) ? Number(raw) : -1;
              };
              const cmpCv = (a) => {
                const raw = a?.cv_match_scored_at;
                return raw ? new Date(raw).getTime() : 0;
              };
              const sorted = [...filteredApps].sort((a, b) => (
                tableSortBy === 'cv' ? cmpCv(b) - cmpCv(a) : cmpScore(b) - cmpScore(a)
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
              return (
                <div className="ctable-wrap">
                  <table className="ctable">
                    <thead>
                      <tr>
                        <th>Candidate</th>
                        <th>Score</th>
                        <th>Stage</th>
                        <th>Status</th>
                        <th>Agent</th>
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
                        const score = Number.isFinite(Number(compositeRaw)) ? Math.round(Number(compositeRaw)) : null;
                        const scoreClass = score == null ? '' : score >= 80 ? 'hi' : score >= 60 ? 'mid' : 'lo';
                        const stageLabel = (PIPELINE_STAGE_ORDER.find((s) => s.key === stage)?.label) || (stage ? stage.replace(/_/g, ' ') : '—');
                        const statusText = resolvePipelineCardFooterStatus(application);
                        const pendingDecision = pendingAgentDecisions[application?.id] || null;
                        const agentLabel = pendingDecision?.recommendation
                          || (stage === 'review' && score != null && score >= 75 ? 'Advance recommended'
                            : stage === 'review' && score != null && score < 50 ? 'Reject recommended'
                            : null);
                        const isAgentRow = Boolean(agentLabel) && stage === 'review';
                        return (
                          <tr
                            key={application.id}
                            className={isAgentRow ? 'agent-row' : ''}
                            onClick={(event) => handlePipelineReportClick(event, application)}
                            onMouseEnter={() => prefetchDocumentBlob({ applicationId: application.id, docType: 'cv' })}
                            style={{ cursor: 'pointer' }}
                          >
                            <td>
                              <div className="name">{buildApplicationTitle(application)}</div>
                              <div className="sub">
                                {application?.candidate_position
                                  || application?.candidate_email
                                  || 'No position captured'}
                              </div>
                            </td>
                            <td>
                              {score != null ? (
                                <span className={`score-pill ${scoreClass}`}>{score}</span>
                              ) : (
                                <span className="score-pill mid" style={{ opacity: 0.5 }}>—</span>
                              )}
                            </td>
                            <td>
                              <span className="stage-pill">{stageLabel}</span>
                            </td>
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
