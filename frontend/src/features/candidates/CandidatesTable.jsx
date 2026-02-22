import React, { useEffect, useMemo, useState } from 'react';
import { ExternalLink, GraduationCap, Briefcase, Github, Linkedin, Twitter, MapPin, UserPlus } from 'lucide-react';

import {
  Badge,
  Button,
  EmptyState,
  Panel,
  Select,
  TableShell,
} from '../../shared/ui/TaaliPrimitives';
import { TableRowSkeleton } from '../../shared/ui/Skeletons';
import { formatCvScore100, formatDateTime, statusVariant } from './candidatesUiUtils';

const COLUMN_STORAGE_KEY = 'taali_candidates_table_columns_v2';

const DEFAULT_COLUMN_PREFS = {
  workable_stage: true,
  workable_candidate_id: false,
  cv: true,
  added: true,
  email: false,
  position: false,
  source: false,
  headline: false,
  location: false,
  skills: false,
};

const PAGE_SIZE = 20;

const SOCIAL_ICONS = {
  linkedin: Linkedin,
  github: Github,
  twitter: Twitter,
};

function CandidateAvatar({ name, imageUrl, size = 32 }) {
  const initials = (name || '?')
    .split(/\s+/)
    .map((w) => w[0])
    .join('')
    .toUpperCase()
    .slice(0, 2);

  if (imageUrl) {
    return (
      <img
        src={imageUrl}
        alt=""
        className="rounded-full object-cover shrink-0"
        style={{ width: size, height: size }}
        onError={(e) => { e.target.style.display = 'none'; e.target.nextSibling && (e.target.nextSibling.style.display = 'flex'); }}
      />
    );
  }

  return (
    <div
      className="rounded-full bg-[var(--taali-primary)] text-white flex items-center justify-center text-xs font-bold shrink-0"
      style={{ width: size, height: size }}
    >
      {initials}
    </div>
  );
}

export const CandidatesTable = ({
  applications,
  loading,
  error,
  searchQuery = '',
  statusFilter = 'all',
  sortBy = 'cv_match_score',
  sortOrder = 'desc',
  roleTasks,
  canCreateAssessment,
  creatingAssessmentId,
  viewingApplicationId,
  generatingTaaliId,
  onChangeSort,
  onAddCandidate,
  onViewCandidate,
  onOpenCvSidebar,
  onCreateAssessment,
  onUploadCv,
  uploadingCvId,
  onGenerateTaaliCvAi,
  onEnrichCandidate,
}) => {
  const [composerApplicationId, setComposerApplicationId] = useState(null);
  const [detailsApplicationId, setDetailsApplicationId] = useState(null);
  const [taskByApplication, setTaskByApplication] = useState({});
  const [columnsOpen, setColumnsOpen] = useState(false);
  const [enrichingId, setEnrichingId] = useState(null);
  const [page, setPage] = useState(0);
  const [columnPrefs, setColumnPrefs] = useState(() => {
    try {
      if (typeof window === 'undefined') return DEFAULT_COLUMN_PREFS;
      const raw = window.localStorage.getItem(COLUMN_STORAGE_KEY);
      if (!raw) return DEFAULT_COLUMN_PREFS;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return DEFAULT_COLUMN_PREFS;
      return { ...DEFAULT_COLUMN_PREFS, ...parsed };
    } catch {
      return DEFAULT_COLUMN_PREFS;
    }
  });

  const sortableColumns = {
    cv_match_score: 'Taali AI (/100)',
    created_at: 'Added',
  };

  useEffect(() => {
    setComposerApplicationId(null);
    setDetailsApplicationId(null);
  }, [applications, roleTasks]);

  useEffect(() => {
    setPage(0);
  }, [applications, searchQuery, statusFilter, sortBy, sortOrder]);

  useEffect(() => {
    try {
      if (typeof window === 'undefined') return;
      window.localStorage.setItem(COLUMN_STORAGE_KEY, JSON.stringify(columnPrefs));
    } catch {
      // ignore persistence failures (e.g. private browsing)
    }
  }, [columnPrefs]);

  const filtered = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return applications.filter((app) => {
      if (statusFilter !== 'all' && (app.status || 'applied').toLowerCase() !== statusFilter) return false;
      if (!query) return true;
      return (
        [
          app.candidate_name,
          app.candidate_email,
          app.candidate_position,
          app.candidate_headline,
          app.candidate_location,
          app.status,
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
          .includes(query)
      );
    });
  }, [applications, searchQuery, statusFilter]);

  const handleSortToggle = (column) => {
    if (!onChangeSort || !sortableColumns[column]) return;
    if (column === sortBy) {
      onChangeSort(column, sortOrder === 'asc' ? 'desc' : 'asc');
      return;
    }
    onChangeSort(column, 'desc');
  };

  const renderSortIndicator = (column) => {
    if (sortBy !== column) return '-';
    return sortOrder === 'asc' ? '^' : 'v';
  };

  const hasClientFilters = Boolean(searchQuery.trim()) || statusFilter !== 'all';
  const totalFiltered = filtered.length;
  const totalPages = Math.max(1, Math.ceil(totalFiltered / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const startIndex = safePage * PAGE_SIZE;
  const pagedFiltered = filtered.slice(startIndex, startIndex + PAGE_SIZE);

  const showColumn = (key) => {
    if (key === 'candidate') return true;
    if (key === 'taali_ai') return true;
    if (key === 'send') return true;
    if (key === 'status') return true;
    return Boolean(columnPrefs[key]);
  };

  const visibleColumnOrder = useMemo(() => (
    [
      'candidate',
      'cv',
      'send',
      'taali_ai',
      'workable_stage',
      'workable_candidate_id',
      'status',
      'headline',
      'location',
      'skills',
      'added',
      'email',
      'position',
      'source',
    ].filter(showColumn)
  ), [columnPrefs]);

  const columnCount = visibleColumnOrder.length;

  const columnLabel = (column) => ({
    candidate: 'Candidate',
    send: 'Send assessment',
    cv: 'CV',
    taali_ai: 'Taali AI (/100)',
    workable_stage: 'Workable stage',
    workable_candidate_id: 'Workable id',
    status: 'Status',
    headline: 'Headline',
    location: 'Location',
    skills: 'Skills',
    added: 'Added',
    email: 'Email',
    position: 'Position',
    source: 'Source',
  }[column] || column);

  const togglePref = (key) => {
    setColumnPrefs((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const renderTaaliScore = (app) => {
    if (typeof app.cv_match_score === 'number') return formatCvScore100(app.cv_match_score, app.cv_match_details);
    if (!app.cv_filename) return '—';
    if (app.cv_match_details?.error) return 'Unavailable';
    return 'Pending';
  };

  const renderTaaliError = (app) => {
    const raw = app?.cv_match_details?.error;
    if (!raw || typeof raw !== 'string') return null;
    const normalized = raw.trim();
    if (!normalized) return null;
    const lower = normalized.toLowerCase();
    if (lower.includes('not_found_error') && lower.includes('model')) {
      return 'Claude model not found on provider; retrying with supported Haiku fallback.';
    }
    if (normalized.length > 180) return `${normalized.slice(0, 180)}...`;
    return normalized;
  };

  const buildScoreWhySections = (app) => {
    const details = (app?.cv_match_details && typeof app.cv_match_details === 'object')
      ? app.cv_match_details
      : {};
    const normalizeList = (value, maxItems = 4) => (
      Array.isArray(value)
        ? value
          .map((item) => String(item || '').trim())
          .filter(Boolean)
          .slice(0, maxItems)
        : []
    );
    const toReason = (text) => {
      const cleaned = String(text || '').trim();
      if (!cleaned) return null;
      return cleaned.endsWith('.') ? cleaned : `${cleaned}.`;
    };
    const compactText = (value, maxChars = 180) => {
      const cleaned = String(value || '').replace(/\s+/g, ' ').trim();
      if (!cleaned) return '';
      if (cleaned.length <= maxChars) return cleaned;
      return `${cleaned.slice(0, maxChars - 1).trimEnd()}…`;
    };

    const cvReasons = [];
    const requirementsReasons = [];

    const matchingSkills = normalizeList(details.matching_skills, 4);
    const experienceHighlights = normalizeList(details.experience_highlights, 2);
    const missingSkills = normalizeList(details.missing_skills, 4);
    const concerns = normalizeList(details.concerns, 2);

    if (matchingSkills.length > 0) {
      cvReasons.push(toReason(`Strong skill alignment: ${matchingSkills.join(', ')}`));
    }
    if (experienceHighlights.length > 0) {
      cvReasons.push(toReason(`Relevant experience evidence: ${experienceHighlights.join('; ')}`));
    }
    if (missingSkills.length > 0) {
      cvReasons.push(toReason(`Gaps vs role spec: ${missingSkills.join(', ')}`));
    }
    if (concerns.length > 0) {
      cvReasons.push(toReason(`Risk signals from CV: ${concerns.join('; ')}`));
    }

    const requirementsCoverage = (details.requirements_coverage && typeof details.requirements_coverage === 'object')
      ? details.requirements_coverage
      : {};
    const requirementsAssessment = Array.isArray(details.requirements_assessment)
      ? details.requirements_assessment
      : [];

    if (typeof details.requirements_match_score_100 === 'number') {
      requirementsReasons.push(
        toReason(`Additional requirements fit score: ${formatCvScore100(details.requirements_match_score_100, details)}`)
      );
    }

    const statusRank = (status) => {
      if (status === 'met') return 0;
      if (status === 'partially_met') return 1;
      if (status === 'missing') return 2;
      return 3;
    };
    const priorityRank = (priority) => {
      if (priority === 'must_have') return 0;
      if (priority === 'constraint') return 1;
      if (priority === 'strong_preference') return 2;
      return 3;
    };
    const requirementEvidenceReasons = requirementsAssessment
      .map((item) => {
        const requirement = compactText(item?.requirement, 150);
        if (!requirement) return null;
        const status = String(item?.status || 'unknown').toLowerCase();
        const priority = String(item?.priority || 'nice_to_have').toLowerCase();
        const evidence = compactText(item?.evidence, 180);
        const impact = compactText(item?.impact, 180);
        const whyText = evidence || impact;
        let prefix = 'Unclear evidence';
        if (status === 'met') prefix = 'Met';
        else if (status === 'partially_met') prefix = 'Partially met';
        else if (status === 'missing') prefix = 'Missing';

        let sentence = `${prefix}: ${requirement}`;
        if (whyText) {
          sentence += ` because ${whyText}`;
        } else if (status === 'missing') {
          sentence += ' because no clear CV evidence was found';
        }

        return {
          text: toReason(sentence),
          statusRank: statusRank(status),
          priorityRank: priorityRank(priority),
        };
      })
      .filter(Boolean)
      .sort((a, b) => (
        (a.statusRank - b.statusRank)
        || (a.priorityRank - b.priorityRank)
      ))
      .slice(0, 3)
      .map((item) => item.text);
    if (requirementEvidenceReasons.length > 0) {
      requirementsReasons.push(...requirementEvidenceReasons);
    }

    const totalReq = Number(requirementsCoverage.total || 0);
    if (totalReq > 0 && requirementsReasons.length < 4) {
      requirementsReasons.push(
        toReason(
          `Coverage: ${requirementsCoverage.met ?? 0}/${totalReq} met, ${requirementsCoverage.partially_met ?? 0} partial, ${requirementsCoverage.missing ?? 0} missing`
        )
      );
    }

    const missingCriticalReqs = requirementsAssessment
      .filter((item) => (
        String(item?.status || '').toLowerCase() === 'missing'
        && ['must_have', 'constraint'].includes(String(item?.priority || '').toLowerCase())
      ))
      .map((item) => String(item?.requirement || '').trim())
      .filter(Boolean)
      .slice(0, 2);
    if (missingCriticalReqs.length > 0 && requirementsReasons.length < 4) {
      requirementsReasons.push(toReason(`Critical requirement gaps: ${missingCriticalReqs.join('; ')}`));
    }

    const modelRationale = normalizeList(details.score_rationale_bullets, 6);
    modelRationale.forEach((bullet) => {
      const lower = bullet.toLowerCase();
      if (lower.includes('requirement') && requirementsReasons.length < 4) {
        requirementsReasons.push(toReason(bullet));
      } else if (requirementsReasons.length < 4) {
        cvReasons.push(toReason(bullet));
      }
    });

    const dedupe = (items, maxItems = 3) => {
      const seen = new Set();
      const out = [];
      for (const item of items) {
        const text = String(item || '').trim();
        if (!text) continue;
        const key = text.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        out.push(text);
        if (out.length >= maxItems) break;
      }
      return out;
    };

    return {
      cvFit: dedupe(cvReasons, 3),
      additionalRequirementsFit: dedupe(requirementsReasons, 4),
    };
  };

  const handleEnrich = async (app) => {
    if (!onEnrichCandidate) return;
    setEnrichingId(app.id);
    try {
      await onEnrichCandidate(app);
    } finally {
      setEnrichingId(null);
    }
  };

  const renderSocialLinks = (socials) => {
    if (!Array.isArray(socials) || socials.length === 0) return null;
    return (
      <div className="flex items-center gap-1.5">
        {socials.map((s, i) => {
          const type = (s.type || '').toLowerCase();
          const Icon = SOCIAL_ICONS[type] || ExternalLink;
          const url = s.url || '#';
          return (
            <a
              key={`${type}-${i}`}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-gray-400 hover:text-gray-700 transition-colors"
              title={s.name || type}
            >
              <Icon size={14} />
            </a>
          );
        })}
      </div>
    );
  };

  if (loading) {
    return (
      <div className="space-y-2">
        <div className="px-1">
          <div className="h-3 w-28 animate-pulse rounded bg-[var(--taali-border)]" />
        </div>
        <TableShell className="max-h-[68vh]">
          <table className="w-full table-fixed min-w-[900px]">
            <thead>
              <tr className="text-left text-xs font-semibold uppercase tracking-[0.08em] text-gray-600">
                {visibleColumnOrder.map((column) => (
                  <th key={column} className="sticky top-0 z-20 bg-[#f4f1fb] px-3 py-2">
                    {columnLabel(column)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: 8 }).map((_, index) => (
                <TableRowSkeleton key={`candidates-skeleton-${index}`} cols={columnCount} />
              ))}
            </tbody>
          </table>
        </TableShell>
      </div>
    );
  }

  if (error) {
    return (
      <Panel className="px-4 py-10 text-center text-sm border-red-200 bg-red-50 text-red-700">
        {error}
      </Panel>
    );
  }

  if (filtered.length === 0) {
    return (
      <EmptyState
        title={hasClientFilters ? 'No matching candidates' : 'No candidates yet'}
        description={hasClientFilters
          ? 'Adjust search or status filters to see more candidates.'
          : 'Add your first candidate to this role and start assessments.'}
        action={(
          <Button type="button" variant="primary" size="sm" onClick={onAddCandidate}>
            <UserPlus size={15} />
            Add candidate
          </Button>
        )}
      />
    );
  }

  const allColumnCheckboxes = [
    { key: 'cv', label: 'CV status' },
    { key: 'workable_stage', label: 'Workable stage' },
    { key: 'workable_candidate_id', label: 'Workable candidate id' },
    { key: 'headline', label: 'Headline' },
    { key: 'location', label: 'Location' },
    { key: 'skills', label: 'Skills' },
    { key: 'added', label: 'Added date' },
    { key: 'email', label: 'Email' },
    { key: 'position', label: 'Position' },
    { key: 'source', label: 'Source' },
  ];

  return (
    <div className="space-y-2">
      <div className="relative flex flex-wrap items-center justify-between gap-2 px-1">
        <p className="text-xs text-gray-500">
          {filtered.length}
          {' '}
          candidate{filtered.length === 1 ? '' : 's'}
        </p>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setColumnsOpen((prev) => !prev)}
          >
            Columns
          </Button>
        </div>
        {columnsOpen ? (
          <div className="absolute right-1 top-full z-40 mt-2 w-[280px] border-2 border-[var(--taali-border)] bg-[var(--taali-surface)] p-3 shadow-sm">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-600">
              Show columns
            </p>
            <div className="mt-2 grid gap-2">
              {allColumnCheckboxes.map(({ key, label }) => (
                <label key={key} className="flex items-center gap-2 text-sm text-gray-700">
                  <input
                    type="checkbox"
                    checked={Boolean(columnPrefs[key])}
                    onChange={() => togglePref(key)}
                  />
                  {label}
                </label>
              ))}
            </div>
            <div className="mt-3 flex justify-end">
              <Button type="button" variant="secondary" size="sm" onClick={() => setColumnsOpen(false)}>
                Close
              </Button>
            </div>
          </div>
        ) : null}
      </div>
      <TableShell className="max-h-[68vh]">
        <table className="w-full table-fixed min-w-[900px]">
          <thead>
          <tr className="text-left text-xs font-semibold uppercase tracking-[0.08em] text-gray-600">
            {visibleColumnOrder.map((column) => {
              const isStickyLeft = column === 'candidate';
              const thBase = isStickyLeft
                ? 'sticky left-0 top-0 z-30 bg-[#f4f1fb]'
                : 'sticky top-0 z-20 bg-[#f4f1fb]';
              const widthClass = {
                candidate: 'w-[280px]',
                cv: 'w-[120px]',
                taali_ai: 'w-[110px]',
                send: 'w-[170px]',
                workable_stage: 'w-[150px]',
                workable_candidate_id: 'w-[200px]',
                status: 'w-[140px]',
                headline: 'w-[200px]',
                location: 'w-[160px]',
                skills: 'w-[200px]',
                added: 'w-[140px]',
                email: 'w-[220px]',
                position: 'w-[200px]',
                source: 'w-[120px]',
              }[column] || '';

              if (column === 'candidate') {
                return (
                  <th key={column} className={`${thBase} ${widthClass} px-3 py-2`}>Candidate</th>
                );
              }

              if (column === 'taali_ai') {
                return (
                  <th
                    key={column}
                    className={`${thBase} ${widthClass} px-3 py-2`}
                    aria-sort={sortBy === 'cv_match_score' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 uppercase tracking-[0.08em] text-gray-600 transition-colors hover:text-gray-900"
                      onClick={() => handleSortToggle('cv_match_score')}
                    >
                      Taali AI (/100)
                      <span className="text-[0.65rem] text-gray-500">{renderSortIndicator('cv_match_score')}</span>
                    </button>
                  </th>
                );
              }

              if (column === 'added') {
                return (
                  <th
                    key={column}
                    className={`${thBase} ${widthClass} px-3 py-2`}
                    aria-sort={sortBy === 'created_at' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 uppercase tracking-[0.08em] text-gray-600 transition-colors hover:text-gray-900"
                      onClick={() => handleSortToggle('created_at')}
                    >
                      Added
                      <span className="text-[0.65rem] text-gray-500">{renderSortIndicator('created_at')}</span>
                    </button>
                  </th>
                );
              }

              const label = {
                send: 'Send assessment',
                cv: 'CV',
                workable_stage: 'Workable stage',
                workable_candidate_id: 'Workable id',
                status: 'Status',
                headline: 'Headline',
                location: 'Location',
                skills: 'Skills',
                email: 'Email',
                position: 'Position',
                source: 'Source',
              }[column] || column;

              return (
                <th key={column} className={`${thBase} ${widthClass} px-3 py-2`}>{label}</th>
              );
            })}
          </tr>
          </thead>
          <tbody>
          {pagedFiltered.map((app) => {
            const selectedTask = taskByApplication[app.id] || (roleTasks.length === 1 ? String(roleTasks[0].id) : '');
            const canOpenComposer = Boolean(canCreateAssessment && roleTasks.length > 0);
            const cvUploadInputId = `cv-upload-${app.id}`;

            return (
              <React.Fragment key={app.id}>
                <tr className="group align-top border-b border-[var(--taali-border)] hover:bg-[var(--taali-surface-hover,rgba(0,0,0,0.04))] transition-colors">
                  {visibleColumnOrder.map((column) => {
                    if (column === 'candidate') {
                      return (
                        <td
                          key={column}
                          className="sticky left-0 z-10 bg-[var(--taali-surface)] px-3 py-2 text-sm group-hover:bg-[var(--taali-surface-hover,rgba(0,0,0,0.04))]"
                        >
                          <div className="flex items-start gap-2.5">
                            <CandidateAvatar
                              name={app.candidate_name}
                              imageUrl={app.candidate_image_url}
                              size={32}
                            />
                            <div className="min-w-0 flex-1">
                              <button
                                type="button"
                                className="block w-full text-left font-semibold text-gray-900 hover:underline truncate"
                                onClick={() => onViewCandidate(app)}
                                disabled={viewingApplicationId === app.id}
                              >
                                {app.candidate_name || app.candidate_email}
                              </button>
                              {app.candidate_headline ? (
                                <p className="text-xs text-gray-500 truncate">{app.candidate_headline}</p>
                              ) : null}
                              {app.candidate_location ? (
                                <p className="text-[11px] text-gray-400 truncate flex items-center gap-0.5">
                                  <MapPin size={10} />
                                  {app.candidate_location}
                                </p>
                              ) : null}
                              <div className="mt-1 flex flex-wrap items-center gap-2">
                                <button
                                  type="button"
                                  className="text-xs text-gray-500 underline decoration-gray-300 underline-offset-2 hover:text-gray-800"
                                  onClick={() => {
                                    setDetailsApplicationId((current) => (current === app.id ? null : app.id));
                                  }}
                                >
                                  {detailsApplicationId === app.id ? 'Hide details' : 'Details'}
                                </button>
                                {onOpenCvSidebar ? (
                                  <button
                                    type="button"
                                    className="text-xs text-[var(--taali-primary)] font-medium hover:underline"
                                    onClick={() => onOpenCvSidebar(app)}
                                  >
                                    View CV
                                  </button>
                                ) : null}
                                {app.source === 'workable' ? (
                                  <span className="text-xs text-gray-500">Workable</span>
                                ) : null}
                              </div>
                            </div>
                          </div>
                        </td>
                      );
                    }

                    if (column === 'taali_ai') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 text-right tabular-nums whitespace-nowrap">
                          {renderTaaliScore(app)}
                        </td>
                      );
                    }

                    if (column === 'cv') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 whitespace-nowrap">
                          {app.cv_filename ? (
                            <span title={app.cv_filename}>📄 Uploaded</span>
                          ) : (
                            <span className="text-gray-500">— Missing</span>
                          )}
                        </td>
                      );
                    }

                    if (column === 'send') {
                      return (
                        <td key={column} className="px-3 py-2">
                          {canCreateAssessment ? (
                            <div className="flex flex-col items-start gap-1">
                              <Button
                                type="button"
                                variant="primary"
                                size="sm"
                                onClick={() => {
                                  if (composerApplicationId === app.id) {
                                    setComposerApplicationId(null);
                                    return;
                                  }
                                  if (roleTasks.length === 1) {
                                    setTaskByApplication((prev) => ({ ...prev, [app.id]: String(roleTasks[0].id) }));
                                  }
                                  setComposerApplicationId(app.id);
                                }}
                                disabled={!canOpenComposer}
                                className="whitespace-nowrap"
                              >
                                Send assessment
                              </Button>
                              {!app.cv_filename ? (
                                <span className="text-[0.7rem] font-semibold text-amber-700">No CV (role fit N/A)</span>
                              ) : null}
                              {roleTasks.length === 0 ? (
                                <span className="text-[0.7rem] font-semibold text-amber-700">Link a task first</span>
                              ) : null}
                            </div>
                          ) : (
                            <span className="text-sm text-gray-500">—</span>
                          )}
                        </td>
                      );
                    }

                    if (column === 'workable_stage') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 break-words">
                          {app.workable_stage || '—'}
                        </td>
                      );
                    }

                    if (column === 'workable_candidate_id') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 break-all">
                          {app.workable_candidate_id || '—'}
                        </td>
                      );
                    }

                    if (column === 'status') {
                      return (
                        <td key={column} className="px-3 py-2">
                          <Badge variant={statusVariant(app.status)}>{app.status || 'applied'}</Badge>
                        </td>
                      );
                    }

                    if (column === 'headline') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 truncate">
                          {app.candidate_headline || '—'}
                        </td>
                      );
                    }

                    if (column === 'location') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 truncate">
                          {app.candidate_location || '—'}
                        </td>
                      );
                    }

                    if (column === 'skills') {
                      const skills = Array.isArray(app.candidate_skills) ? app.candidate_skills : [];
                      return (
                        <td key={column} className="px-3 py-2">
                          {skills.length > 0 ? (
                            <div className="flex flex-wrap gap-1">
                              {skills.slice(0, 3).map((s) => (
                                <Badge key={s} variant="muted">{s}</Badge>
                              ))}
                              {skills.length > 3 ? (
                                <span className="text-xs text-gray-400">+{skills.length - 3}</span>
                              ) : null}
                            </div>
                          ) : '—'}
                        </td>
                      );
                    }

                    if (column === 'added') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 whitespace-nowrap">
                          {formatDateTime(app.created_at)}
                        </td>
                      );
                    }

                    if (column === 'email') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 break-all">
                          {app.candidate_email || '—'}
                        </td>
                      );
                    }

                    if (column === 'position') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 break-words">
                          {app.candidate_position || '—'}
                        </td>
                      );
                    }

                    if (column === 'source') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 whitespace-nowrap">
                          {app.source || 'manual'}
                        </td>
                      );
                    }

                    return null;
                  })}
                </tr>

                {composerApplicationId === app.id ? (
                  <tr className="bg-[#faf8ff]">
                    <td colSpan={columnCount} className="px-3 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Select
                          value={selectedTask}
                          onChange={(event) => {
                            setTaskByApplication((prev) => ({ ...prev, [app.id]: event.target.value }));
                          }}
                          className="min-w-[240px]"
                        >
                          <option value="">Select task...</option>
                          {roleTasks.map((task) => (
                            <option key={task.id} value={task.id}>{task.name}</option>
                          ))}
                        </Select>
                        <Button
                          type="button"
                          variant="primary"
                          size="sm"
                          onClick={async () => {
                            const success = await onCreateAssessment(app, selectedTask);
                            if (success) setComposerApplicationId(null);
                          }}
                          disabled={!selectedTask || creatingAssessmentId === app.id}
                        >
                          {creatingAssessmentId === app.id ? 'Creating...' : 'Send assessment'}
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => setComposerApplicationId(null)}
                        >
                          Cancel
                        </Button>
                      </div>
                    </td>
                  </tr>
                ) : null}

                {detailsApplicationId === app.id ? (
                  <tr className="bg-[#faf8ff]">
                    <td colSpan={columnCount} className="px-3 py-4">
                      {(() => {
                        const scoreWhy = buildScoreWhySections(app);
                        return (
                          <>
                      {/* Profile header */}
                      <div className="flex items-start gap-4 mb-4">
                        <CandidateAvatar
                          name={app.candidate_name}
                          imageUrl={app.candidate_image_url}
                          size={56}
                        />
                        <div className="flex-1 min-w-0">
                          <p className="text-base font-bold text-gray-900">{app.candidate_name || app.candidate_email}</p>
                          {app.candidate_headline ? (
                            <p className="text-sm text-gray-600">{app.candidate_headline}</p>
                          ) : null}
                          <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-gray-500">
                            {app.candidate_location ? (
                              <span className="inline-flex items-center gap-0.5">
                                <MapPin size={12} />
                                {app.candidate_location}
                              </span>
                            ) : null}
                            {app.candidate_email ? (
                              <span>{app.candidate_email}</span>
                            ) : null}
                            {app.candidate_phone ? (
                              <span>{app.candidate_phone}</span>
                            ) : null}
                          </div>
                          <div className="mt-1.5 flex items-center gap-2">
                            {renderSocialLinks(app.candidate_social_profiles)}
                            {app.workable_profile_url ? (
                              <a
                                href={app.workable_profile_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-xs text-[var(--taali-primary)] hover:underline"
                              >
                                View in Workable
                              </a>
                            ) : null}
                          </div>
                        </div>
                      </div>

                      {/* Skills & Tags */}
                      {(Array.isArray(app.candidate_skills) && app.candidate_skills.length > 0) ||
                       (Array.isArray(app.candidate_tags) && app.candidate_tags.length > 0) ? (
                        <div className="mb-4">
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500 mb-1.5">Skills & Tags</p>
                          <div className="flex flex-wrap gap-1.5">
                            {(app.candidate_skills || []).map((s) => (
                              <Badge key={`skill-${s}`} variant="muted">{s}</Badge>
                            ))}
                            {(app.candidate_tags || []).map((t) => (
                              <Badge key={`tag-${t}`} variant="muted">{t}</Badge>
                            ))}
                          </div>
                        </div>
                      ) : null}

                      <div className="grid gap-4 md:grid-cols-2">
                        {/* Experience */}
                        {Array.isArray(app.candidate_experience) && app.candidate_experience.length > 0 ? (
                          <div>
                            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500 mb-1.5 flex items-center gap-1">
                              <Briefcase size={12} />
                              Experience
                            </p>
                            <div className="space-y-1.5">
                              {app.candidate_experience.map((exp, i) => (
                                <div key={i} className="text-sm text-gray-700">
                                  <p className="font-medium">
                                    {exp.title || 'Role'}{exp.company ? ` at ${exp.company}` : ''}
                                    {exp.current ? ' (current)' : ''}
                                  </p>
                                  {(exp.start_date || exp.end_date) ? (
                                    <p className="text-xs text-gray-400">
                                      {exp.start_date || '?'} — {exp.current ? 'Present' : (exp.end_date || '?')}
                                    </p>
                                  ) : null}
                                </div>
                              ))}
                            </div>
                          </div>
                        ) : null}

                        {/* Education */}
                        {Array.isArray(app.candidate_education) && app.candidate_education.length > 0 ? (
                          <div>
                            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500 mb-1.5 flex items-center gap-1">
                              <GraduationCap size={12} />
                              Education
                            </p>
                            <div className="space-y-1.5">
                              {app.candidate_education.map((edu, i) => (
                                <div key={i} className="text-sm text-gray-700">
                                  <p className="font-medium">
                                    {[edu.degree, edu.field_of_study].filter(Boolean).join(', ') || 'Degree'}
                                    {edu.school ? `, ${edu.school}` : ''}
                                  </p>
                                  {(edu.start_date || edu.end_date) ? (
                                    <p className="text-xs text-gray-400">
                                      {edu.start_date || '?'} — {edu.end_date || '?'}
                                    </p>
                                  ) : null}
                                </div>
                              ))}
                            </div>
                          </div>
                        ) : null}
                      </div>

                      {/* Summary */}
                      {app.candidate_summary ? (
                        <div className="mt-4">
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500 mb-1">Summary</p>
                          <p className="text-sm text-gray-700 whitespace-pre-wrap">{app.candidate_summary}</p>
                        </div>
                      ) : null}

                      {/* Scores */}
                      <div className="mt-4 grid gap-3 md:grid-cols-2">
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">Taali score (/100)</p>
                          <p className="mt-1 text-sm text-gray-800">{renderTaaliScore(app)}</p>
                          {renderTaaliError(app) ? (
                            <p className="mt-1 text-xs text-amber-700">{renderTaaliError(app)}</p>
                          ) : null}
                          {(scoreWhy.cvFit.length > 0 || scoreWhy.additionalRequirementsFit.length > 0) ? (
                            <div className="mt-3 space-y-2">
                              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">Why this score</p>
                              {scoreWhy.cvFit.length > 0 ? (
                                <div>
                                  <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-gray-500">CV fit</p>
                                  <ul className="mt-1 space-y-1">
                                    {scoreWhy.cvFit.map((reason, index) => (
                                      <li key={`cv-fit-reason-${index}`} className="flex items-start gap-1.5 text-xs text-gray-700">
                                        <span className="text-[var(--taali-success)]">•</span>
                                        <span>{reason}</span>
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              ) : null}
                              {scoreWhy.additionalRequirementsFit.length > 0 ? (
                                <div>
                                  <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-gray-500">Additional requirements fit</p>
                                  <ul className="mt-1 space-y-1">
                                    {scoreWhy.additionalRequirementsFit.map((reason, index) => (
                                      <li key={`req-fit-reason-${index}`} className="flex items-start gap-1.5 text-xs text-gray-700">
                                        <span className="text-[var(--taali-success)]">•</span>
                                        <span>{reason}</span>
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              ) : null}
                            </div>
                          ) : null}
                          {typeof app.cv_match_score !== 'number' && typeof onGenerateTaaliCvAi === 'function' ? (
                            <div className="mt-2">
                              <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                onClick={() => onGenerateTaaliCvAi(app)}
                                disabled={generatingTaaliId === app.id}
                              >
                                {generatingTaaliId === app.id ? 'Scoring...' : 'Generate TAALI Score'}
                              </Button>
                            </div>
                          ) : null}
                        </div>
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">CV</p>
                          <p className="mt-1 text-sm text-gray-800">{app.cv_filename || '—'}</p>
                        </div>
                      </div>

                      {/* Actions */}
                      <div className="mt-4 flex flex-wrap items-center gap-2">
                        {onOpenCvSidebar ? (
                          <Button type="button" variant="secondary" size="sm" onClick={() => onOpenCvSidebar(app)}>
                            View CV
                          </Button>
                        ) : null}
                        {!app.cv_filename && onUploadCv ? (
                          <>
                            <input
                              id={cvUploadInputId}
                              type="file"
                              accept=".pdf,.docx,.doc"
                              className="sr-only"
                              onChange={(event) => {
                                const file = event.target.files?.[0];
                                if (!file) return;
                                onUploadCv(app, file);
                                event.target.value = '';
                              }}
                            />
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              disabled={uploadingCvId === app.id}
                              onClick={() => {
                                if (typeof document === 'undefined') return;
                                document.getElementById(cvUploadInputId)?.click();
                              }}
                            >
                              {uploadingCvId === app.id ? 'Uploading...' : 'Upload CV'}
                            </Button>
                          </>
                        ) : null}
                        {app.workable_profile_url ? (
                          <a
                            href={app.workable_profile_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-xs text-[var(--taali-primary)] hover:underline"
                          >
                            <ExternalLink size={12} />
                            View in Workable
                          </a>
                        ) : null}
                      </div>

                      {/* Enrichment hint */}
                      {app.workable_enriched === false && app.source === 'workable' && onEnrichCandidate ? (
                        <div className="mt-3 border border-amber-200 bg-amber-50 rounded px-3 py-2 text-xs text-amber-800 flex items-center gap-2">
                          <span>Basic profile only.</span>
                          <button
                            type="button"
                            className="font-semibold underline hover:no-underline"
                            onClick={() => handleEnrich(app)}
                            disabled={enrichingId === app.id}
                          >
                            {enrichingId === app.id ? 'Loading...' : 'Load full details from Workable'}
                          </button>
                        </div>
                      ) : null}

                      <div className="mt-3 grid gap-3 md:grid-cols-3 text-sm">
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">Status</p>
                          <p className="mt-1 text-gray-800">{app.status || 'applied'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">Added</p>
                          <p className="mt-1 text-gray-800">{formatDateTime(app.created_at)}</p>
                        </div>
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">Updated</p>
                          <p className="mt-1 text-gray-800">{formatDateTime(app.updated_at || app.created_at)}</p>
                        </div>
                      </div>
                          </>
                        );
                      })()}
                    </td>
                  </tr>
                ) : null}
              </React.Fragment>
            );
          })}
          </tbody>
        </table>
      </TableShell>
      {totalFiltered > PAGE_SIZE ? (
        <div className="flex items-center justify-between border-t border-[var(--taali-border)] pt-4 font-mono text-xs text-[var(--taali-muted)]">
          <span>
            Showing {startIndex + 1}-{Math.min(startIndex + PAGE_SIZE, totalFiltered)} of {totalFiltered}
          </span>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              disabled={safePage === 0}
              onClick={() => setPage((prev) => Math.max(0, prev - 1))}
            >
              Previous
            </Button>
            <span>Page {safePage + 1} of {totalPages}</span>
            <Button
              size="sm"
              variant="ghost"
              disabled={safePage >= totalPages - 1}
              onClick={() => setPage((prev) => Math.min(totalPages - 1, prev + 1))}
            >
              Next
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
};
