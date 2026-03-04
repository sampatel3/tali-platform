import React, { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Printer } from 'lucide-react';
import { useParams, useSearchParams } from 'react-router-dom';

import * as apiClient from '../../shared/api';
import {
  Button,
  PageContainer,
  Panel,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';
import { buildStandingCandidateReportModel } from './assessmentViewModels';
import { CandidateAssessmentSummaryView } from './CandidateAssessmentSummaryView';
import {
  buildAssessmentReportIdentity,
  buildClientReportFilenameStem,
} from './clientReportUtils';

const PRINT_DELAY_MS = 500;

export const CandidateClientReportPage = ({ onNavigate }) => {
  const { assessmentId } = useParams();
  const [searchParams] = useSearchParams();
  const assessmentsApi = apiClient.assessments;
  const [assessment, setAssessment] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [printRequested, setPrintRequested] = useState(false);

  const numericAssessmentId = Number(assessmentId);
  const identity = useMemo(
    () => buildAssessmentReportIdentity(assessment),
    [assessment]
  );
  const filenameStem = useMemo(
    () => buildClientReportFilenameStem(identity.roleName, identity.name),
    [identity.name, identity.roleName]
  );
  const reportModel = useMemo(
    () => buildStandingCandidateReportModel({
      application: null,
      completedAssessment: assessment,
      identity,
    }),
    [assessment, identity]
  );

  useEffect(() => {
    if (!Number.isFinite(numericAssessmentId)) {
      setAssessment(null);
      setError('Client report unavailable.');
      setLoading(false);
      return undefined;
    }

    let cancelled = false;
    setLoading(true);
    setError('');

    assessmentsApi.get(numericAssessmentId)
      .then((res) => {
        if (cancelled) return;
        setAssessment(res?.data || null);
      })
      .catch((err) => {
        if (cancelled) return;
        setAssessment(null);
        setError(err?.response?.data?.detail || 'Failed to load client report.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [assessmentsApi, numericAssessmentId]);

  useEffect(() => {
    if (typeof document === 'undefined') return undefined;
    const previousTitle = document.title;
    document.title = filenameStem;
    return () => {
      document.title = previousTitle;
    };
  }, [filenameStem]);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    if (searchParams.get('print') !== '1' || loading || !assessment || printRequested) {
      return undefined;
    }

    const timeoutId = window.setTimeout(() => {
      window.print();
      setPrintRequested(true);
    }, PRINT_DELAY_MS);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [assessment, loading, printRequested, searchParams]);

  return (
    <div className="taali-client-report-page min-h-screen">
      <PageContainer width="wide" className="taali-client-report-shell">
        <div className="taali-print-hidden mb-4 flex flex-wrap items-center justify-between gap-2">
          <Button type="button" variant="ghost" size="xs" className="font-mono" onClick={() => onNavigate('assessment-results', {
            candidateDetailAssessmentId: numericAssessmentId,
          })}>
            <ArrowLeft size={16} /> Back to assessment
          </Button>
          <Button type="button" variant="secondary" size="sm" onClick={() => window.print()}>
            <Printer size={16} /> Print / save PDF
          </Button>
        </div>

        {loading ? (
          <div className="flex min-h-[280px] items-center justify-center">
            <Spinner size={24} />
          </div>
        ) : error ? (
          <Panel className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
            {error}
          </Panel>
        ) : reportModel ? (
          <div className="space-y-4">
            <Panel className="overflow-hidden p-0">
              <div
                className="px-5 py-4 md:px-6"
                style={{
                  background: 'linear-gradient(135deg, var(--taali-purple), var(--taali-purple-hover))',
                  color: 'var(--taali-inverse-text)',
                }}
              >
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.16em]" style={{ color: 'rgba(248, 250, 252, 0.8)' }}>TAALI</p>
                    <h1 className="taali-display mt-2 text-2xl font-semibold tracking-tight">Client report</h1>
                  </div>
                  <p className="text-sm font-medium" style={{ color: 'rgba(248, 250, 252, 0.8)' }}>Employer-facing summary</p>
                </div>
              </div>
            </Panel>

            <CandidateAssessmentSummaryView reportModel={reportModel} />
          </div>
        ) : null}
      </PageContainer>
    </div>
  );
};

export default CandidateClientReportPage;
