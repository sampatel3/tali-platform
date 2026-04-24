const WORKABLE_RELATIVE = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });

const ABSOLUTE_SECONDS = {
  year: 60 * 60 * 24 * 365,
  month: 60 * 60 * 24 * 30,
  week: 60 * 60 * 24 * 7,
  day: 60 * 60 * 24,
  hour: 60 * 60,
  minute: 60,
};

const parseDate = (value) => {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};

const durationUnits = [
  ['year', ABSOLUTE_SECONDS.year],
  ['month', ABSOLUTE_SECONDS.month],
  ['week', ABSOLUTE_SECONDS.week],
  ['day', ABSOLUTE_SECONDS.day],
  ['hour', ABSOLUTE_SECONDS.hour],
  ['minute', ABSOLUTE_SECONDS.minute],
];

export const formatRelativeTime = (value, reference = new Date()) => {
  const date = parseDate(value);
  const anchor = parseDate(reference);
  if (!date || !anchor) return '—';

  const deltaSeconds = Math.round((date.getTime() - anchor.getTime()) / 1000);
  if (Math.abs(deltaSeconds) < 45) return 'just now';

  for (const [unit, secondsPerUnit] of durationUnits) {
    if (Math.abs(deltaSeconds) >= secondsPerUnit || unit === 'minute') {
      return WORKABLE_RELATIVE.format(Math.round(deltaSeconds / secondsPerUnit), unit);
    }
  }

  return '—';
};

export const formatUtcClock = (value) => {
  const date = parseDate(value);
  if (!date) return '—';
  return new Intl.DateTimeFormat('en', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'UTC',
  }).format(date).replace(',', '') + ' UTC';
};

export const formatDurationLabel = (secondsValue) => {
  const seconds = Number(secondsValue);
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 1) return '<1s';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(seconds >= 600 ? 0 : 1)}m`;
  return `${(seconds / 3600).toFixed(seconds >= 36000 ? 0 : 1)}h`;
};

export const deriveNextPullAt = (lastSyncAt, intervalMinutes) => {
  const date = parseDate(lastSyncAt);
  const minutes = Number(intervalMinutes);
  if (!date || !Number.isFinite(minutes) || minutes <= 0) return null;
  return new Date(date.getTime() + (minutes * 60 * 1000));
};

export const deriveSyncHealth = ({
  lastSyncStatus,
  syncInProgress = false,
  lastSyncAt = null,
  errors = [],
}) => {
  if (syncInProgress) return 'healthy';
  const normalized = String(lastSyncStatus || '').trim().toLowerCase();
  if (normalized === 'error' || normalized === 'failed' || (Array.isArray(errors) && errors.length > 0)) {
    return 'error';
  }
  const lastSyncDate = parseDate(lastSyncAt);
  if (!lastSyncDate) return 'stale';
  const ageMinutes = (Date.now() - lastSyncDate.getTime()) / (60 * 1000);
  if (ageMinutes > 180) return 'stale';
  return 'healthy';
};

export const deriveWorkableSummary = ({
  org = null,
  syncStatus = null,
  roles = [],
  applications = [],
}) => {
  const statusSummary = syncStatus?.workable_last_sync_summary || org?.workable_last_sync_summary || {};
  const dbSnapshot = syncStatus?.workable_sync_progress?.db_snapshot || syncStatus?.db_snapshot || {};
  const errors = Array.isArray(syncStatus?.errors)
    ? syncStatus.errors
    : Array.isArray(statusSummary?.errors)
      ? statusSummary.errors
      : [];

  return {
    openJobs:
      Number(statusSummary?.open_jobs)
      || Number(statusSummary?.jobs_seen)
      || Number(syncStatus?.db_roles_count)
      || Number(dbSnapshot?.roles_active)
      || roles.filter((role) => role?.is_active !== false).length,
    activeCandidates:
      Number(statusSummary?.active_candidates)
      || Number(statusSummary?.candidates_seen)
      || Number(syncStatus?.db_applications_count)
      || Number(dbSnapshot?.applications_active)
      || applications.length,
    newCandidates:
      Number(statusSummary?.new_candidates)
      || Number(statusSummary?.candidates_upserted)
      || Number(syncStatus?.candidates_upserted)
      || 0,
    errors: errors.length || Number(statusSummary?.errors_count) || 0,
    duration:
      formatDurationLabel(statusSummary?.duration_seconds)
      || (() => {
        const started = parseDate(syncStatus?.started_at);
        const finished = parseDate(syncStatus?.finished_at);
        if (!started || !finished) return null;
        return formatDurationLabel((finished.getTime() - started.getTime()) / 1000);
      })(),
  };
};

export const canManageWorkable = (user) => ['admin', 'owner'].includes(String(user?.role || '').toLowerCase());

export const normalizeWorkableSubdomain = (value) => {
  const raw = String(value || '').trim();
  if (!raw) return '';
  return raw.endsWith('.workable.com') ? raw : `${raw}.workable.com`;
};
