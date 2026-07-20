import { Suspense, useEffect, useRef, useState } from 'react';
import { Navigate, useParams, useSearchParams } from 'react-router-dom';

import { AssessmentPage, CandidateWelcomePage } from './lazyPages';
import { recoverCandidateRuntimeToken } from '../shared/assessment/candidateProofBinding';
import { Spinner } from '../shared/ui/TaaliPrimitives';

const assessmentFallback = (
  <div className="min-h-screen flex items-center justify-center">
    <Spinner size={28} />
  </div>
);

let nextAssessmentRuntimeIdentity = 0;

const createAssessmentRuntimeIdentity = () => {
  nextAssessmentRuntimeIdentity += 1;
  return `candidate-runtime:${nextAssessmentRuntimeIdentity}`;
};

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
  const explicitToken = searchParams.get('token');
  const recoveredToken = explicitToken ? null : recoverCandidateRuntimeToken();
  const incomingToken = explicitToken || recoveredToken || startData?.token || null;
  const incomingAssessmentId = Number(startData?.assessment_id) || null;
  const demo = searchParams.get('demo') === '1';
  const runtimeRef = useRef(null);
  const priorRuntime = runtimeRef.current;
  const tokenChanged = Boolean(
    incomingToken && priorRuntime?.token && incomingToken !== priorRuntime.token
  );
  const assessmentChanged = Boolean(
    !incomingToken
      && incomingAssessmentId
      && priorRuntime?.assessmentId
      && incomingAssessmentId !== priorRuntime.assessmentId
  );
  if (
    !priorRuntime
    || priorRuntime.demo !== demo
    || (!demo && (tokenChanged || assessmentChanged))
  ) {
    runtimeRef.current = {
      key: createAssessmentRuntimeIdentity(),
      token: incomingToken,
      assessmentId: incomingAssessmentId,
      demo,
    };
  } else {
    if (!priorRuntime.token && incomingToken) priorRuntime.token = incomingToken;
    if (incomingAssessmentId) {
      priorRuntime.assessmentId = incomingAssessmentId;
    }
  }
  const runtimeIdentity = runtimeRef.current;
  const token = incomingToken || runtimeIdentity.token;
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
        key={runtimeIdentity.key}
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
