import { Suspense, useEffect, useState } from 'react';
import { Navigate, useParams, useSearchParams } from 'react-router-dom';

import { AssessmentPage, CandidateWelcomePage } from './lazyPages';
import { recoverCandidateRuntimeToken } from '../shared/assessment/candidateProofBinding';
import { Spinner } from '../shared/ui/TaaliPrimitives';

const assessmentFallback = (
  <div className="min-h-screen flex items-center justify-center">
    <Spinner size={28} />
  </div>
);

export function CandidateWelcomeRoute({ onNavigate, onStarted }) {
  const { token } = useParams();
  return (
    <Suspense fallback={assessmentFallback}>
      <CandidateWelcomePage
        token={token || null}
        onNavigate={onNavigate}
        onStarted={onStarted}
      />
    </Suspense>
  );
}

export function CandidateWelcomeWithIdRoute({ onNavigate, onStarted }) {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token');
  if (!token) return <Navigate to="/" replace />;
  return (
    <Suspense fallback={assessmentFallback}>
      <CandidateWelcomePage
        token={token}
        onNavigate={onNavigate}
        onStarted={onStarted}
      />
    </Suspense>
  );
}

export function AssessmentLiveRoute({ startData }) {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') || recoverCandidateRuntimeToken();
  const demo = searchParams.get('demo') === '1';
  const runtimeIdentity = demo
    ? 'demo:walkthrough'
    : token
      ? `token:${token}`
      : startData?.assessment_id
        ? `assessment:${startData.assessment_id}`
        : 'candidate:unresolved';
  const [demoFixtures, setDemoFixtures] = useState(null);

  useEffect(() => {
    if (demo && !demoFixtures) {
      import('../features/demo/productWalkthroughModels').then((module) =>
        setDemoFixtures({
          startData: module.PRODUCT_WALKTHROUGH_START_DATA,
          runtime: module.PRODUCT_WALKTHROUGH.runtime,
        })
      );
    }
  }, [demo, demoFixtures]);

  if (demo && !demoFixtures) return assessmentFallback;
  return (
    <Suspense fallback={assessmentFallback}>
      <AssessmentPage
        key={runtimeIdentity}
        token={demo ? null : token}
        startData={demo ? demoFixtures.startData : startData}
        demoMode={demo}
        demoProfile={demo ? {
          ...demoFixtures.runtime,
          output: demoFixtures.runtime.output,
        } : undefined}
      />
    </Suspense>
  );
}
