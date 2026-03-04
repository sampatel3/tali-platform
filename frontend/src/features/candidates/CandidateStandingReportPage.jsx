import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

import * as apiClient from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import { PageContainer, Panel, Spinner } from '../../shared/ui/TaaliPrimitives';
import { COMPLETED_ASSESSMENT_STATUSES } from './assessmentViewModels';
import { AssessmentResultsPage } from './CandidateDetailPage';
import { getErrorMessage } from './candidatesUiUtils';

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

export const CandidateStandingReportPage = ({ onNavigate, NavComponent = null }) => {
  const { showToast } = useToast();
  const { applicationId } = useParams();
  const rolesApi = 'roles' in apiClient ? apiClient.roles : null;
  const [application, setApplication] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const numericApplicationId = Number(applicationId);

  const loadApplication = useCallback(async () => {
    if (!rolesApi?.getApplication || !Number.isFinite(numericApplicationId)) {
      setApplication(null);
      setError('Candidate report unavailable.');
      setLoading(false);
      return;
    }

    setLoading(true);
    setError('');
    try {
      const res = await rolesApi.getApplication(numericApplicationId);
      setApplication(res?.data || null);
    } catch (err) {
      const message = getErrorMessage(err, 'Failed to load candidate report.');
      setApplication(null);
      setError(message);
      showToast(message, 'error');
    } finally {
      setLoading(false);
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

  if (loading) {
    return (
      <div>
        {NavComponent ? <NavComponent currentPage="candidates" onNavigate={onNavigate} /> : null}
        <PageContainer density="compact" width="wide">
          <div className="flex min-h-[280px] items-center justify-center">
            <Spinner size={22} />
          </div>
        </PageContainer>
      </div>
    );
  }

  if (error || !application) {
    return (
      <div>
        {NavComponent ? <NavComponent currentPage="candidates" onNavigate={onNavigate} /> : null}
        <PageContainer density="compact" width="wide">
          <Panel className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
            {error || 'Candidate report unavailable.'}
          </Panel>
        </PageContainer>
      </div>
    );
  }

  return (
    <AssessmentResultsPage
      application={application}
      candidate={null}
      onNavigate={onNavigate}
      NavComponent={NavComponent}
      backTo={{ page: 'candidates', label: 'Back to Candidates' }}
    />
  );
};

export default CandidateStandingReportPage;
