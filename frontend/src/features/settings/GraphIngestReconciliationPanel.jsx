import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react';

import { roles as rolesApi } from '../../shared/api';
import { formatRelativeDateTime } from '../../shared/ui/RecruiterDesignPrimitives';

import './graph-ingest-reconciliation.css';

const CONFIRM_PRESENT = 'confirm_entire_operation_present';
const RETRY_ABSENT = 'retry_after_entire_operation_absent';
const PAGE_SIZE = 20;

const entityLabel = (workKind) => ({
  candidate: 'Candidate',
  interview: 'Interview',
  event: 'Pipeline event',
}[String(workKind || '')] || 'Talent data');

const safeApiFailure = (error, action = 'load') => {
  const status = Number(error?.response?.status || 0);
  if (status === 403) return 'Only a workspace owner can review graph operations.';
  if (status === 409) return 'This operation changed. Refresh its evidence before acting.';
  if (status === 422) {
    return action === 'resolve'
      ? 'The exact whole-operation attestation is required before this action can run.'
      : 'The evidence page could not be continued safely. Refresh from the first page.';
  }
  if (action === 'resolve') {
    return 'The resolution could not be saved. No provider retry was started.';
  }
  return 'Graph reconciliation evidence could not be loaded. Try again.';
};

const providerOutcomeLabel = (code) => {
  const value = String(code || '');
  if (value === 'provider_attempt_worker_lost') {
    return 'The worker stopped after provider work began.';
  }
  if (value.startsWith('provider_outcome_ambiguous:')) {
    return 'The provider outcome could not be verified.';
  }
  if (value === 'support_review_required') {
    return 'Stored evidence requires support review.';
  }
  return 'The provider outcome requires review.';
};

const resolutionActionLabel = (action) => ({
  [CONFIRM_PRESENT]: 'Marked fully present without replay',
  [RETRY_ABSENT]: 'Authorized retry after full absence',
}[String(action || '')] || 'Recorded a manual resolution');

const SHA256_PATTERN = /^[0-9a-f]{64}$/;

const manifestEvidenceAvailable = (operation) => {
  if (operation?.operation_manifest_state !== 'available') return false;
  if (!SHA256_PATTERN.test(String(operation?.operation_manifest_sha256 || ''))) {
    return false;
  }
  const count = operation?.operation_episode_count;
  const episodes = operation?.operation_episodes;
  if (!Number.isInteger(count) || count < 1 || count > 100 || !Array.isArray(episodes)) {
    return false;
  }
  if (episodes.length !== count) return false;
  return episodes.every((episode, ordinal) => (
    episode
    && episode.ordinal === ordinal
    && typeof episode.episode_name === 'string'
    && episode.episode_name.trim().length > 0
    && SHA256_PATTERN.test(String(episode.episode_sha256 || ''))
  ));
};

const operationBlocker = (operation) => {
  if (!operation?.attempt_fence_available || !operation?.expected_attempt_nonce) {
    return 'The exact provider attempt identity is unavailable.';
  }
  if (operation?.source_evidence_state !== 'available') {
    return 'The source evidence requires support review.';
  }
  if (operation?.reconciliation_history_state !== 'available') {
    return 'The retained reconciliation history requires support review.';
  }
  if (!manifestEvidenceAvailable(operation)) {
    return 'The exact provider payload manifest requires support review.';
  }
  if (operation?.status !== 'reconciliation_required') {
    return 'This operation is no longer awaiting reconciliation.';
  }
  return null;
};

const DateEvidence = ({ value }) => {
  if (!value) return <span>Unknown</span>;
  return (
    <time dateTime={value} title={value}>
      {formatRelativeDateTime(value)}
    </time>
  );
};

function OperationCard({
  operation,
  draft,
  acting,
  evidenceStale,
  onBegin,
  onToggleAttestation,
  onCancel,
  onConfirm,
}) {
  const blocker = evidenceStale
    ? 'Fresh graph evidence is required before another action.'
    : operationBlocker(operation);
  const sourceLabels = (operation.source_refs || []).map(
    (source) => `${String(source.kind).replace(/_/g, ' ')} #${source.id}`,
  );
  const manifestEpisodes = Array.isArray(operation.operation_episodes)
    ? operation.operation_episodes
    : [];
  const activeDraft = draft?.operationId === operation.operation_id ? draft : null;
  const isActing = acting === operation.operation_id;
  const anyActing = Boolean(acting);
  const isPresent = activeDraft?.action === CONFIRM_PRESENT;
  const confirmationCopy = isPresent
    ? 'I verified that the entire exact graph operation is fully present. It is not partial or uncertain.'
    : 'I verified that the entire exact graph operation is entirely absent. It is not partial or uncertain.';
  const cardHeadingId = `graph-reconcile-operation-${operation.operation_id}`;

  return (
    <article className="graph-reconcile-card" aria-labelledby={cardHeadingId}>
      <div className="graph-reconcile-card__header">
        <div>
          <strong id={cardHeadingId}>
            {entityLabel(operation.work_kind)} #{operation.entity_id}
          </strong>
          <span>{providerOutcomeLabel(operation.last_error_code)}</span>
        </div>
        <span className="graph-reconcile-card__badge">Provider outcome unresolved</span>
      </div>

      <dl className="graph-reconcile-evidence">
        <div>
          <dt>Operation ID</dt>
          <dd><code>{operation.operation_id}</code></dd>
        </div>
        <div>
          <dt>Provider attempt ID</dt>
          <dd><code>{operation.expected_attempt_nonce || 'Unavailable'}</code></dd>
        </div>
        <div>
          <dt>Provider work began</dt>
          <dd><DateEvidence value={operation.provider_attempt_started_at} /></dd>
        </div>
        <div>
          <dt>Dispatch attempts</dt>
          <dd>{Number(operation.dispatch_attempts || 0)}</dd>
        </div>
        <div>
          <dt>Prior reconciliations</dt>
          <dd>{Number(operation.reconciliation_count || 0)}</dd>
        </div>
        <div>
          <dt>Source evidence</dt>
          <dd>{sourceLabels.length ? sourceLabels.join(' · ') : 'Support review required'}</dd>
        </div>
        <div className="graph-reconcile-evidence__wide">
          <dt>Source fingerprint</dt>
          <dd><code>{operation.source_refs_sha256}</code></dd>
        </div>
        <div className="graph-reconcile-evidence__wide">
          <dt>Exact provider payload fingerprint</dt>
          <dd>
            <code>{operation.operation_manifest_sha256 || 'Support review required'}</code>
          </dd>
        </div>
        <div className="graph-reconcile-evidence__wide">
          <dt>Ordered provider episodes ({Number(operation.operation_episode_count || 0)})</dt>
          <dd>
            {manifestEpisodes.length ? (
              <ol className="graph-reconcile-manifest">
                {manifestEpisodes.map((episode) => (
                  <li key={`${episode.ordinal}:${episode.episode_sha256}`}>
                    <span>{Number(episode.ordinal) + 1}. {episode.episode_name}</span>
                    <code>{episode.episode_sha256}</code>
                  </li>
                ))}
              </ol>
            ) : 'Support review required'}
          </dd>
        </div>
        {operation.last_resolution ? (
          <div className="graph-reconcile-evidence__wide">
            <dt>Last retained resolution</dt>
            <dd>
              {resolutionActionLabel(operation.last_resolution.action)} by workspace user #
              {operation.last_resolution.actor_id} ·{' '}
              <DateEvidence value={operation.last_resolution.resolved_at} />
            </dd>
          </div>
        ) : null}
      </dl>

      {blocker ? (
        <div className="graph-reconcile-blocker" role="status">
          <ShieldAlert size={16} aria-hidden="true" />
          <span>{blocker} Actions remain disabled so evidence is not guessed or overwritten.</span>
        </div>
      ) : null}
      <div className="graph-reconcile-actions">
        <button
          type="button"
          className="bg-jobs-panel-btn"
          onClick={() => onBegin(operation, CONFIRM_PRESENT)}
          disabled={Boolean(blocker) || anyActing}
        >
          Confirm fully present
        </button>
        <button
          type="button"
          className="bg-jobs-panel-btn graph-reconcile-actions__retry"
          onClick={() => onBegin(operation, RETRY_ABSENT)}
          disabled={Boolean(blocker) || anyActing}
        >
          Retry after full absence
        </button>
      </div>

      {activeDraft && !blocker ? (
        <fieldset className="graph-reconcile-confirmation" disabled={anyActing}>
          <legend>
            {isPresent ? 'Confirm the provider result' : 'Authorize one ordinary outbox retry'}
          </legend>
          <p>
            This confirmation is fenced to operation <code>{operation.operation_id}</code> and
            provider attempt <code>{activeDraft.expectedAttemptNonce}</code>.
          </p>
          <label>
            <input
              type="checkbox"
              checked={activeDraft.checked}
              onChange={(event) => onToggleAttestation(event.target.checked)}
            />
            <span>{confirmationCopy}</span>
          </label>
          <div className="graph-reconcile-confirmation__actions">
            <button type="button" className="bg-jobs-panel-btn" onClick={onCancel}>
              Cancel
            </button>
            <button
              type="button"
              className="bg-jobs-panel-btn graph-reconcile-confirmation__submit"
              onClick={onConfirm}
              disabled={!activeDraft.checked || anyActing}
            >
              {isActing
                ? 'Saving…'
                : (isPresent ? 'Mark exact operation complete' : 'Authorize exact operation retry')}
            </button>
          </div>
        </fieldset>
      ) : null}
    </article>
  );
}

export default function GraphIngestReconciliationPanel() {
  const requestGeneration = useRef(0);
  const operationsRef = useRef([]);
  const nextCursorRef = useRef(null);
  const [operations, setOperations] = useState([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [evidenceStale, setEvidenceStale] = useState(false);
  const [draft, setDraft] = useState(null);
  const [acting, setActing] = useState(null);

  const loadOperations = useCallback(async ({ initial = false, append = false } = {}) => {
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    const requestedCursor = append ? nextCursorRef.current : null;
    if (!append) nextCursorRef.current = null;
    if (initial) setLoading(true);
    else if (append) setLoadingMore(true);
    else setRefreshing(true);
    setError('');
    setDraft(null);
    try {
      const { data } = await rolesApi.graphIngestReconciliations(
        requestedCursor == null
          ? { limit: PAGE_SIZE }
          : { limit: PAGE_SIZE, cursor: requestedCursor },
      );
      if (requestGeneration.current !== generation) return false;
      const incoming = Array.isArray(data?.operations) ? data.operations : [];
      let nextOperations = incoming;
      if (append) {
        const seen = new Set(operationsRef.current.map((item) => item.operation_id));
        nextOperations = [
          ...operationsRef.current,
          ...incoming.filter((item) => !seen.has(item.operation_id)),
        ];
      }
      operationsRef.current = nextOperations;
      nextCursorRef.current = typeof data?.next_cursor === 'string'
        ? data.next_cursor
        : null;
      setOperations(nextOperations);
      setHasMore(Boolean(data?.has_more) && nextCursorRef.current !== null);
      if (!append) setEvidenceStale(false);
      return true;
    } catch (loadError) {
      if (requestGeneration.current !== generation) return false;
      setError(safeApiFailure(loadError));
      setEvidenceStale(true);
      return false;
    } finally {
      if (requestGeneration.current === generation) {
        setLoading(false);
        setRefreshing(false);
        setLoadingMore(false);
      }
    }
  }, []);

  useEffect(() => {
    loadOperations({ initial: true });
    return () => {
      requestGeneration.current += 1;
    };
  }, [loadOperations]);

  const beginResolution = (operation, action) => {
    if (evidenceStale || operationBlocker(operation)) return;
    setError('');
    setNotice('');
    setDraft({
      operationId: operation.operation_id,
      expectedAttemptNonce: operation.expected_attempt_nonce,
      action,
      checked: false,
    });
  };

  const confirmResolution = async () => {
    if (!draft?.checked || !draft?.expectedAttemptNonce || acting || evidenceStale) return;
    const operation = operations.find((item) => item.operation_id === draft.operationId);
    if (
      !operation
      || operationBlocker(operation)
      || operation.expected_attempt_nonce !== draft.expectedAttemptNonce
    ) {
      setError('This operation changed. Refresh its evidence before acting.');
      setDraft(null);
      return;
    }

    setActing(draft.operationId);
    setError('');
    setNotice('');
    try {
      const resolvedOperationId = draft.operationId;
      const resolvedAction = draft.action;
      await rolesApi.resolveGraphIngestReconciliation(resolvedOperationId, {
        action: resolvedAction,
        expected_attempt_nonce: draft.expectedAttemptNonce,
        entire_operation_present_attested: resolvedAction === CONFIRM_PRESENT,
        entire_operation_absent_attested: resolvedAction === RETRY_ABSENT,
      });
      const resolutionNotice = resolvedAction === CONFIRM_PRESENT
          ? 'The exact operation was marked complete without replay.'
          : 'The absence attestation was saved and the ordinary outbox retry was requested.';
      // A successful fenced mutation is authoritative. Remove its stale action
      // immediately; a failed follow-up read must never offer the operation a
      // second time as though nothing happened.
      const retainedOperations = operationsRef.current.filter(
        (item) => item.operation_id !== resolvedOperationId,
      );
      operationsRef.current = retainedOperations;
      setOperations(retainedOperations);
      setDraft(null);
      setNotice(resolutionNotice);
      const refreshed = await loadOperations();
      if (!refreshed) {
        setEvidenceStale(true);
        setNotice(resolutionNotice);
        setError(
          'The resolution was saved, but refreshed graph evidence could not be loaded. '
          + 'Refresh before taking another action.',
        );
      }
    } catch (resolveError) {
      const safeMessage = safeApiFailure(resolveError, 'resolve');
      if (Number(resolveError?.response?.status || 0) === 409) {
        const refreshed = await loadOperations();
        if (refreshed) {
          setError(
            'The operation changed. Fresh evidence is shown; review it before acting.',
          );
        }
      } else {
        setError(safeMessage);
      }
    } finally {
      setActing(null);
    }
  };

  return (
    <section className="graph-reconcile-panel" aria-labelledby="graph-reconcile-title">
      <div className="graph-reconcile-panel__header">
        <div>
          <h3 id="graph-reconcile-title">Graph provider reconciliation</h3>
          <p>
            Owner-only operations whose provider result cannot be verified. Nothing is replayed
            until the entire exact operation is confirmed absent.
          </p>
        </div>
        <button
          type="button"
          className="bg-jobs-panel-btn graph-reconcile-panel__refresh"
          onClick={() => loadOperations()}
          disabled={refreshing || Boolean(acting)}
        >
          <RefreshCw size={13} aria-hidden="true" />
          {refreshing ? 'Refreshing…' : 'Refresh evidence'}
        </button>
      </div>

      <div className="graph-reconcile-panel__message" aria-live="polite">
        {notice ? (
          <span className="graph-reconcile-panel__notice">
            <CheckCircle2 size={15} aria-hidden="true" /> {notice}
          </span>
        ) : null}
        {error ? (
          <span className="graph-reconcile-panel__error">
            <AlertTriangle size={15} aria-hidden="true" /> {error}
          </span>
        ) : null}
      </div>

      {loading ? (
        <div className="graph-reconcile-panel__empty">Loading retained graph evidence…</div>
      ) : null}
      {!loading && error && operations.length === 0 ? (
        <button type="button" className="bg-jobs-panel-btn" onClick={() => loadOperations()}>
          Try again
        </button>
      ) : null}
      {!loading && !error && operations.length === 0 ? (
        <div className="graph-reconcile-panel__empty">
          No graph operations currently require reconciliation.
        </div>
      ) : null}
      {!loading && operations.length > 0 ? (
        <div className="graph-reconcile-panel__list">
          {operations.map((operation) => (
            <OperationCard
              key={operation.operation_id}
              operation={operation}
              draft={draft}
              acting={acting}
              evidenceStale={evidenceStale}
              onBegin={beginResolution}
              onToggleAttestation={(checked) => setDraft((current) => (
                current ? { ...current, checked } : current
              ))}
              onCancel={() => setDraft(null)}
              onConfirm={confirmResolution}
            />
          ))}
        </div>
      ) : null}
      {hasMore ? (
        <div className="graph-reconcile-panel__load-more">
          <button
            type="button"
            className="bg-jobs-panel-btn"
            onClick={() => loadOperations({ append: true })}
            disabled={loadingMore || refreshing || Boolean(acting) || evidenceStale}
          >
            {loadingMore ? 'Loading more…' : 'Load more evidence'}
          </button>
        </div>
      ) : null}
    </section>
  );
}

export {
  CONFIRM_PRESENT,
  RETRY_ABSENT,
  entityLabel,
  manifestEvidenceAvailable,
  operationBlocker,
  providerOutcomeLabel,
  safeApiFailure,
};
