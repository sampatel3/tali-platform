import React, { useEffect, useMemo, useState } from 'react';
import { MapPin, Upload, UserPlus } from 'lucide-react';

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
import { CandidateScoreRing } from './CandidateScoreRing';

const COLUMN_STORAGE_KEY = 'taali_candidates_table_columns_v2';
const PAGE_SIZE = 20;

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

function CandidateAvatar({ name, imageUrl, size = 32 }) {
  const initials = (name || '?')
    .split(/\s+/)
    .map((word) => word[0])
    .join('')
    .toUpperCase()
    .slice(0, 2);

  if (imageUrl) {
    return (
      <img
        src={imageUrl}
        alt=""
        className="shrink-0 rounded-full object-cover"
        style={{ width: size, height: size }}
        onError={(event) => { event.currentTarget.style.display = 'none'; }}
      />
    );
  }

  return (
    <div
      className="flex shrink-0 items-center justify-center rounded-full bg-[linear-gradient(135deg,var(--taali-purple),#6b4dff)] text-xs font-bold text-white shadow-[var(--taali-shadow-soft)]"
      style={{ width: size, height: size }}
    >
      {initials}
    </div>
  );
}

const renderModeLabel = (application) => {
  const mode = String(application.score_mode || application.score_summary?.mode || '').toLowerCase();
  if (mode === 'assessment_plus_role_fit' || mode === 'assessment_plus_cv') return 'Assessment + Role fit';
  if (mode === 'assessment_only_fallback') return 'Assessment only';
  if (mode === 'pending') return 'Pending';
  return 'Role fit only';
};

const getTaaliScorePayload = (application) => {
  if (typeof application.taali_score === 'number') {
    return { score: application.taali_score, details: { score_scale: '0-100' } };
  }
  if (typeof application.score_summary?.taali_score === 'number') {
    return { score: application.score_summary.taali_score, details: { score_scale: '0-100' } };
  }
  if (typeof application.cv_match_score === 'number') {
    return { score: application.cv_match_score, details: application.cv_match_details };
  }
  return { score: null, details: null };
};

const renderTaaliScore = (application) => {
  const payload = getTaaliScorePayload(application);
  if (typeof payload.score === 'number') {
    return formatCvScore100(payload.score, payload.details);
  }
  if (!application.cv_filename) return '—';
  return 'Pending';
};

const columnLabel = (column) => ({
  candidate: 'Candidate',
  cv: 'CV',
  send: 'Assessment',
  taali_ai: 'TAALI Score',
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

export const CandidatesTable = ({
  applications,
  loading,
  error,
  searchQuery = '',
  statusFilter = 'all',
  sortBy = 'taali_score',
  sortOrder = 'desc',
  roleTasks,
  canCreateAssessment,
  creatingAssessmentId,
  viewingApplicationId,
  onChangeSort,
  onAddCandidate,
  onViewCandidate,
  onOpenDetails,
  onOpenCvSidebar,
  onCreateAssessment,
  onUploadCv,
  uploadingCvId,
}) => {
  const [composerApplicationId, setComposerApplicationId] = useState(null);
  const [taskByApplication, setTaskByApplication] = useState({});
  const [columnsOpen, setColumnsOpen] = useState(false);
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

  useEffect(() => {
    setComposerApplicationId(null);
  }, [applications, roleTasks]);

  useEffect(() => {
    setPage(0);
  }, [applications, searchQuery, statusFilter, sortBy, sortOrder]);

  useEffect(() => {
    try {
      if (typeof window === 'undefined') return;
      window.localStorage.setItem(COLUMN_STORAGE_KEY, JSON.stringify(columnPrefs));
    } catch {
      // Ignore localStorage issues.
    }
  }, [columnPrefs]);

  const filtered = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return applications.filter((application) => {
      if (statusFilter !== 'all' && (application.status || 'applied').toLowerCase() !== statusFilter) return false;
      if (!query) return true;
      return [
        application.candidate_name,
        application.candidate_email,
        application.candidate_position,
        application.candidate_headline,
        application.candidate_location,
        application.status,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(query);
    });
  }, [applications, searchQuery, statusFilter]);

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
    ].filter((key) => {
      if (['candidate', 'send', 'taali_ai', 'status'].includes(key)) return true;
      return Boolean(columnPrefs[key]);
    })
  ), [columnPrefs]);

  const totalFiltered = filtered.length;
  const totalPages = Math.max(1, Math.ceil(totalFiltered / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const startIndex = safePage * PAGE_SIZE;
  const pagedFiltered = filtered.slice(startIndex, startIndex + PAGE_SIZE);
  const columnCount = visibleColumnOrder.length;

  const togglePref = (key) => {
    setColumnPrefs((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const handleSortToggle = (column) => {
    if (!onChangeSort) return;
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

  if (loading) {
    return (
      <div className="space-y-1.5">
        <div className="px-1">
          <div className="h-3 w-28 animate-pulse rounded bg-[var(--taali-border)]" />
        </div>
        <TableShell className="max-h-[70vh]">
          <table className="w-full table-fixed min-w-[900px]">
            <thead>
              <tr className="text-left text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                {visibleColumnOrder.map((column) => (
                  <th key={column} className="sticky top-0 z-20 bg-[var(--taali-table-header)] px-3 py-2">
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
      <Panel className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] px-4 py-10 text-center text-sm text-[var(--taali-danger)]">
        {error}
      </Panel>
    );
  }

  if (filtered.length === 0) {
    return (
      <EmptyState
        title={searchQuery.trim() || statusFilter !== 'all' ? 'No matching candidates' : 'No candidates yet'}
        description={searchQuery.trim() || statusFilter !== 'all'
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
    <div className="space-y-1.5">
      <div className="relative flex flex-wrap items-center justify-between gap-2 px-1">
        <p className="text-xs text-[var(--taali-muted)]">
          {filtered.length}
          {' '}
          candidate{filtered.length === 1 ? '' : 's'}
        </p>
        <Button type="button" variant="ghost" size="xs" onClick={() => setColumnsOpen((prev) => !prev)}>
          Columns
        </Button>
        {columnsOpen ? (
          <div className="absolute right-1 top-full z-40 mt-2 w-[280px] rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-3 shadow-[var(--taali-shadow-soft)]">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Show columns</p>
            <div className="mt-2 grid gap-2">
              {allColumnCheckboxes.map(({ key, label }) => (
                <label key={key} className="flex items-center gap-2 text-sm text-[var(--taali-text)]">
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
              <Button type="button" variant="secondary" size="xs" onClick={() => setColumnsOpen(false)}>
                Close
              </Button>
            </div>
          </div>
        ) : null}
      </div>

      <TableShell className="max-h-[70vh]">
        <table className="w-full table-fixed min-w-[900px]">
          <thead>
            <tr className="text-left text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              {visibleColumnOrder.map((column) => {
                const isStickyLeft = column === 'candidate';
                const thBase = isStickyLeft
                  ? 'sticky left-0 top-0 z-30 bg-[var(--taali-table-header)]'
                  : 'sticky top-0 z-20 bg-[var(--taali-table-header)]';
                const widthClass = {
                  candidate: 'w-[250px]',
                  cv: 'w-[140px]',
                  taali_ai: 'w-[126px]',
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
                  return <th key={column} className={`${thBase} ${widthClass} px-3 py-2`}>Candidate</th>;
                }

                if (column === 'taali_ai') {
                  return (
                    <th
                      key={column}
                      className={`${thBase} ${widthClass} px-3 py-2`}
                      aria-sort={sortBy === 'taali_score' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'}
                    >
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 uppercase tracking-[0.08em] text-[var(--taali-muted)] transition-colors hover:text-[var(--taali-text)]"
                        onClick={() => handleSortToggle('taali_score')}
                      >
                        TAALI Score
                        <span className="text-[0.65rem] text-[var(--taali-muted)]">{renderSortIndicator('taali_score')}</span>
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
                        className="inline-flex items-center gap-1 uppercase tracking-[0.08em] text-[var(--taali-muted)] transition-colors hover:text-[var(--taali-text)]"
                        onClick={() => handleSortToggle('created_at')}
                      >
                        Added
                        <span className="text-[0.65rem] text-[var(--taali-muted)]">{renderSortIndicator('created_at')}</span>
                      </button>
                    </th>
                  );
                }

                return (
                  <th key={column} className={`${thBase} ${widthClass} px-3 py-2`}>
                    {columnLabel(column)}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {pagedFiltered.map((application) => {
              const selectedTask = taskByApplication[application.id] || (roleTasks.length === 1 ? String(roleTasks[0].id) : '');
              const hasValidAssessment = Boolean(application.valid_assessment_id);
              const uploadInputId = `cv-upload-${application.id}`;

              return (
                <React.Fragment key={application.id}>
                  <tr className="group align-top border-b border-[var(--taali-border)] transition-colors hover:bg-[var(--taali-surface-hover,rgba(0,0,0,0.04))]">
                    {visibleColumnOrder.map((column) => {
                      if (column === 'candidate') {
                        return (
                          <td
                            key={column}
                            className="sticky left-0 z-10 bg-[var(--taali-surface)] px-3 py-2 text-sm group-hover:bg-[var(--taali-surface-hover,rgba(0,0,0,0.04))]"
                          >
                            <div className="flex items-start gap-2.5">
                              <CandidateAvatar
                                name={application.candidate_name}
                                imageUrl={application.candidate_image_url}
                                size={28}
                              />
                              <div className="min-w-0 flex-1">
                                <button
                                  type="button"
                                  className="block w-full truncate text-left font-semibold text-[var(--taali-text)] hover:underline"
                                  onClick={() => onViewCandidate(application)}
                                  disabled={viewingApplicationId === application.id}
                                >
                                  {application.candidate_name || application.candidate_email}
                                </button>
                                {application.candidate_headline ? (
                                  <p className="truncate text-xs text-[var(--taali-muted)]">{application.candidate_headline}</p>
                                ) : null}
                                {application.candidate_location ? (
                                  <p className="flex items-center gap-0.5 truncate text-[11px] text-[var(--taali-muted)]">
                                    <MapPin size={10} />
                                    {application.candidate_location}
                                  </p>
                                ) : null}
                                <div className="mt-1 flex flex-wrap items-center gap-2">
                                  <button
                                    type="button"
                                    className="text-xs text-[var(--taali-muted)] underline decoration-[var(--taali-border-muted)] underline-offset-2 hover:text-[var(--taali-text)]"
                                    onClick={() => onOpenDetails?.(application)}
                                  >
                                    View assessment
                                  </button>
                                  {onOpenCvSidebar ? (
                                    <button
                                    type="button"
                                    className="text-xs font-medium text-[var(--taali-purple-hover)] hover:text-[var(--taali-purple)] hover:underline"
                                    onClick={() => onOpenCvSidebar(application)}
                                  >
                                      View CV
                                    </button>
                                  ) : null}
                                  {application.source === 'workable' ? (
                                    <span className="text-xs text-[var(--taali-muted)]">Workable</span>
                                  ) : null}
                                </div>
                              </div>
                            </div>
                          </td>
                        );
                      }

                      if (column === 'cv') {
                        return (
                          <td key={column} className="px-3 py-2">
                            <div className="flex flex-col items-start gap-1">
                              <span className="text-sm text-[var(--taali-text)]">
                                {application.cv_filename ? 'Uploaded' : 'Missing'}
                              </span>
                              {!application.cv_filename && onUploadCv ? (
                                <>
                                  <input
                                    id={uploadInputId}
                                    type="file"
                                    accept=".pdf,.docx,.doc"
                                    className="sr-only"
                                    onChange={(event) => {
                                      const file = event.target.files?.[0];
                                      if (!file) return;
                                      onUploadCv(application, file);
                                      event.target.value = '';
                                    }}
                                  />
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="xs"
                                    className="!px-0 text-[11px]"
                                    disabled={uploadingCvId === application.id}
                                    onClick={() => document.getElementById(uploadInputId)?.click()}
                                  >
                                    <Upload size={12} />
                                    {uploadingCvId === application.id ? 'Uploading...' : 'Upload CV'}
                                  </Button>
                                </>
                              ) : null}
                            </div>
                          </td>
                        );
                      }

                      if (column === 'send') {
                        return (
                          <td key={column} className="px-3 py-2">
                            <div className="flex flex-col items-start gap-1">
                              <Button
                                type="button"
                                variant="primary"
                                size="xs"
                                className="whitespace-nowrap"
                                disabled={!canCreateAssessment || roleTasks.length === 0}
                                onClick={() => {
                                  if (composerApplicationId === application.id) {
                                    setComposerApplicationId(null);
                                    return;
                                  }
                                  if (roleTasks.length === 1) {
                                    setTaskByApplication((prev) => ({ ...prev, [application.id]: String(roleTasks[0].id) }));
                                  }
                                  setComposerApplicationId(application.id);
                                }}
                              >
                                {hasValidAssessment ? 'Retake assessment' : 'Send assessment'}
                              </Button>
                              {!application.cv_filename ? (
                                <span className="text-[0.7rem] font-semibold text-[var(--taali-warning)]">No CV (role fit N/A)</span>
                              ) : null}
                              {roleTasks.length === 0 ? (
                                <span className="text-[0.7rem] font-semibold text-[var(--taali-warning)]">Link a task first</span>
                              ) : null}
                            </div>
                          </td>
                        );
                      }

                      if (column === 'taali_ai') {
                        const taaliScore = getTaaliScorePayload(application);
                        return (
                          <td key={column} className="px-3 py-2">
                            <div className="flex flex-col items-center gap-2 text-center">
                              <CandidateScoreRing
                                score={taaliScore.score}
                                details={taaliScore.details}
                                size={48}
                                strokeWidth={5}
                                label={`TAALI Score for ${application.candidate_name || application.candidate_email || 'candidate'}`}
                              />
                              <div className="max-w-[80px] text-[11px] leading-4 text-[var(--taali-muted)]" title={renderTaaliScore(application)}>
                                {renderModeLabel(application)}
                              </div>
                            </div>
                          </td>
                        );
                      }

                      if (column === 'workable_stage') {
                        return <td key={column} className="break-words px-3 py-2 text-sm text-[var(--taali-text)]">{application.workable_stage || '—'}</td>;
                      }

                      if (column === 'workable_candidate_id') {
                        return <td key={column} className="break-all px-3 py-2 text-sm text-[var(--taali-text)]">{application.workable_candidate_id || '—'}</td>;
                      }

                      if (column === 'status') {
                        return (
                          <td key={column} className="px-3 py-2">
                            <Badge variant={statusVariant(application.status)}>{application.status || 'applied'}</Badge>
                          </td>
                        );
                      }

                      if (column === 'headline') {
                        return <td key={column} className="truncate px-3 py-2 text-sm text-[var(--taali-text)]">{application.candidate_headline || '—'}</td>;
                      }

                      if (column === 'location') {
                        return <td key={column} className="truncate px-3 py-2 text-sm text-[var(--taali-text)]">{application.candidate_location || '—'}</td>;
                      }

                      if (column === 'skills') {
                        const skills = Array.isArray(application.candidate_skills) ? application.candidate_skills : [];
                        return (
                          <td key={column} className="px-3 py-2">
                            {skills.length > 0 ? (
                              <div className="flex flex-wrap gap-1">
                                {skills.slice(0, 3).map((skill) => (
                                  <Badge key={skill} variant="muted">{skill}</Badge>
                                ))}
                                {skills.length > 3 ? <span className="text-xs text-[var(--taali-muted)]">+{skills.length - 3}</span> : null}
                              </div>
                            ) : '—'}
                          </td>
                        );
                      }

                      if (column === 'added') {
                        return <td key={column} className="whitespace-nowrap px-3 py-2 text-sm text-[var(--taali-text)]">{formatDateTime(application.created_at)}</td>;
                      }

                      if (column === 'email') {
                        return <td key={column} className="break-all px-3 py-2 text-sm text-[var(--taali-text)]">{application.candidate_email || '—'}</td>;
                      }

                      if (column === 'position') {
                        return <td key={column} className="break-words px-3 py-2 text-sm text-[var(--taali-text)]">{application.candidate_position || '—'}</td>;
                      }

                      if (column === 'source') {
                        return <td key={column} className="whitespace-nowrap px-3 py-2 text-sm text-[var(--taali-text)]">{application.source || 'manual'}</td>;
                      }

                      return null;
                    })}
                  </tr>

                  {composerApplicationId === application.id ? (
                    <tr className="bg-[var(--taali-surface-subtle)]">
                      <td colSpan={columnCount} className="px-3 py-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <Select
                            value={selectedTask}
                            onChange={(event) => {
                              setTaskByApplication((prev) => ({ ...prev, [application.id]: event.target.value }));
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
                            size="xs"
                            disabled={!selectedTask || creatingAssessmentId === application.id}
                            onClick={async () => {
                              const success = await onCreateAssessment(application, selectedTask, { retake: hasValidAssessment });
                              if (success) setComposerApplicationId(null);
                            }}
                          >
                            {creatingAssessmentId === application.id
                              ? 'Creating...'
                              : (hasValidAssessment ? 'Retake assessment' : 'Send assessment')}
                          </Button>
                          <Button type="button" variant="ghost" size="xs" onClick={() => setComposerApplicationId(null)}>
                            Cancel
                          </Button>
                        </div>
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
        <div className="flex items-center justify-between border-t border-[var(--taali-border)] pt-3 font-mono text-xs text-[var(--taali-muted)]">
          <span>
            Showing {startIndex + 1}-{Math.min(startIndex + PAGE_SIZE, totalFiltered)} of {totalFiltered}
          </span>
          <div className="flex items-center gap-2">
            <Button
              size="xs"
              variant="ghost"
              disabled={safePage === 0}
              onClick={() => setPage((prev) => Math.max(0, prev - 1))}
            >
              Previous
            </Button>
            <span>Page {safePage + 1} of {totalPages}</span>
            <Button
              size="xs"
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
