import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { roles as rolesApi } from '../../shared/api';
import { Button, Select, Spinner } from '../../shared/ui/TaaliPrimitives';

const QUESTION_KINDS = [
  { value: 'boolean', label: 'Yes / no' },
  { value: 'single_select', label: 'Single choice' },
  { value: 'multi_select', label: 'Multiple choice' },
  { value: 'text', label: 'Free text' },
  { value: 'number', label: 'Number' },
];

const EMPTY_DRAFT = Object.freeze({
  prompt: '',
  kind: 'boolean',
  optionsText: '',
  required: true,
  knockout: false,
  expectedText: 'yes',
});

const csvValues = (value) => String(value || '')
  .split(',')
  .map((item) => item.trim())
  .filter(Boolean);

const questionDraft = (question = null) => ({
  ...EMPTY_DRAFT,
  prompt: question?.prompt || '',
  kind: question?.kind || 'boolean',
  optionsText: Array.isArray(question?.options) ? question.options.join(', ') : '',
  required: question?.required !== false,
  knockout: Boolean(question?.knockout),
  expectedText: Array.isArray(question?.knockout_expected)
    ? question.knockout_expected.join(', ')
    : (question?.kind === 'boolean' ? 'yes' : ''),
});

const kindLabel = (kind) => QUESTION_KINDS.find((item) => item.value === kind)?.label || kind;

export default function RoleScreeningQuestions({ roleId }) {
  const [questions, setQuestions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [editingId, setEditingId] = useState(null);
  const [draft, setDraft] = useState(() => ({ ...EMPTY_DRAFT }));
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [formError, setFormError] = useState('');

  const load = useCallback(async () => {
    if (!roleId) return;
    setLoading(true);
    setLoadError('');
    try {
      const response = await rolesApi.listScreeningQuestions(roleId);
      setQuestions(Array.isArray(response?.data) ? response.data : []);
    } catch {
      setLoadError('Could not load screening questions. Try again.');
    } finally {
      setLoading(false);
    }
  }, [roleId]);

  useEffect(() => { void load(); }, [load]);

  const usesOptions = draft.kind === 'single_select' || draft.kind === 'multi_select';
  const optionValues = useMemo(() => (
    draft.kind === 'boolean' ? ['yes', 'no'] : csvValues(draft.optionsText)
  ), [draft.kind, draft.optionsText]);

  const reset = () => {
    setEditingId(null);
    setDraft({ ...EMPTY_DRAFT });
    setFormError('');
  };

  const edit = (question) => {
    setEditingId(question.id);
    setDraft(questionDraft(question));
    setFormError('');
  };

  const save = async () => {
    const prompt = draft.prompt.trim();
    const options = usesOptions ? csvValues(draft.optionsText) : null;
    const expected = draft.knockout ? csvValues(draft.expectedText) : null;
    if (!prompt) {
      setFormError('Enter the candidate-facing question.');
      return;
    }
    if (usesOptions && !options.length) {
      setFormError('Add at least one choice, separated by commas.');
      return;
    }
    if (draft.knockout && !expected.length) {
      setFormError('Add at least one passing answer for a knockout question.');
      return;
    }
    setSaving(true);
    setFormError('');
    const payload = {
      prompt,
      kind: draft.kind,
      options,
      required: Boolean(draft.required),
      knockout: Boolean(draft.knockout),
      knockout_expected: expected,
    };
    try {
      const response = editingId == null
        ? await rolesApi.createScreeningQuestion(roleId, payload)
        : await rolesApi.updateScreeningQuestion(roleId, editingId, payload);
      const saved = response?.data;
      if (saved) {
        setQuestions((current) => (
          editingId == null
            ? [...current, saved]
            : current.map((item) => (item.id === editingId ? saved : item))
        ));
      } else {
        await load();
      }
      reset();
    } catch {
      setFormError('Could not save this question. Try again.');
    } finally {
      setSaving(false);
    }
  };

  const remove = async (questionId) => {
    setDeletingId(questionId);
    setFormError('');
    try {
      await rolesApi.deleteScreeningQuestion(roleId, questionId);
      setQuestions((current) => current.filter((item) => item.id !== questionId));
      if (editingId === questionId) reset();
    } catch {
      setFormError('Could not remove that question. Try again.');
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <section className="mc-agent-settings-card" data-testid="role-screening-questions">
      <div className="mc-agent-settings-card-head">
        <div>
          <h2 className="mc-agent-settings-card-title">Application <em>screening</em></h2>
          <p className="mc-agent-settings-card-help">
            Questions appear on this role&apos;s public application form. A deterministic knockout
            stops AI processing when the answer misses the passing value. With deterministic
            pre-screen rejection enabled it rejects under the configured safeguards; otherwise it
            creates a recruiter review decision. Passing answers are never exposed to candidates.
          </p>
        </div>
      </div>

      {loading ? (
        <div className="mc-agent-settings-card-help"><Spinner size={14} className="!text-current" /> Loading questions…</div>
      ) : loadError ? (
        <div className="mc-agent-warn" role="alert">
          <div>
            <div className="mc-agent-warn-body">{loadError}</div>
            <button type="button" className="taali-text-btn" onClick={() => void load()}>Retry</button>
          </div>
        </div>
      ) : questions.length ? (
        <div style={{ display: 'grid', gap: 10, marginBottom: 16 }}>
          {questions.map((question) => (
            <div key={question.id} style={{ border: '1px solid var(--line)', borderRadius: 10, padding: 12 }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
                <div>
                  <div style={{ fontWeight: 600 }}>{question.prompt}</div>
                  <div className="mc-agent-settings-card-help" style={{ marginTop: 4 }}>
                    {kindLabel(question.kind)} · {question.required ? 'Required' : 'Optional'}
                    {question.knockout ? ` · Knockout passes: ${(question.knockout_expected || []).join(', ')}` : ''}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 6 }}>
                  <Button type="button" variant="ghost" size="sm" onClick={() => edit(question)}>Edit</Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={deletingId === question.id}
                    onClick={() => void remove(question.id)}
                  >
                    {deletingId === question.id ? 'Removing…' : 'Remove'}
                  </Button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="mc-agent-settings-card-help">No extra questions yet. Applicants will only provide their contact details and resume.</p>
      )}

      <div style={{ display: 'grid', gap: 12, paddingTop: 14, borderTop: '1px solid var(--line)' }}>
        <div className="mc-kicker is-mute">{editingId == null ? 'ADD A QUESTION' : 'EDIT QUESTION'}</div>
        <label className="field">
          <span className="k">Candidate-facing question</span>
          <input
            value={draft.prompt}
            onChange={(event) => setDraft((current) => ({ ...current, prompt: event.target.value }))}
            placeholder="e.g. Are you legally authorized to work in the UAE?"
          />
        </label>
        <label className="field">
          <span className="k">Answer type</span>
          <Select aria-label="Answer type" value={draft.kind} onChange={(event) => setDraft((current) => ({
            ...current,
            kind: event.target.value,
            optionsText: '',
            expectedText: event.target.value === 'boolean' ? 'yes' : '',
          }))}>
            {QUESTION_KINDS.map((kind) => <option key={kind.value} value={kind.value}>{kind.label}</option>)}
          </Select>
        </label>
        {usesOptions ? (
          <label className="field">
            <span className="k">Choices · comma separated</span>
            <input
              value={draft.optionsText}
              onChange={(event) => setDraft((current) => ({ ...current, optionsText: event.target.value }))}
              placeholder="UAE, Saudi Arabia, Qatar"
            />
          </label>
        ) : null}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18 }}>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <input
              type="checkbox"
              checked={draft.required}
              onChange={(event) => setDraft((current) => ({ ...current, required: event.target.checked }))}
            />
            Required
          </label>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <input
              type="checkbox"
              checked={draft.knockout}
              onChange={(event) => setDraft((current) => ({ ...current, knockout: event.target.checked }))}
            />
            Deterministic knockout
          </label>
        </div>
        {draft.knockout ? (
          <label className="field">
            <span className="k">Passing answer{optionValues.length > 1 ? 's' : ''} · comma separated</span>
            <input
              list={`screening-passing-options-${roleId}`}
              value={draft.expectedText}
              onChange={(event) => setDraft((current) => ({ ...current, expectedText: event.target.value }))}
              placeholder={optionValues.join(', ') || 'Enter the accepted value'}
            />
            {optionValues.length ? (
              <datalist id={`screening-passing-options-${roleId}`}>
                {optionValues.map((option) => <option key={option} value={option} />)}
              </datalist>
            ) : null}
          </label>
        ) : null}
        {formError ? <div className="mc-agent-warn-body" role="alert">{formError}</div> : null}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          {editingId != null ? <Button type="button" variant="ghost" size="sm" onClick={reset}>Cancel</Button> : null}
          <Button type="button" variant="primary" size="sm" disabled={saving} onClick={() => void save()}>
            {saving ? 'Saving…' : editingId == null ? 'Add question' : 'Save question'}
          </Button>
        </div>
      </div>
    </section>
  );
}
