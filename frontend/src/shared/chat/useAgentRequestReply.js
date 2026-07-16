import { useCallback, useRef, useState } from 'react';

const responseMode = (item) => {
  const schema = item?.response_schema || {};
  const valueSchema = schema?.properties?.value || schema;
  return item?.input_mode
    || (['integer', 'number'].includes(valueSchema?.type) ? valueSchema.type : 'string');
};

const requestIdFor = (item) => item?.needs_input_id ?? item?.id;

const coerceReply = (item, raw) => {
  const text = String(raw || '').trim();
  if (!text) return { error: 'Write an answer before sending.' };

  const mode = responseMode(item);
  if (!['integer', 'number', 'option_or_number'].includes(mode)) {
    return { value: text };
  }

  const value = Number(text);
  if (!Number.isFinite(value)) return { error: 'Enter a valid number.' };
  if (mode === 'integer' && !Number.isInteger(value)) {
    return { error: 'Enter a whole number.' };
  }

  const schema = item?.response_schema || {};
  const valueSchema = schema?.properties?.value || schema;
  if (typeof valueSchema?.minimum === 'number' && value < valueSchema.minimum) {
    return { error: `Enter ${valueSchema.minimum} or more.` };
  }
  if (typeof valueSchema?.maximum === 'number' && value > valueSchema.maximum) {
    return { error: `Enter ${valueSchema.maximum} or less.` };
  }
  return { value };
};

/**
 * Moves free-form needs-input answers into the shared composer without losing
 * whatever the recruiter was already drafting. Quick-choice answers continue
 * to use their explicit buttons, preserving the server's typed response shape.
 */
export function useAgentRequestReply({ value, onChange, onAnswer }) {
  const [request, setRequest] = useState(null);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const savedDraftRef = useRef('');
  // Every reply-context change invalidates work started for the previous one.
  // This lets people cancel or switch agents during a slow save without the
  // stale completion clearing a newer draft or closing a newer request.
  const generationRef = useRef(0);

  const beginReply = useCallback((item) => {
    if (!item) return;
    if (!request) savedDraftRef.current = value || '';
    generationRef.current += 1;
    setRequest(item);
    setError('');
    onChange('');
  }, [onChange, request, value]);

  const cancelReply = useCallback(() => {
    generationRef.current += 1;
    setRequest(null);
    setError('');
    onChange(savedDraftRef.current);
    savedDraftRef.current = '';
  }, [onChange]);

  const submitReply = useCallback(async (raw) => {
    if (!request || submitting) return false;
    const parsed = coerceReply(request, raw);
    if (parsed.error) {
      setError(parsed.error);
      return false;
    }

    const generation = generationRef.current;
    setError('');
    setSubmitting(true);
    let saved;
    try {
      saved = await onAnswer?.(requestIdFor(request), { value: parsed.value });
    } catch {
      // Keep reply mode open when an integration throws instead of returning
      // false. The composer owns the recovery path, so no answer draft is lost.
      saved = false;
    } finally {
      setSubmitting(false);
    }
    if (generationRef.current !== generation) return false;
    if (saved === false) {
      setError('That answer was not saved. Try again.');
      return false;
    }

    generationRef.current += 1;
    setRequest(null);
    onChange(savedDraftRef.current);
    savedDraftRef.current = '';
    return true;
  }, [onAnswer, onChange, request, submitting]);

  return {
    beginReply,
    cancelReply,
    submitReply,
    replying: Boolean(request),
    submitting,
    replyTo: request ? {
      id: requestIdFor(request),
      label: request.title || 'Reply to agent',
      prompt: request.prompt,
      error,
    } : null,
  };
}

export default useAgentRequestReply;
