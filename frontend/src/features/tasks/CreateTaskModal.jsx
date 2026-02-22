import React, { useEffect, useMemo, useState } from 'react';
import { X } from 'lucide-react';

import { buildTaskFormState, TASK_TEMPLATES } from './taskTemplates';
import { ScoringGlossaryPanel, SCORING_GLOSSARY_METRIC_COUNT } from '../../shared/ui/ScoringGlossaryPanel';
import { Button, Input, Panel, Select, Textarea } from '../../shared/ui/TaaliPrimitives';

const DEFAULT_SCORE_WEIGHTS = {
  task_completion: 0.2,
  prompt_clarity: 0.15,
  context_provision: 0.15,
  independence_efficiency: 0.2,
  response_utilization: 0.1,
  debugging_design: 0.05,
  written_communication: 0.1,
  role_fit: 0.05,
};

const SCORE_WEIGHT_FIELDS = [
  { key: 'task_completion', label: 'Task completion' },
  { key: 'prompt_clarity', label: 'Prompt clarity' },
  { key: 'context_provision', label: 'Context provision' },
  { key: 'independence_efficiency', label: 'Independence & efficiency' },
  { key: 'response_utilization', label: 'Response utilization' },
  { key: 'debugging_design', label: 'Debugging & design' },
  { key: 'written_communication', label: 'Written communication' },
  { key: 'role_fit', label: 'Role fit' },
];

const TASK_TYPE_OPTIONS = [
  'debugging',
  'ai_engineering',
  'optimization',
  'build',
  'refactor',
];

const DIFFICULTY_OPTIONS = ['junior', 'mid', 'senior', 'staff'];

const normalizeWeightsForForm = (scoreWeights) => {
  const source = scoreWeights && typeof scoreWeights === 'object' ? scoreWeights : DEFAULT_SCORE_WEIGHTS;
  const out = {};
  SCORE_WEIGHT_FIELDS.forEach(({ key }) => {
    const raw = Number(source[key] ?? DEFAULT_SCORE_WEIGHTS[key] ?? 0);
    out[key] = Math.round(raw * 100);
  });
  return out;
};

const normalizeWeightsForApi = (weightsForm) => {
  const raw = {};
  let total = 0;
  SCORE_WEIGHT_FIELDS.forEach(({ key }) => {
    const value = Math.max(0, Math.min(100, Number(weightsForm?.[key] ?? 0)));
    raw[key] = value;
    total += value;
  });
  if (total <= 0) {
    return { ...DEFAULT_SCORE_WEIGHTS };
  }
  const out = {};
  SCORE_WEIGHT_FIELDS.forEach(({ key }) => {
    out[key] = raw[key] / total;
  });
  return out;
};

export const CreateTaskModal = ({
  onClose,
  onSubmit,
  initialTask = null,
  mode = 'view',
  saving = false,
  error = '',
  taskAuthoringEnabled = true,
}) => {
  const isViewOnly = mode === 'view';

  const [form, setForm] = useState(() => {
    const base = buildTaskFormState(initialTask || undefined);
    return {
      ...base,
      score_weights: normalizeWeightsForForm(initialTask?.score_weights),
    };
  });
  const [validationError, setValidationError] = useState('');

  useEffect(() => {
    const base = buildTaskFormState(initialTask || undefined);
    setForm({
      ...base,
      score_weights: normalizeWeightsForForm(initialTask?.score_weights),
    });
    setValidationError('');
  }, [initialTask, mode]);

  const title = useMemo(() => {
    if (mode === 'create') return 'Create Task';
    if (mode === 'edit') return 'Edit Task';
    return 'Task Overview';
  }, [mode]);

  const weightTotal = SCORE_WEIGHT_FIELDS.reduce(
    (sum, field) => sum + Math.max(0, Math.min(100, Number(form.score_weights?.[field.key] ?? 0))),
    0,
  );
  const effectiveWeightPercent = useMemo(() => {
    if (weightTotal <= 0) {
      return SCORE_WEIGHT_FIELDS.reduce((acc, field) => {
        acc[field.key] = Math.round((DEFAULT_SCORE_WEIGHTS[field.key] || 0) * 100);
        return acc;
      }, {});
    }
    return SCORE_WEIGHT_FIELDS.reduce((acc, field) => {
      const current = Math.max(0, Math.min(100, Number(form.score_weights?.[field.key] ?? 0)));
      acc[field.key] = Math.round((current / weightTotal) * 100);
      return acc;
    }, {});
  }, [form.score_weights, weightTotal]);

  const applyTemplate = (templateId) => {
    const selected = TASK_TEMPLATES.find((item) => item.id === templateId);
    if (!selected) return;
    const next = buildTaskFormState(selected.task);
    setForm({
      ...next,
      score_weights: normalizeWeightsForForm(next.score_weights),
    });
  };

  const handleChange = (key, value) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const handleWeightChange = (key, value) => {
    setForm((prev) => ({
      ...prev,
      score_weights: {
        ...(prev.score_weights || {}),
        [key]: Number(value),
      },
    }));
  };

  const handleSubmit = async () => {
    if (isViewOnly || !onSubmit) return;
    setValidationError('');
    if (!taskAuthoringEnabled) {
      setValidationError('Task authoring is currently disabled for this environment.');
      return;
    }
    if (!String(form.name || '').trim()) {
      setValidationError('Task name is required.');
      return;
    }
    if (!String(form.description || '').trim()) {
      setValidationError('Description is required.');
      return;
    }
    if (!String(form.starter_code || '').trim()) {
      setValidationError('Starter code is required.');
      return;
    }
    if (!String(form.test_code || '').trim()) {
      setValidationError('Test suite is required.');
      return;
    }

    await onSubmit({
      name: String(form.name || '').trim(),
      description: String(form.description || '').trim(),
      task_type: String(form.task_type || 'debugging'),
      difficulty: String(form.difficulty || 'mid'),
      duration_minutes: Number(form.duration_minutes || 30),
      claude_budget_limit_usd: form.claude_budget_limit_usd === '' || form.claude_budget_limit_usd == null
        ? null
        : Number(form.claude_budget_limit_usd),
      starter_code: String(form.starter_code || ''),
      test_code: String(form.test_code || ''),
      role: String(form.role || '').trim() || null,
      scenario: String(form.scenario || form.description || '').trim() || null,
      score_weights: normalizeWeightsForApi(form.score_weights),
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="bg-[var(--taali-surface)] border-2 border-[var(--taali-border)] w-full max-w-5xl max-h-[92vh] overflow-y-auto"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="sticky top-0 z-10 flex items-center justify-between px-8 py-5 border-b-2 border-[var(--taali-border)] bg-[var(--taali-surface)]">
          <div>
            <h2 className="text-xl font-bold text-[var(--taali-text)]">{title}</h2>
            {!taskAuthoringEnabled && !isViewOnly ? (
              <p className="font-mono text-xs text-[var(--taali-warning)] mt-1">Task authoring is disabled by feature flag.</p>
            ) : null}
          </div>
          <Button variant="ghost" size="sm" className="!p-2" onClick={onClose} aria-label="Close">
            <X size={18} />
          </Button>
        </div>

        <div className="px-8 py-6 space-y-5">
          {!isViewOnly ? (
            <Panel as="div" className="p-4">
              <div className="font-mono text-xs text-[var(--taali-muted)] mb-2">Start from template</div>
              <div className="flex flex-wrap gap-2">
                {TASK_TEMPLATES.map((template) => (
                  <Button
                    key={template.id}
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => applyTemplate(template.id)}
                  >
                    {template.label}
                  </Button>
                ))}
              </div>
            </Panel>
          ) : null}

          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Task name</span>
              <Input
                value={form.name || ''}
                onChange={(event) => handleChange('name', event.target.value)}
                disabled={isViewOnly}
              />
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Role</span>
              <Input
                value={form.role || ''}
                placeholder="backend_engineer"
                onChange={(event) => handleChange('role', event.target.value)}
                disabled={isViewOnly}
              />
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Task type</span>
              <Select
                value={form.task_type || 'debugging'}
                onChange={(event) => handleChange('task_type', event.target.value)}
                disabled={isViewOnly}
              >
                {TASK_TYPE_OPTIONS.map((option) => (
                  <option key={option} value={option}>{option.replace(/_/g, ' ')}</option>
                ))}
              </Select>
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Difficulty</span>
              <Select
                value={form.difficulty || 'mid'}
                onChange={(event) => handleChange('difficulty', event.target.value)}
                disabled={isViewOnly}
              >
                {DIFFICULTY_OPTIONS.map((option) => (
                  <option key={option} value={option}>{option}</option>
                ))}
              </Select>
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Duration (minutes)</span>
              <Input
                type="number"
                min="15"
                max="180"
                value={form.duration_minutes ?? 30}
                onChange={(event) => handleChange('duration_minutes', event.target.value)}
                disabled={isViewOnly}
              />
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Claude budget (USD)</span>
              <Input
                type="number"
                min="0"
                step="0.1"
                value={form.claude_budget_limit_usd ?? ''}
                onChange={(event) => handleChange('claude_budget_limit_usd', event.target.value)}
                disabled={isViewOnly}
              />
            </label>
          </div>

          <label className="block">
            <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Description</span>
            <Textarea
              className="min-h-[90px]"
              value={form.description || ''}
              onChange={(event) => handleChange('description', event.target.value)}
              disabled={isViewOnly}
            />
          </label>

          <label className="block">
            <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Scenario</span>
            <Textarea
              className="min-h-[80px]"
              value={form.scenario || ''}
              onChange={(event) => handleChange('scenario', event.target.value)}
              disabled={isViewOnly}
            />
          </label>

          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Starter code</span>
              <Textarea
                className="min-h-[220px] font-mono text-xs"
                value={form.starter_code || ''}
                onChange={(event) => handleChange('starter_code', event.target.value)}
                disabled={isViewOnly}
              />
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Test suite</span>
              <Textarea
                className="min-h-[220px] font-mono text-xs"
                value={form.test_code || ''}
                onChange={(event) => handleChange('test_code', event.target.value)}
                disabled={isViewOnly}
              />
            </label>
          </div>

          <Panel as="div" className="p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="font-mono text-xs text-[var(--taali-muted)]">Score weights (%)</div>
              <div className="font-mono text-xs text-[var(--taali-muted)]">
                Total: <span className="font-bold text-[var(--taali-text)]">{weightTotal}%</span>
                {weightTotal !== 100 ? ' (will be normalized to 100%)' : ''}
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {SCORE_WEIGHT_FIELDS.map((field) => (
                <label key={field.key} className="block">
                  <div className="mb-1 flex items-center justify-between">
                    <span className="font-mono text-xs text-[var(--taali-text)]">{field.label}</span>
                    <span className="font-mono text-xs text-[var(--taali-muted)]">
                      {form.score_weights?.[field.key] ?? 0}% → {effectiveWeightPercent[field.key] ?? 0}% effective
                    </span>
                  </div>
                  <Input
                    type="range"
                    min="0"
                    max="100"
                    step="1"
                    value={form.score_weights?.[field.key] ?? 0}
                    onChange={(event) => handleWeightChange(field.key, event.target.value)}
                    disabled={isViewOnly}
                  />
                </label>
              ))}
            </div>
          </Panel>

          <Panel as="div" className="p-4">
            <details>
              <summary className="cursor-pointer font-mono text-xs text-[var(--taali-purple)] hover:underline">
                View TAALI scoring glossary ({SCORING_GLOSSARY_METRIC_COUNT} metrics) →
              </summary>
              <ScoringGlossaryPanel className="mt-3" />
            </details>
          </Panel>

          {validationError ? (
            <div className="text-sm text-[var(--taali-danger)]">{validationError}</div>
          ) : null}
          {error ? (
            <div className="text-sm text-[var(--taali-danger)]">{error}</div>
          ) : null}
        </div>

        <div className="sticky bottom-0 border-t-2 border-[var(--taali-border)] bg-[var(--taali-surface)] px-8 py-4 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          {!isViewOnly ? (
            <Button type="button" variant="primary" onClick={handleSubmit} disabled={saving}>
              {saving ? 'Saving...' : mode === 'create' ? 'Create task' : 'Save changes'}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
};
