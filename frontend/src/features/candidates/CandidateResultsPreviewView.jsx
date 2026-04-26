import React from 'react';

import { PageContainer, Panel } from '../../shared/ui/TaaliPrimitives';
import {
  RecruiterPageHero,
  WorkableComparisonCard,
  buildStatusHeroPill,
  buildWorkableHeroPill,
} from '../../shared/ui/RecruiterDesignPrimitives';
import { CandidateAssessmentSummaryView } from './CandidateAssessmentSummaryView';
import { PRODUCT_WALKTHROUGH } from '../demo/productWalkthroughModels';

const DEFAULT_PREVIEW_MODEL = PRODUCT_WALKTHROUGH.report;

export const CandidateResultsPreviewView = ({
  className = '',
  maxHeightClass = 'max-h-[35rem]',
  scaleClassName = 'scale-[0.78]',
  scaledWidth = '128%',
  lightMode = false,
  previewModel = DEFAULT_PREVIEW_MODEL,
}) => {
  const reportModel = previewModel?.reportModel || DEFAULT_PREVIEW_MODEL.reportModel;
  const hero = previewModel?.hero || DEFAULT_PREVIEW_MODEL.hero;
  const workable = previewModel?.workable || DEFAULT_PREVIEW_MODEL.workable;
  const previewThemeClass = lightMode ? 'taali-preview-scope-light' : 'taali-preview-scope-dark';

  return (
    <div
      className={`overflow-hidden bg-[var(--taali-bg)] ${previewThemeClass} ${className}`}
      style={{ colorScheme: lightMode ? 'light' : 'dark' }}
    >
      <div className={`${maxHeightClass} overflow-hidden`}>
        <div className={`origin-top-left ${scaleClassName}`} style={{ width: scaledWidth }}>
          <div className="min-h-full bg-[var(--taali-bg)] text-[var(--taali-text)]">
            <PageContainer density="compact" width="wide" className="!px-4 !pb-5 !pt-4 md:!px-5">
              <RecruiterPageHero
                eyebrow={hero?.eyebrow || 'Candidate standing report'}
                title={hero?.title || 'Candidate walkthrough'}
                subtitle={
                  hero?.subtitle
                  || 'The recruiter view keeps the AI-collaboration signal, the report, and the integration context in one place.'
                }
                pills={[
                  buildStatusHeroPill(hero?.stageLabel || 'Stage · review'),
                  buildStatusHeroPill(hero?.outcomeLabel || 'Outcome · review'),
                  buildWorkableHeroPill(hero?.workableLabel || 'Synced from Workable'),
                ]}
                stats={Array.isArray(hero?.stats) ? hero.stats : []}
              />

              <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
                <CandidateAssessmentSummaryView
                  reportModel={reportModel}
                  variant="page"
                  showIdentityTitle={false}
                  showInterviewGuidanceAction={false}
                />

                <div className="space-y-4">
                  <WorkableComparisonCard
                    workableRawScore={workable?.workableRawScore}
                    taaliScore={workable?.taaliScore}
                    posted={Boolean(workable?.posted)}
                    postedAt={workable?.postedAt || null}
                    workableProfileUrl={workable?.workableProfileUrl || ''}
                    scorePrecedence={workable?.scorePrecedence || 'workable_first'}
                  />

                  <Panel className="p-4">
                    <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                      What recruiters review
                    </div>
                    <div className="mt-2 text-sm text-[var(--taali-text)]">
                      One report brings together AI use, role-fit evidence, transcript context, and the current ATS
                      signal before the hiring team makes a decision.
                    </div>
                  </Panel>
                </div>
              </div>
            </PageContainer>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CandidateResultsPreviewView;
