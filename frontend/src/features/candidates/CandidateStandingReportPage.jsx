import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { useParams } from 'react-router-dom';

import * as apiClient from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import {
  Badge,
  Button,
  PageContainer,
  Panel,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';
import {
  buildStandingCandidateReportModel,
  COMPLETED_ASSESSMENT_STATUSES,
} from './assessmentViewModels';
import { CandidateCvSidebar } from './CandidateCvSidebar';
import { CandidateReportView } from './CandidateReportView';
import { formatDateTime, getErrorMessage } from './candidatesUiUtils';

const resolveAssessmentId = (application) => (
  application?.score_summary?.assessment_id
  || application?.valid_assessment_id
  || null
);

const resolveAssessmentStatus = (application) => (
  String(application?.score_summary?.assessment_status || application?.valid_assessment_status || '').toLowerCase()
);

const hasCompletedAssessment = (application) => (
  Boolean(resolveAssessmentId(application))
  && COMPLETED_ASSESSMENT_STATUSES.has(resolveAssessmentStatus(application))
);

const toAssessmentStatusText = (status) => {
  const cleaned = String(status || '').trim();
  if (!cleaned) return 'not started';
  return cleaned.replace(/_/g, ' ');
};

export const CandidateStandingReportPage = ({ onNavigate, NavComponent = null }) => {
  const { showToast } = useToast();
  const { applicationId } = useParams();
  const rolesApi = 'roles' in apiClient ? apiClient.roles : null;
  const [application, setApplication] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingCvText, setLoadingCvText] = useState(false);
  const [error, setError] = useState('');
  const [cvSidebarOpen, setCvSidebarOpen] = useState(false);
  const [fetchingCvApplicationId, setFetchingCvApplicationId] = useState(null);

  const numericApplicationId = Number(applicationId);
  const assessmentHistory = Array.isArray(application?.assessment_history) ? application.assessment_history : [];
  const reportModel = useMemo(() => (
    buildStandingCandidateReportModel({
      application,
      completedAssessment: null,
      identity: {
        sectionLabel: 'Standing candidate report',
        name: application?.candidate_name || application?.candidate_email || 'Candidate',
        email: application?.candidate_email || '',
        position: application?.candidate_position || '',
        roleName: application?.role_name || '',
        applicationStatus: application?.status || '',
      },
    })
  ), [application]);

  const loadApplication = useCallback(async ({ includeCvText = false } = {}) => {
    if (!rolesApi?.getApplication || !Number.isFinite(numericApplicationId)) {
      setApplication(null);
      setError('Candidate report unavailable.');
      setLoading(false);
      return null;
    }

    const setLoadingState = includeCvText ? setLoadingCvText : setLoading;
    setLoadingState(true);
    setError('');
    try {
      const res = await rolesApi.getApplication(numericApplicationId, {
        params: { include_cv_text: includeCvText },
      });
      const detail = res?.data || null;
      setApplication((current) => ({ ...(current || {}), ...(detail || {}) }));
      return detail;
    } catch (err) {
      const message = getErrorMessage(err, 'Failed to load candidate report.');
      setError(message);
      showToast(message, 'error');
      return null;
    } finally {
      setLoadingState(false);
    }
  }, [numericApplicationId, rolesApi, showToast]);

  useEffect(() => {
    void loadApplication();
  }, [loadApplication]);

  useEffect(() => {
    if (!application || !hasCompletedAssessment(application)) return;
    const assessmentId = resolveAssessmentId(application);
    if (!assessmentId) return;
    onNavigate('candidate-detail', {
      candidateDetailAssessmentId: assessmentId,
      replace: true,
    });
  }, [application, onNavigate]);

  const handleOpenCv = async () => {
    if (!application) return;
    if (!application.cv_text) {
      await loadApplication({ includeCvText: true });
    }
    setCvSidebarOpen(true);
  };

  const handleFetchCvFromWorkable = async () => {
    if (!rolesApi?.generateTaaliCvAi || !application?.id) return;
    setFetchingCvApplicationId(application.id);
    try {
      await rolesApi.generateTaaliCvAi(application.id);
      await loadApplication({ includeCvText: true });
      showToast('CV fetched and role-fit evidence refreshed.', 'success');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to fetch CV from Workable.'), 'error');
    } finally {
      setFetchingCvApplicationId(null);
    }
  };

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="candidates" onNavigate={onNavigate} /> : null}
      <PageContainer density="compact" width="wide">
        <Button
          type="button"
          variant="ghost"
          size="xs"
          className="mb-4 font-mono"
          onClick={() => onNavigate('candidates')}
        >
          <ArrowLeft size={16} /> Back to Candidates
        </Button>

        {loading ? (
          <div className="flex min-h-[280px] items-center justify-center">
            <Spinner size={22} />
          </div>
        ) : error ? (
          <Panel className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
            {error}
          </Panel>
        ) : application ? (
          <div className="space-y-4">
            <CandidateReportView model={reportModel} />

            <Panel className="p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Candidate actions</div>
                  <p className="mt-2 text-sm text-[var(--taali-text)]">
                    This page mirrors the standing candidate report used in the sidebar so recruiters can review the same evidence without returning to the pipeline table.
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={handleOpenCv}
                    disabled={loadingCvText}
                  >
                    {loadingCvText ? 'Loading CV...' : 'View CV'}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => onNavigate('candidates')}
                  >
                    Open pipeline
                  </Button>
                </div>
              </div>
            </Panel>

            <Panel className="p-4">
              <div className="mb-3 flex items-center justify-between gap-2">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Assessment history</p>
                {application?.score_summary?.has_voided_attempts ? <Badge variant="warning">Includes voided attempts</Badge> : null}
              </div>
              {assessmentHistory.length === 0 ? (
                <p className="text-sm text-[var(--taali-muted)]">No assessment attempts yet for this role.</p>
              ) : (
                <div className="space-y-3">
                  {assessmentHistory.map((item) => (
                    <div key={item.assessment_id || `${item.task_name}-${item.created_at || ''}`} className="border border-[var(--taali-border-muted)] bg-[var(--taali-surface-subtle)] px-3 py-3">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="font-semibold text-[var(--taali-text)]">{item.task_name || `Assessment #${item.assessment_id}`}</p>
                            {item.is_voided ? <Badge variant="warning">Voided</Badge> : <Badge variant="muted">Attempt</Badge>}
                          </div>
                          <p className="mt-1 text-sm text-[var(--taali-muted)]">
                            Status: {toAssessmentStatusText(item.status)}
                            {item.completed_at ? ` • Completed ${formatDateTime(item.completed_at)}` : ''}
                            {!item.completed_at && item.created_at ? ` • Created ${formatDateTime(item.created_at)}` : ''}
                          </p>
                          {item.void_reason ? (
                            <p className="mt-1 text-sm text-amber-700">Void reason: {item.void_reason}</p>
                          ) : null}
                        </div>
                        {item.assessment_id && COMPLETED_ASSESSMENT_STATUSES.has(String(item.status || '').toLowerCase()) ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => onNavigate('candidate-detail', {
                              candidateDetailAssessmentId: item.assessment_id,
                            })}
                          >
                            View assessment
                          </Button>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>
        ) : (
          <Panel className="p-4 text-sm text-[var(--taali-muted)]">
            Candidate report unavailable.
          </Panel>
        )}
      </PageContainer>

      <CandidateCvSidebar
        open={cvSidebarOpen}
        application={application}
        onClose={() => setCvSidebarOpen(false)}
        onFetchCvFromWorkable={handleFetchCvFromWorkable}
        fetchingCvApplicationId={fetchingCvApplicationId}
      />
    </div>
  );
};

export default CandidateStandingReportPage;
