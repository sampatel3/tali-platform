import { formatScale100Score } from '../../lib/scoreDisplay';

const toNumber = (value) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};

export const uniq = (items, limit = Infinity) => {
  const seen = new Set();
  const output = [];

  (Array.isArray(items) ? items : []).forEach((item) => {
    const text = String(item || '').trim();
    if (!text) return;
    const key = text.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    output.push(text);
  });

  return output.slice(0, limit);
};

export const initialsFor = (...values) => {
  const token = values
    .filter(Boolean)
    .join(' ')
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join('');

  return token.toUpperCase() || 'TA';
};

export const formatShortDate = (value) => (
  value ? new Date(value).toLocaleDateString() : '—'
);

export const formatDateTime = (value) => (
  value ? new Date(value).toLocaleString() : '—'
);

export const formatClockDuration = (value) => {
  const totalSeconds = toNumber(value);
  if (totalSeconds == null) return '—';
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.max(0, Math.round(totalSeconds % 60));
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
};

export const formatMinutesWords = (value) => {
  const totalSeconds = toNumber(value);
  if (totalSeconds == null) return '—';
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.max(0, Math.round(totalSeconds % 60));
  return `${minutes} min ${seconds} s`;
};

export const formatScale100 = (value) => formatScale100Score(value, '0-100');

export const formatStatusLabel = (value) => {
  const normalized = String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[_-]+/g, ' ');

  if (!normalized) return '—';
  return normalized.replace(/\b\w/g, (chunk) => chunk.toUpperCase());
};

export const recommendationFromScore = (score100) => {
  const numeric = toNumber(score100);
  if (numeric == null) return { label: 'Pending', ctaLabel: 'Pending review', variant: 'muted' };
  if (numeric >= 80) return { label: 'Strong Hire', ctaLabel: 'Advance', variant: 'success' };
  if (numeric >= 65) return { label: 'Hire', ctaLabel: 'Advance', variant: 'info' };
  if (numeric >= 50) return { label: 'Consider', ctaLabel: 'Discuss', variant: 'warning' };
  return { label: 'No Hire', ctaLabel: 'Hold', variant: 'danger' };
};

export const deriveTimeToFirstPromptSeconds = (assessment) => {
  const direct = toNumber(
    assessment?.time_to_first_prompt_seconds
    ?? assessment?.time_to_first_prompt
  );
  if (direct != null) return direct;

  const startedAt = assessment?.started_at ? new Date(assessment.started_at).getTime() : null;
  if (!Number.isFinite(startedAt)) return null;

  const timeline = Array.isArray(assessment?.timeline) ? assessment.timeline : [];
  const firstPromptEvent = timeline.find((item) => {
    const type = String(item?.event_type || item?.type || item?.event || '').toLowerCase();
    return type.includes('first_prompt') || type.includes('ai_prompt') || type.includes('prompt');
  });

  if (!firstPromptEvent?.timestamp) return null;
  const promptAt = new Date(firstPromptEvent.timestamp).getTime();
  if (!Number.isFinite(promptAt)) return null;
  return Math.max(0, Math.round((promptAt - startedAt) / 1000));
};

const timeToPromptBarPercent = (seconds) => {
  const numeric = toNumber(seconds);
  if (numeric == null) return null;
  if (numeric <= 180) return 96;
  if (numeric <= 300) return 90;
  if (numeric <= 480) return 82;
  if (numeric <= 720) return 70;
  if (numeric <= 900) return 58;
  return 42;
};

export const toneForTenPointScore = (value) => {
  const numeric = toNumber(value);
  if (numeric == null) return 'muted';
  if (numeric >= 8) return 'success';
  if (numeric >= 6) return 'warning';
  return 'danger';
};

export const aiCollabBand = (assessment) => {
  const scores = [
    toNumber(assessment?.prompt_quality_score),
    toNumber(assessment?.error_recovery_score),
    toNumber(assessment?.independence_score),
    toNumber(assessment?.context_utilization_score),
    toNumber(assessment?.design_thinking_score),
  ].filter((value) => value != null);

  if (!scores.length) {
    return { label: 'Pending', score: null };
  }

  const average = scores.reduce((sum, value) => sum + value, 0) / scores.length;
  const rounded = Math.round(average * 10) / 10;

  if (rounded >= 9) return { label: 'A+', score: rounded };
  if (rounded >= 8) return { label: 'A', score: rounded };
  if (rounded >= 7) return { label: 'B', score: rounded };
  if (rounded >= 6) return { label: 'C', score: rounded };
  return { label: 'Watch', score: rounded };
};

export const buildSixAxisMetrics = (assessment) => {
  const timeToPrompt = deriveTimeToFirstPromptSeconds(assessment);

  return [
    {
      key: 'prompt_quality',
      label: 'Prompt quality',
      value: toNumber(assessment?.prompt_quality_score),
      displayValue: toNumber(assessment?.prompt_quality_score) != null ? `${Number(assessment.prompt_quality_score).toFixed(1)} / 10` : '—',
      percent: toNumber(assessment?.prompt_quality_score) != null ? Number(assessment.prompt_quality_score) * 10 : null,
      tone: toneForTenPointScore(assessment?.prompt_quality_score),
      note: 'Average prompt clarity and specificity from the captured Claude conversation.',
    },
    {
      key: 'error_recovery',
      label: 'Error recovery',
      value: toNumber(assessment?.error_recovery_score),
      displayValue: toNumber(assessment?.error_recovery_score) != null ? `${Number(assessment.error_recovery_score).toFixed(1)} / 10` : '—',
      percent: toNumber(assessment?.error_recovery_score) != null ? Number(assessment.error_recovery_score) * 10 : null,
      tone: toneForTenPointScore(assessment?.error_recovery_score),
      note: 'Measures whether the candidate caught bad suggestions, recoveries, or broken assumptions quickly.',
    },
    {
      key: 'independence',
      label: 'Independence',
      value: toNumber(assessment?.independence_score),
      displayValue: toNumber(assessment?.independence_score) != null ? `${Number(assessment.independence_score).toFixed(1)} / 10` : '—',
      percent: toNumber(assessment?.independence_score) != null ? Number(assessment.independence_score) * 10 : null,
      tone: toneForTenPointScore(assessment?.independence_score),
      note: 'Tracks how much of the solution path stayed candidate-directed rather than assistant-led.',
    },
    {
      key: 'context_utilization',
      label: 'Context utilization',
      value: toNumber(assessment?.context_utilization_score),
      displayValue: toNumber(assessment?.context_utilization_score) != null ? `${Number(assessment.context_utilization_score).toFixed(1)} / 10` : '—',
      percent: toNumber(assessment?.context_utilization_score) != null ? Number(assessment.context_utilization_score) * 10 : null,
      tone: toneForTenPointScore(assessment?.context_utilization_score),
      note: 'Reflects whether code, files, tests, and task constraints were provided before asking for help.',
    },
    {
      key: 'design_thinking',
      label: 'Design thinking',
      value: toNumber(assessment?.design_thinking_score),
      displayValue: toNumber(assessment?.design_thinking_score) != null ? `${Number(assessment.design_thinking_score).toFixed(1)} / 10` : '—',
      percent: toNumber(assessment?.design_thinking_score) != null ? Number(assessment.design_thinking_score) * 10 : null,
      tone: toneForTenPointScore(assessment?.design_thinking_score),
      note: 'Captures rollout judgment, patch ordering, and tradeoff awareness under time pressure.',
    },
    {
      key: 'time_to_first_prompt',
      label: 'Time to first prompt',
      value: timeToPrompt,
      displayValue: formatMinutesWords(timeToPrompt),
      percent: timeToPromptBarPercent(timeToPrompt),
      tone: timeToPrompt != null && timeToPrompt <= 480 ? 'success' : timeToPrompt != null && timeToPrompt <= 900 ? 'warning' : 'danger',
      note: 'Elapsed time between assessment start and the first Claude interaction.',
    },
  ];
};

const previewText = (value, maxLength = 180) => {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (!text) return '';
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength - 1)}…`;
};

export const buildAssessmentEvidenceCards = (assessment) => {
  const prompts = Array.isArray(assessment?.prompts_list) ? assessment.prompts_list : [];
  const promptAnalytics = assessment?.prompt_analytics && typeof assessment.prompt_analytics === 'object'
    ? assessment.prompt_analytics
    : {};
  const firstPrompt = prompts[0];
  const testsPassed = toNumber(assessment?.tests_passed ?? assessment?.tests_pass_count);
  const testsTotal = toNumber(assessment?.tests_total ?? assessment?.tests_run_count);
  const totalPrompts = toNumber(assessment?.total_prompts ?? prompts.length);
  const browserFocusRatio = toNumber(assessment?.browser_focus_ratio);
  const heuristicSummary = previewText(
    assessment?.score_breakdown?.heuristic_summary
    || promptAnalytics?.heuristic_summary
  );

  const items = uniq([
    firstPrompt?.message || firstPrompt?.text || firstPrompt?.prompt,
    heuristicSummary,
  ]);

  const cards = [
    firstPrompt ? {
      id: 'prompt',
      badge: 'TURN 01 · PROMPT TRAIL',
      title: 'Started with a concrete ask',
      body: previewText(firstPrompt.message || firstPrompt.text || firstPrompt.prompt, 220),
    } : null,
    (testsPassed != null || testsTotal != null || totalPrompts != null) ? {
      id: 'runtime',
      badge: 'RUNTIME · EXECUTION',
      title: 'Validated in the live workspace',
      body: [
        testsPassed != null && testsTotal != null ? `Tests passed: ${testsPassed}/${testsTotal}.` : null,
        totalPrompts != null ? `${totalPrompts} prompts were captured during the session.` : null,
        browserFocusRatio != null ? `Browser focus stayed at ${Math.round(browserFocusRatio * 100)}%.` : null,
      ].filter(Boolean).join(' '),
    } : null,
    (heuristicSummary || items.length > 1 || Array.isArray(assessment?.prompt_fraud_flags)) ? {
      id: 'report',
      badge: 'REPORT · EVIDENCE',
      title: 'Left behind evidence attached to the score',
      body: heuristicSummary
        || `Fraud flags: ${(assessment?.prompt_fraud_flags || []).length || 0}. Review stays attached to the final assessment report.`,
    } : null,
  ].filter(Boolean);

  return cards.slice(0, 3);
};

export const buildAssessmentTimelineRows = (assessment) => {
  const timeline = Array.isArray(assessment?.timeline) ? assessment.timeline : [];

  const rows = timeline.slice(0, 6).map((item, index) => {
    const type = String(item?.event_type || item?.type || item?.event || '').toLowerCase();
    const label = type
      ? type.replace(/[_-]+/g, ' ').replace(/\b\w/g, (chunk) => chunk.toUpperCase())
      : `Event ${index + 1}`;
    const detail = previewText(
      item?.preview
      || item?.message
      || item?.prompt
      || item?.text
      || [
        item?.file_path ? `File: ${item.file_path}` : null,
        item?.tests_passed != null && item?.tests_total != null ? `Tests ${item.tests_passed}/${item.tests_total}` : null,
      ].filter(Boolean).join(' · '),
      120
    );

    let tone = 'muted';
    if (type.includes('submit') || type.includes('complete')) tone = 'success';
    if (type.includes('error') || type.includes('flag')) tone = 'danger';
    if (type.includes('test') || type.includes('run') || type.includes('prompt')) tone = 'info';

    return {
      id: `${label}-${index}`,
      label,
      detail: detail || 'Assessment activity recorded.',
      timestamp: item?.timestamp || item?.ts || item?.time || null,
      tone,
    };
  });

  if (rows.length) return rows;

  return [
    assessment?.started_at ? {
      id: 'started',
      label: 'Assessment started',
      detail: 'Candidate opened the live workspace.',
      timestamp: assessment.started_at,
      tone: 'info',
    } : null,
    assessment?.completed_at ? {
      id: 'completed',
      label: 'Assessment completed',
      detail: 'The final submission is attached to the report.',
      timestamp: assessment.completed_at,
      tone: 'success',
    } : null,
  ].filter(Boolean);
};

export const copyText = async (text) => {
  await navigator.clipboard.writeText(text);
};
