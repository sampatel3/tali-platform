import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { Copy, Mail, ExternalLink, Eye } from 'lucide-react';

import * as apiClient from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import {
  Button,
  Input,
  Panel,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';
import {
  WorkableComparisonCard,
} from '../../shared/ui/RecruiterDesignPrimitives';
import { buildClientReportFilenameStem } from './clientReportUtils';
import { buildStandingCandidateReportModel, COMPLETED_ASSESSMENT_STATUSES } from './assessmentViewModels';
import {
  getErrorMessage,
  resolveCvMatchDetails,
  extractRequirementEvidence,
  extractRequirementKey,
} from './candidatesUiUtils';
import {
  AI_SHOWCASE_APPLICATION,
  AI_SHOWCASE_COMPLETED_ASSESSMENT,
} from '../demo/productWalkthroughModels';

const resolveAssessmentId = (application) => (
  application?.score_summary?.assessment_id
  || application?.valid_assessment_id
  || null
);

const resolveAssessmentStatus = (application) => (
  String(application?.score_summary?.assessment_status || application?.valid_assessment_status || '').toLowerCase()
);

const REPORT_TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'assessment', label: 'Assessment' },
  { id: 'cv', label: 'CV & match', internalOnly: true },
  { id: 'prep', label: 'Interview prep' },
  { id: 'notes', label: 'Notes & timeline', internalOnly: true },
];

const INTERNAL_TABS = new Set(REPORT_TABS.filter((tab) => tab.internalOnly).map((tab) => tab.id));
const REPORT_TAB_IDS = new Set(REPORT_TABS.map((tab) => tab.id));

// Inline viewer for the candidate's CV file. Uses the existing
// /candidates/{id}/documents/cv blob endpoint. Caches the blob URL
// across re-renders; cleans up on unmount. Inline preview only works for
// PDFs (browser-native <iframe>); other extensions show a download
// button.
const CV_VIEWER_PDF_HEIGHT = 720;

const inferCvMime = (filename) => {
  const ext = String(filename || '').split('.').pop().toLowerCase();
  if (ext === 'pdf') return 'application/pdf';
  if (ext === 'docx') return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
  if (ext === 'doc') return 'application/msword';
  if (ext === 'txt') return 'text/plain';
  return '';
};

const CvViewer = ({ candidateId, filename, uploadedAt, candidatesApi, parsedSections }) => {
  const [blobUrl, setBlobUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [showInline, setShowInline] = useState(false);

  const mime = inferCvMime(filename);
  const isPdf = mime === 'application/pdf';
  const downloadName = filename || 'candidate-cv';

  useEffect(() => {
    return () => {
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [blobUrl]);

  const ensureBlob = useCallback(async () => {
    if (blobUrl) return blobUrl;
    if (!candidateId || !candidatesApi?.downloadDocument) return '';
    setLoading(true);
    setErrorMessage('');
    try {
      const res = await candidatesApi.downloadDocument(candidateId, 'cv');
      const blob = new Blob([res.data], mime ? { type: mime } : undefined);
      const url = URL.createObjectURL(blob);
      setBlobUrl(url);
      return url;
    } catch (err) {
      setErrorMessage('Failed to load CV.');
      return '';
    } finally {
      setLoading(false);
    }
  }, [blobUrl, candidateId, candidatesApi, mime]);

  const handleDownload = useCallback(async () => {
    const url = await ensureBlob();
    if (!url) return;
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = downloadName;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  }, [ensureBlob, downloadName]);

  const handleTogglePreview = useCallback(async () => {
    if (showInline) {
      setShowInline(false);
      return;
    }
    if (!blobUrl) await ensureBlob();
    setShowInline(true);
  }, [showInline, blobUrl, ensureBlob]);

  if (!filename) {
    return (
      <div className="cv-viewer empty">
        <div className="cv-viewer-head">
          <div>
            <div className="sub">Candidate CV</div>
            <div className="headline">No CV on file</div>
          </div>
        </div>
        <div className="cv-viewer-empty-body">
          Click "Fetch CVs" on the role pipeline (or upload one manually) to score this candidate.
        </div>
      </div>
    );
  }

  return (
    <div className="cv-viewer">
      <div className="cv-viewer-head">
        <div>
          <div className="sub">Candidate CV ✓ fetched</div>
          <div className="headline">{filename}</div>
          {uploadedAt ? (
            <div className="muted-xs">Last updated {new Date(uploadedAt).toLocaleString()}</div>
          ) : null}
        </div>
        <div className="cv-viewer-actions">
          {isPdf ? (
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={handleTogglePreview}
              disabled={loading}
            >
              {loading ? 'Loading…' : showInline ? 'Hide preview' : 'Preview inline'}
            </button>
          ) : null}
          <button
            type="button"
            className="btn btn-purple btn-sm"
            onClick={handleDownload}
            disabled={loading}
          >
            {loading ? 'Loading…' : 'Download'}
          </button>
        </div>
      </div>
      {errorMessage ? <div className="cv-viewer-error">{errorMessage}</div> : null}
      {showInline && isPdf && blobUrl ? (
        <iframe
          title="Candidate CV"
          src={blobUrl}
          className="cv-viewer-frame"
          style={{ width: '100%', height: CV_VIEWER_PDF_HEIGHT, border: '1px solid var(--taali-border)', borderRadius: 8 }}
        />
      ) : null}
      {parsedSections ? <CvParsedSections sections={parsedSections} /> : null}
    </div>
  );
};

const CvParsedSections = ({ sections }) => {
  if (!sections || typeof sections !== 'object') return null;
  if (sections.parse_failed) return null;
  const {
    headline,
    summary,
    experience,
    education,
    skills,
    certifications,
    languages,
    links,
  } = sections;
  return (
    <div className="cv-sections">
      {headline ? <div className="cv-section-headline"><strong>{headline}</strong></div> : null}
      {summary ? (
        <section className="cv-section">
          <h4>Summary</h4>
          <p>{summary}</p>
        </section>
      ) : null}
      {Array.isArray(experience) && experience.length ? (
        <section className="cv-section">
          <h4>Experience</h4>
          <div className="cv-experience-list">
            {experience.map((entry, idx) => (
              <div key={`${entry?.company || ''}-${entry?.title || ''}-${idx}`} className="cv-experience-card">
                <div className="cv-experience-head">
                  <strong>{entry?.title || 'Role'}</strong>
                  {entry?.company ? <span> · {entry.company}</span> : null}
                </div>
                {(entry?.start || entry?.end) ? (
                  <div className="muted-xs">{entry?.start || ''}{entry?.start && entry?.end ? ' — ' : ''}{entry?.end || ''}</div>
                ) : null}
                {Array.isArray(entry?.bullets) && entry.bullets.length ? (
                  <ul>
                    {entry.bullets.map((bullet, bi) => (
                      <li key={bi}>{bullet}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      ) : null}
      {Array.isArray(education) && education.length ? (
        <section className="cv-section">
          <h4>Education</h4>
          <ul>
            {education.map((entry, idx) => (
              <li key={`${entry?.institution || ''}-${entry?.degree || ''}-${idx}`}>
                <strong>{entry?.degree || 'Education'}</strong>
                {entry?.institution ? `, ${entry.institution}` : ''}
                {(entry?.start || entry?.end) ? <span className="muted-xs"> ({entry?.start || ''}{entry?.start && entry?.end ? '–' : ''}{entry?.end || ''})</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {Array.isArray(skills) && skills.length ? (
        <section className="cv-section">
          <h4>Skills</h4>
          <div className="cv-chip-row">
            {skills.map((skill, idx) => <span key={`${skill}-${idx}`} className="cv-chip">{skill}</span>)}
          </div>
        </section>
      ) : null}
      {Array.isArray(certifications) && certifications.length ? (
        <section className="cv-section">
          <h4>Certifications</h4>
          <div className="cv-chip-row">
            {certifications.map((cert, idx) => <span key={`${cert}-${idx}`} className="cv-chip">{cert}</span>)}
          </div>
        </section>
      ) : null}
      {Array.isArray(languages) && languages.length ? (
        <section className="cv-section">
          <h4>Languages</h4>
          <div className="cv-chip-row">
            {languages.map((lang, idx) => <span key={`${lang}-${idx}`} className="cv-chip">{lang}</span>)}
          </div>
        </section>
      ) : null}
      {Array.isArray(links) && links.length ? (
        <section className="cv-section">
          <h4>Links</h4>
          <ul>
            {links.map((href, idx) => (
              <li key={`${href}-${idx}`}>
                <a href={href} target="_blank" rel="noopener noreferrer">{href}</a>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
};

const buildFallbackShareUrl = (applicationId, shareToken) => {
  const normalized = String(shareToken || '').trim();
  if (!normalized) return '';
  const appId = String(applicationId || 'candidate').trim();
  const path = `/c/${encodeURIComponent(appId)}?view=interview&k=${encodeURIComponent(normalized)}`;
  if (typeof window === 'undefined') return path;
  return `${window.location.origin}${path}`;
};

export const CandidateStandingReportPage = ({ onNavigate, NavComponent = null }) => {
  const { showToast } = useToast();
  const { applicationId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const rolesApi = 'roles' in apiClient ? apiClient.roles : null;
  const assessmentsApi = 'assessments' in apiClient ? apiClient.assessments : null;
  const candidatesApi = 'candidates' in apiClient ? apiClient.candidates : null;
  const organizationsApi = 'organizations' in apiClient ? apiClient.organizations : null;
  const teamApi = 'team' in apiClient ? apiClient.team : null;

  const [application, setApplication] = useState(null);
  const [completedAssessment, setCompletedAssessment] = useState(null);
  const [orgData, setOrgData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyAction, setBusyAction] = useState('');
  const [applicationEvents, setApplicationEvents] = useState([]);
  const [shareState, setShareState] = useState({
    url: '',
    token: '',
    createdAt: null,
    loading: false,
    error: '',
  });

  const routeApplicationKey = String(applicationId || '').trim();
  const sharedRouteToken = String(searchParams.get('k') || '').trim()
    || (routeApplicationKey.startsWith('shr_') ? routeApplicationKey : '');
  const numericApplicationId = Number(routeApplicationKey);
  const isInterviewView = searchParams.get('view') === 'interview';
  const requestedTab = searchParams.get('tab') || 'overview';
  const [activeTab, setActiveTab] = useState(
    REPORT_TAB_IDS.has(requestedTab) ? requestedTab : 'overview'
  );

  useEffect(() => {
    document.body.classList.toggle('interview-view', isInterviewView);
    return () => {
      document.body.classList.remove('interview-view');
    };
  }, [isInterviewView]);

  useEffect(() => {
    const nextTab = REPORT_TAB_IDS.has(requestedTab) ? requestedTab : 'overview';
    setActiveTab(isInterviewView && INTERNAL_TABS.has(nextTab) ? 'overview' : nextTab);
  }, [isInterviewView, requestedTab]);

  const activateTab = useCallback((tabId) => {
    const safeTab = isInterviewView && INTERNAL_TABS.has(tabId) ? 'overview' : tabId;
    setActiveTab(safeTab);
    const nextParams = new URLSearchParams(searchParams);
    if (safeTab === 'overview') {
      nextParams.delete('tab');
    } else {
      nextParams.set('tab', safeTab);
    }
    setSearchParams(nextParams, { replace: true });
  }, [isInterviewView, searchParams, setSearchParams]);

  const loadStandingReport = useCallback(async () => {
    if (routeApplicationKey === 'demo') {
      setApplication(AI_SHOWCASE_APPLICATION);
      setCompletedAssessment(AI_SHOWCASE_COMPLETED_ASSESSMENT);
      setOrgData(null);
      setApplicationEvents([]);
      setError('');
      setLoading(false);
      return;
    }

    const canLoadById = !sharedRouteToken && rolesApi?.getApplication && Number.isFinite(numericApplicationId);
    const canLoadByShare = Boolean(sharedRouteToken && rolesApi?.getApplicationByShareToken);
    if (!canLoadById && !canLoadByShare) {
      setApplication(null);
      setCompletedAssessment(null);
      setError('Candidate report unavailable.');
      setLoading(false);
      return;
    }

    setLoading(true);
    setError('');
    try {
      const appRes = sharedRouteToken
        ? await rolesApi.getApplicationByShareToken(sharedRouteToken)
        : await rolesApi.getApplication(numericApplicationId);
      const nextApplication = appRes?.data || null;
      setApplication(nextApplication);

      const assessmentId = resolveAssessmentId(nextApplication);
      const hasCompletedAssessment = Boolean(
        assessmentId
        && COMPLETED_ASSESSMENT_STATUSES.has(resolveAssessmentStatus(nextApplication))
      );
      const canUseInternalApis = !sharedRouteToken;

      const [assessmentRes, orgRes, eventsRes] = await Promise.all([
        canUseInternalApis && hasCompletedAssessment && assessmentsApi?.get
          ? assessmentsApi.get(Number(assessmentId))
          : Promise.resolve(null),
        canUseInternalApis && organizationsApi?.get
          ? organizationsApi.get()
          : Promise.resolve(null),
        canUseInternalApis && rolesApi?.listApplicationEvents && nextApplication?.id
          ? rolesApi.listApplicationEvents(nextApplication.id)
          : Promise.resolve(null),
      ]);

      setCompletedAssessment(assessmentRes?.data || null);
      setOrgData(orgRes?.data || null);
      setApplicationEvents(Array.isArray(eventsRes?.data) ? eventsRes.data : (eventsRes?.data?.items || []));
    } catch (err) {
      const message = getErrorMessage(err, 'Failed to load candidate report.');
      setApplication(null);
      setCompletedAssessment(null);
      setApplicationEvents([]);
      setError(message);
      showToast(message, 'error');
    } finally {
      setLoading(false);
    }
  }, [assessmentsApi, numericApplicationId, organizationsApi, rolesApi, routeApplicationKey, sharedRouteToken, showToast]);

  useEffect(() => {
    void loadStandingReport();
  }, [loadStandingReport]);

  useEffect(() => {
    if (!sharedRouteToken) return;
    const fallbackUrl = buildFallbackShareUrl(application?.id || routeApplicationKey, sharedRouteToken);
    setShareState((prev) => ({
      ...prev,
      token: prev.token || sharedRouteToken,
      url: !prev.url || prev.url.includes('/c/shr_') ? fallbackUrl : prev.url,
    }));
  }, [application?.id, routeApplicationKey, sharedRouteToken]);

  const reportModel = useMemo(() => (
    application ? buildStandingCandidateReportModel({
      application,
      completedAssessment,
      identity: {
        assessmentId: completedAssessment?.id || resolveAssessmentId(application),
        sectionLabel: 'Standing report',
        name: application?.candidate_name || application?.candidate_email || 'Candidate',
        email: application?.candidate_email || '',
        position: application?.candidate_position || '',
        roleName: application?.role_name || '',
        applicationStatus: application?.application_outcome || application?.status || '',
      },
    }) : null
  ), [application, completedAssessment]);

  const assessmentId = completedAssessment?.id || resolveAssessmentId(application);
  const canOpenAssessmentDetail = Boolean(completedAssessment?.id);
  const workableConnected = Boolean(orgData?.workable_connected);
  const workableSource = Boolean(application?.workable_sourced || application?.workable_score_raw != null || application?.workable_profile_url);
  const shareUrl = shareState.url || (sharedRouteToken ? buildFallbackShareUrl(application?.id || routeApplicationKey, sharedRouteToken) : '');
  const strengthItems = useMemo(() => (
    (reportModel?.dimensionEntries || [])
      .filter((item) => Number.isFinite(Number(item?.value)))
      .sort((a, b) => Number(b.value || 0) - Number(a.value || 0))
      .slice(0, 4)
  ), [reportModel?.dimensionEntries]);
  const riskItems = useMemo(() => {
    const concerns = Array.isArray(reportModel?.roleFitModel?.concerns) ? reportModel.roleFitModel.concerns : [];
    const requirementGap = reportModel?.roleFitModel?.firstRequirementGap;
    const items = [];
    if (reportModel?.probeTitle || reportModel?.probeDescription) {
      items.push({
        title: reportModel.probeTitle || 'Primary probe area',
        description: reportModel.probeDescription || 'Probe where the current evidence is still thin.',
      });
    }
    if (requirementGap?.requirement && !items.some((item) => item.title === requirementGap.requirement)) {
      items.push({
        title: requirementGap.requirement,
        description: requirementGap.impact || requirementGap.evidence || 'Validate this gap during the panel loop.',
      });
    }
    concerns.forEach((concern) => {
      if (!items.some((item) => item.title === concern)) {
        items.push({ title: concern, description: 'This surfaced in the standing report evidence and is worth pressure-testing live.' });
      }
    });
    return items.slice(0, 3);
  }, [reportModel]);
  const cvMatchDetails = resolveCvMatchDetails({
    application,
    completedAssessment,
    fallback: reportModel?.roleFitModel,
  });
  const matchedRequirements = useMemo(() => {
    const requirements = Array.isArray(cvMatchDetails?.requirements_assessment)
      ? cvMatchDetails.requirements_assessment
      : [];
    return requirements
      .filter((item) => String(item?.status || '').toLowerCase() === 'met')
      .slice(0, 4);
  }, [cvMatchDetails]);
  const missingRequirements = useMemo(() => {
    const requirements = Array.isArray(cvMatchDetails?.requirements_assessment)
      ? cvMatchDetails.requirements_assessment
      : [];
    return requirements
      .filter((item) => String(item?.status || '').toLowerCase() !== 'met')
      .slice(0, 4);
  }, [cvMatchDetails]);
  const interviewQuestions = useMemo(() => {
    const stageOne = [
      {
        question: `Walk me through the strongest evidence that ${application?.role_name || 'this role'} matches your recent work.`,
        listenFor: 'Specific examples tied to the CV and role requirements.',
        source: 'CV + job spec',
      },
      ...(riskItems.length ? riskItems.map((item) => ({
        question: `How would you de-risk ${item.title.toLowerCase()} before the next stage?`,
        listenFor: item.description,
        source: 'Taali signal',
      })) : []),
    ].slice(0, 4);
    const stageTwo = [
      ...(strengthItems.length ? strengthItems.map((item) => ({
        question: `Show us a project where ${item.label.toLowerCase()} mattered under real delivery pressure.`,
        listenFor: 'Evidence of judgment, tradeoffs, and ownership rather than generic tool use.',
        source: 'Assessment',
      })) : []),
      {
        question: 'Where did AI help, and where did you deliberately slow down or reject its suggestion?',
        listenFor: 'Clear boundaries around AI assistance, verification, and accountability.',
        source: 'Taali + Fireflies',
      },
    ].slice(0, 4);
    return { stageOne, stageTwo };
  }, [application?.role_name, riskItems, strengthItems]);
  const timelineItems = useMemo(() => {
    if (applicationEvents.length) {
      return applicationEvents.slice(0, 8).map((event) => ({
        title: String(event?.event_type || 'Activity').replace(/_/g, ' '),
        detail: event?.reason || event?.description || event?.metadata?.note || 'Candidate activity recorded.',
        when: event?.created_at,
      }));
    }
    return [
      {
        title: 'Application created',
        detail: `${application?.candidate_name || application?.candidate_email || 'Candidate'} entered the Taali workflow.`,
        when: application?.created_at,
      },
      {
        title: completedAssessment ? 'Assessment completed' : 'Assessment pending',
        detail: completedAssessment
          ? 'Technical assessment signal is available in the report.'
          : 'This standing report is currently anchored to CV and role-fit evidence.',
        when: completedAssessment?.completed_at || application?.updated_at,
      },
    ].filter((item) => item.when || item.detail);
  }, [application, applicationEvents, completedAssessment]);

  const loadShareLink = useCallback(async ({ force = false } = {}) => {
    if (!application?.id) return null;
    if (!force && shareUrl && shareState.createdAt) {
      return {
        share_url: shareUrl,
        share_token: shareState.token || sharedRouteToken,
        created_at: shareState.createdAt,
      };
    }
    if (!rolesApi?.getApplicationShareLink) {
      if (sharedRouteToken) {
        return {
          share_url: buildFallbackShareUrl(application?.id || routeApplicationKey, sharedRouteToken),
          share_token: sharedRouteToken,
          created_at: shareState.createdAt,
        };
      }
      throw new Error('Share link endpoint is unavailable.');
    }

    setShareState((prev) => ({ ...prev, loading: true, error: '' }));
    try {
      const res = await rolesApi.getApplicationShareLink(application.id);
      const payload = res?.data || {};
      const nextState = {
        url: payload.share_url || buildFallbackShareUrl(application.id, payload.share_token || sharedRouteToken),
        token: payload.share_token || sharedRouteToken,
        createdAt: payload.created_at || null,
        loading: false,
        error: '',
      };
      setShareState(nextState);
      return payload;
    } catch (err) {
      const message = getErrorMessage(err, 'Failed to create secure report link.');
      setShareState((prev) => ({ ...prev, loading: false, error: message }));
      throw err;
    }
  }, [application?.id, rolesApi, routeApplicationKey, shareState.createdAt, shareState.token, shareUrl, sharedRouteToken]);

  useEffect(() => {
    if (!application?.id || routeApplicationKey === 'demo' || sharedRouteToken || isInterviewView) return;
    void loadShareLink().catch(() => {});
  }, [application?.id, isInterviewView, loadShareLink, routeApplicationKey, sharedRouteToken]);

  const handleDownloadReport = async () => {
    if (!application) return;
    setBusyAction('report');
    try {
      const res = completedAssessment?.id
        ? await assessmentsApi?.downloadReport?.(completedAssessment.id)
        : await rolesApi?.downloadApplicationReport?.(application.id);
      if (!res) throw new Error('Report download is unavailable.');
      const blob = new Blob([res.data], {
        type: res?.headers?.['content-type'] || 'application/pdf',
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `${buildClientReportFilenameStem(
        application?.role_name,
        application?.candidate_name || application?.candidate_email
      )}.pdf`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to download report.'), 'error');
    } finally {
      setBusyAction('');
    }
  };

  const handleCopyLink = async () => {
    try {
      const payload = await loadShareLink({ force: !shareUrl });
      const nextShareUrl = payload?.share_url || shareUrl || buildFallbackShareUrl(application?.id || routeApplicationKey, payload?.share_token || sharedRouteToken);
      if (!nextShareUrl) throw new Error('Share link unavailable.');
      await navigator.clipboard.writeText(nextShareUrl);
      showToast('Secure report link copied.', 'success');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to copy report link.'), 'error');
    }
  };

  const handleEmailShare = async () => {
    try {
      const payload = await loadShareLink({ force: !shareUrl });
      const nextShareUrl = payload?.share_url || shareUrl || buildFallbackShareUrl(application?.id || routeApplicationKey, payload?.share_token || sharedRouteToken);
      if (!nextShareUrl) throw new Error('Share link unavailable.');

      let recipientEmails = [];
      if (teamApi?.list) {
        try {
          const teamRes = await teamApi.list();
          recipientEmails = Array.from(new Set(
            (Array.isArray(teamRes?.data) ? teamRes.data : [])
              .filter((member) => member?.is_active !== false && member?.is_email_verified !== false)
              .map((member) => String(member?.email || '').trim().toLowerCase())
              .filter(Boolean)
          ));
        } catch {
          recipientEmails = [];
        }
      }

      const subject = encodeURIComponent(`Standing report · ${application?.candidate_name || application?.candidate_email || 'Candidate'}`);
      const body = encodeURIComponent(
        `Interview-view candidate report link for review.\n\nThis read-only link shows the panel-safe Overview, Assessment, and Interview prep tabs.\n\n${nextShareUrl}`
      );
      const bcc = recipientEmails.length ? `&bcc=${encodeURIComponent(recipientEmails.join(','))}` : '';
      window.location.href = `mailto:?subject=${subject}${bcc}&body=${body}`;
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to prepare report email.'), 'error');
    }
  };

  const handlePostToWorkable = useCallback(async () => {
    if (!assessmentId || !assessmentsApi?.postToWorkable) {
      showToast('Workable posting is unavailable for this report.', 'error');
      return;
    }
    setBusyAction('workable');
    try {
      const res = await assessmentsApi.postToWorkable(assessmentId);
      const postedAt = res?.data?.posted_to_workable_at || new Date().toISOString();
      setCompletedAssessment((prev) => ({
        ...(prev || {}),
        posted_to_workable: true,
        posted_to_workable_at: postedAt,
      }));
      showToast(res?.data?.already_posted ? 'Already posted to Workable' : 'Posted to Workable', 'success');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to post to Workable.'), 'error');
    } finally {
      setBusyAction('');
    }
  }, [assessmentId, assessmentsApi, showToast]);

  const handleDownloadCandidateDoc = async (docType) => {
    const candidateId = application?.candidate_id || completedAssessment?.candidate_id || null;
    if (!candidateId || !candidatesApi?.downloadDocument) return;
    try {
      const res = await candidatesApi.downloadDocument(candidateId, docType);
      const blob = new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = docType === 'cv'
        ? (application?.cv_filename || completedAssessment?.candidate_cv_filename || 'candidate-cv')
        : (completedAssessment?.candidate_job_spec_filename || application?.role_job_spec_filename || 'job-spec');
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to download document.'), 'error');
    }
  };

  if (loading) {
    return (
      <div>
        {NavComponent && !isInterviewView ? <NavComponent currentPage="candidates" onNavigate={onNavigate} /> : null}
        <div className="page">
          <div className="flex min-h-[280px] items-center justify-center">
            <Spinner size={22} />
          </div>
        </div>
      </div>
    );
  }

  if (error || !application || !reportModel) {
    return (
      <div>
        {NavComponent && !isInterviewView ? <NavComponent currentPage="candidates" onNavigate={onNavigate} /> : null}
        <div className="page">
          <Panel className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
            {error || 'Candidate report unavailable.'}
          </Panel>
        </div>
      </div>
    );
  }

  return (
    <div>
      {NavComponent && !isInterviewView ? <NavComponent currentPage="candidates" onNavigate={onNavigate} /> : null}
      <div className="page">
        {isInterviewView ? (
          <div className="iv-banner">
            <Eye size={16} />
            <span><b>Interview view.</b> You are seeing the panel-safe version of this Taali report.</span>
          </div>
        ) : null}
        <button type="button" className="standing-back back" data-internal-only onClick={() => onNavigate('candidates')}>
          Back to candidates
        </button>
        <div className="kicker" style={{ marginBottom: '10px' }}>Candidate standing report</div>

        <div className="report-hero">
          <div className="meta">
            <span className="kicker">STANDING REPORT · APPLICATION #{application?.id || '—'}</span>
            <span className={`chip ${reportModel?.recommendation?.variant === 'success' ? 'green' : reportModel?.recommendation?.variant === 'warning' ? 'amber' : 'purple'}`}>
              {reportModel?.recommendation?.label || 'Pending review'}
            </span>
          </div>
          <h1>
            {application?.candidate_name || application?.candidate_email || 'Candidate'} — where they <em>stand</em> in the pipeline.
          </h1>
          <p className="lead">
            A role-anchored, shareable summary. Evidence-first: every major claim stays tied to recruiter-visible signals from the application, role fit, and completed assessment history.
          </p>
          <div className="report-hero-grid">
            <div className="c hi">
              <div className="k">Composite</div>
              <div className="v">{reportModel?.summaryModel?.taaliScore != null ? `${Math.round(reportModel.summaryModel.taaliScore)} / 100` : '—'}</div>
              <div className="d">{completedAssessment ? 'Assessment included' : 'Standing view only'}</div>
            </div>
            <div className="c hi">
              <div className="k">Role fit</div>
              <div className="v">{reportModel?.summaryModel?.roleFitScore != null ? `${Math.round(reportModel.summaryModel.roleFitScore)}%` : '—'}</div>
              <div className="d">{application?.role_name || application?.candidate_position || 'Role evidence'}</div>
            </div>
            <div className="c">
              <div className="k">Assessment</div>
              <div className="v">{reportModel?.summaryModel?.assessmentScore != null ? `${Math.round(reportModel.summaryModel.assessmentScore)}` : '—'}</div>
              <div className="d">{completedAssessment ? 'Completed signal present' : 'Pending completion'}</div>
            </div>
            <div className="c">
              <div className="k">Workable raw</div>
              <div className="v">{application?.workable_score_raw != null ? `${Math.round(application.workable_score_raw)}` : '—'}</div>
              <div className="d">{workableSource ? 'Synced candidate context' : 'Manual application'}</div>
            </div>
          </div>
        </div>

        <div className="share-bar" data-internal-only>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ width: 32, height: 32, borderRadius: 10, background: 'var(--purple-soft)', color: 'var(--purple)', display: 'grid', placeItems: 'center' }}>
              <Copy size={15} />
            </div>
            <div>
              <div style={{ fontWeight: 600, fontSize: '13.5px' }}>Shareable link</div>
              <div style={{ fontSize: 12, color: 'var(--mute)' }}>Read-only interview link · panel-safe tabs only</div>
            </div>
          </div>
          <Input
            readOnly
            aria-label="Shareable report link"
            value={shareUrl || (shareState.loading ? 'Generating secure link…' : 'Secure link unavailable')}
            className="link"
          />
          <div className="row">
            <button type="button" className="btn btn-outline btn-sm" onClick={handleCopyLink} disabled={shareState.loading || !application?.id}>
              Copy
            </button>
            <button type="button" className="btn btn-outline btn-sm" onClick={handleEmailShare} disabled={shareState.loading || !application?.id}>
              <Mail size={14} />
              Email to panel
            </button>
            {/* Download PDF removed — the shareable web link above is the canonical report. */}
          </div>
        </div>
        {shareState.error ? <p className="mt-3 text-xs text-[var(--taali-danger)]">{shareState.error}</p> : null}

        <div className="tabs report-tabs" role="tablist" aria-label="Candidate report sections">
          {REPORT_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={activeTab === tab.id ? 'active' : ''}
              data-internal-only={tab.internalOnly ? '' : undefined}
              role="tab"
              aria-selected={activeTab === tab.id}
              onClick={() => activateTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className={`pane ${activeTab === 'overview' ? 'active' : ''}`} data-p="overview">
        <div className="report-body">
          <div>
            <div className="report-card">
              <div className="kicker">Verdict</div>
              <h2 style={{ fontSize: '28px', margin: '10px 0 12px' }}>
                {reportModel?.recommendation?.label || 'Continue review'}. <em>With context.</em>
              </h2>
              <p style={{ fontSize: '15.5px', lineHeight: 1.6, color: 'var(--ink-2)', margin: '0 0 14px' }}>
                {reportModel?.recruiterSummaryText}
              </p>
              <p style={{ fontSize: '14.5px', lineHeight: 1.6, color: 'var(--mute)', margin: 0 }}>
                <b style={{ color: 'var(--ink-2)' }}>Watch-out.</b> {reportModel?.integritySummaryText}
              </p>
            </div>

            <div className="report-card">
              <h2>Top <em>strengths</em></h2>
              <p className="sub">Ranked by the strongest dimensions currently visible in this standing report.</p>
              {strengthItems.length ? strengthItems.map((item, index) => (
                <div key={item.key} className="rank-row">
                  <div className="rk">{String(index + 1).padStart(2, '0')}</div>
                  <div>
                    <div className="t">{item.label}</div>
                    <div className="s">
                      {index === 0 ? reportModel?.strongestSignalDescription : `Score signal remains strong in ${item.label.toLowerCase()} across the current evidence set.`}
                    </div>
                    {index === 0 ? (
                      <div className="evidence-block">
                        <div className="turn">Evidence</div>
                        {reportModel?.evidenceSections?.roleFit?.description || reportModel?.evidenceSections?.assessment?.description || 'Standing report evidence is attached directly to the linked recruiter and assessment records.'}
                      </div>
                    ) : null}
                  </div>
                  <div className="pct">{Math.round(Number(item.value || 0))} / 10</div>
                </div>
              )) : (
                <div className="rank-row">
                  <div className="rk">01</div>
                  <div>
                    <div className="t">{reportModel?.strongestSignalTitle || 'Signal building'}</div>
                    <div className="s">{reportModel?.strongestSignalDescription}</div>
                  </div>
                  <div className="pct">—</div>
                </div>
              )}
            </div>

            <div className="report-card">
              <h2>Risks to <em>probe</em></h2>
              <p className="sub">Use these in the panel loop so the decision stays evidence-based.</p>
              {riskItems.map((item, index) => (
                <div key={`${item.title}-${index}`} className="rank-row">
                  <div className="rk" style={{ color: 'var(--amber)' }}>{String(index + 1).padStart(2, '0')}</div>
                  <div>
                    <div className="t">{item.title}</div>
                    <div className="s">{item.description}</div>
                  </div>
                  <div className="pct amber">Probe</div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <div className="report-card">
              <h2>Signal <em>breakdown</em></h2>
              <p className="sub">Dimension-level scoring behind the standing recommendation.</p>
              {(reportModel?.dimensionEntries || []).map((item) => (
                <div key={item.key} className="dimension-row">
                  <div className="dimension-row-head">
                    <span className="dimension-name">{item.label}</span>
                    <span className="dimension-score">{Math.round(Number(item.value || 0))} / 10</span>
                  </div>
                  <div className="bar">
                    <i style={{ width: `${Math.max(0, Math.min(100, Number(item.value || 0) * 10))}%` }} />
                  </div>
                </div>
              ))}
            </div>

            <div className="report-card" data-internal-only>
              <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Quick links</div>
              <div className="mt-3 space-y-2">
                {canOpenAssessmentDetail ? (
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    className="w-full justify-between"
                    onClick={() => onNavigate('candidate-detail', { candidateDetailAssessmentId: assessmentId })}
                  >
                    Open assessment detail
                    <ExternalLink size={14} />
                  </Button>
                ) : null}
                {application?.workable_profile_url ? (
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    className="w-full justify-between"
                    onClick={() => window.open(application.workable_profile_url, '_blank', 'noopener,noreferrer')}
                  >
                    View on Workable
                    <ExternalLink size={14} />
                  </Button>
                ) : null}
              </div>
            </div>

            {workableConnected && workableSource ? (
              <div data-internal-only>
                <WorkableComparisonCard
                  workableRawScore={application?.workable_score_raw}
                  taaliScore={reportModel?.summaryModel?.taaliScore}
                  posted={Boolean(completedAssessment?.posted_to_workable)}
                  postedAt={completedAssessment?.posted_to_workable_at || null}
                  workableProfileUrl={application?.workable_profile_url || ''}
                  scorePrecedence={orgData?.workable_config?.score_precedence || 'workable_first'}
                  onPost={assessmentId && !completedAssessment?.posted_to_workable ? handlePostToWorkable : null}
                  posting={busyAction === 'workable'}
                />
              </div>
            ) : null}

            <div className="report-card">
              <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Standing report status</div>
              <div className="mt-2 text-sm text-[var(--taali-text)]">
                {completedAssessment
                  ? 'Assessment completed. This report now combines role-fit evidence with final assessment signal.'
                  : 'Assessment not completed yet. This report stays anchored to CV, role-fit, and recruiter-facing evidence already on file.'}
              </div>
            </div>
          </div>
        </div>
        </div>

        <div className={`pane ${activeTab === 'assessment' ? 'active' : ''}`} data-p="assessment">
          <div className="two-col">
            <div className="panel">
              <h2>Scored <em>dimensions</em></h2>
              <p className="sub">The assessment read separates delivery from how the candidate worked with AI.</p>
              {(reportModel?.dimensionEntries || []).map((item) => (
                <div key={item.key} className="dim">
                  <div className="dim-row">
                    <span className="dim-name">{item.label}</span>
                    <span className="dim-score">{Math.round(Number(item.value || 0))} / 10</span>
                  </div>
                  <div className="bar">
                    <i style={{ width: `${Math.max(0, Math.min(100, Number(item.value || 0) * 10))}%` }} />
                  </div>
                  <p className="dim-note">
                    {item.description || `Signal from the completed work sample and AI-collaboration trace.`}
                  </p>
                </div>
              ))}
            </div>
            <div className="panel">
              <h2>Live <em>evidence</em></h2>
              <p className="sub">Panel-safe highlights tied to the work sample, not generic screening copy.</p>
              <div className="evi">
                {[
                  reportModel?.evidenceSections?.assessment,
                  reportModel?.evidenceSections?.roleFit,
                  reportModel?.evidenceSections?.integrity,
                ].filter(Boolean).map((item, index) => (
                  <div key={`${item.title || 'evidence'}-${index}`} className="ev">
                    <div className="ico">{index + 1}</div>
                    <div>
                      <h4>{item.title || 'Evidence'}</h4>
                      <p>{item.description || 'Evidence is attached to the candidate report.'}</p>
                      <div className="tag">{item.label || 'Taali signal'}</div>
                    </div>
                  </div>
                ))}
              </div>
              {workableConnected && workableSource && assessmentId ? (
                <div className="wk-push" data-internal-only>
                  <div className="lg">W</div>
                  <div>
                    <h4>{completedAssessment?.posted_to_workable ? 'Posted to Workable' : 'Push final report to Workable'}</h4>
                    <div className="meta">
                      <span>Taali {reportModel?.summaryModel?.taaliScore != null ? Math.round(reportModel.summaryModel.taaliScore) : '—'}</span>
                      <span>Workable {application?.workable_score_raw != null ? Math.round(application.workable_score_raw) : '—'}</span>
                    </div>
                  </div>
                  {completedAssessment?.posted_to_workable ? (
                    <span className="chip green">Posted</span>
                  ) : (
                    <button type="button" className="btn btn-outline btn-sm" onClick={handlePostToWorkable} disabled={busyAction === 'workable'}>
                      {busyAction === 'workable' ? 'Posting…' : 'Post'}
                    </button>
                  )}
                </div>
              ) : null}
            </div>
          </div>
        </div>

        <div className={`pane ${activeTab === 'cv' ? 'active' : ''}`} data-p="cv" data-internal-only>
          <CvViewer
            candidateId={application?.candidate_id || completedAssessment?.candidate_id || null}
            filename={application?.cv_filename || completedAssessment?.candidate_cv_filename || ''}
            uploadedAt={application?.cv_uploaded_at || null}
            candidatesApi={candidatesApi}
            parsedSections={application?.cv_sections || null}
          />
          <div className="match-head">
            <div className={`match-score ${(reportModel?.summaryModel?.roleFitScore || 0) >= 75 ? 'hi' : 'md'}`}>
              {reportModel?.summaryModel?.roleFitScore != null ? Math.round(reportModel.summaryModel.roleFitScore) : '—'}<sup>%</sup>
            </div>
            <div>
              <div className="sub">CV & role match</div>
              <div className="headline">{cvMatchDetails?.summary || 'Role-fit evidence is available from the candidate application and assessment context.'}</div>
            </div>
            {application?.workable_profile_url ? (
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() => window.open(application.workable_profile_url, '_blank', 'noopener,noreferrer')}
              >
                View on Workable
              </button>
            ) : null}
          </div>
          <div className="match-grid">
            <div className="match-col matched">
              <h4>Matched requirements <span className="chip-num">{matchedRequirements.length}</span></h4>
              <div className="match-list">
                {(matchedRequirements.length ? matchedRequirements : (cvMatchDetails?.matching_skills || []).map((skill) => ({ requirement: skill, evidence_quote: 'Skill matched in candidate profile.' }))).slice(0, 5).map((item, index) => {
                  const evidence = extractRequirementEvidence(item) || 'Matched evidence on file.';
                  return (
                    <div key={extractRequirementKey(item, index)} className="match-item">
                      <span className="tick">✓</span>
                      <span>{item.requirement || item}<div className="ev">{evidence}</div></span>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="match-col missing">
              <h4>Gaps to validate <span className="chip-num">{missingRequirements.length || (cvMatchDetails?.missing_skills || []).length}</span></h4>
              <div className="match-list">
                {(missingRequirements.length ? missingRequirements : (cvMatchDetails?.missing_skills || []).map((skill) => ({ requirement: skill, evidence_quote: 'Probe this in the interview loop.' }))).slice(0, 5).map((item, index) => {
                  const evidence = item?.impact || extractRequirementEvidence(item) || 'Probe this live.';
                  return (
                    <div key={extractRequirementKey(item, index)} className="match-item">
                      <span className="cross">×</span>
                      <span>{item.requirement || item}<div className="ev">{evidence}</div></span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>

        <div className={`pane ${activeTab === 'prep' ? 'active' : ''}`} data-p="prep">
          <div className="prep-stack">
            <div className="panel prep-panel">
              <h2>Stage 1 <em>recruiter screen</em></h2>
              <p className="sub">Use these to validate claims quickly before the deeper panel loop.</p>
              <div className="qgroup">
                {interviewQuestions.stageOne.map((item, index) => (
                  <div key={`${item.question}-${index}`} className="q-card">
                    <div className="q-num">QUESTION {String(index + 1).padStart(2, '0')} · {item.source}</div>
                    <div className="q-text">{item.question}</div>
                    <div className="q-meta">
                      <div><div className="label">Listen for</div><ul className="listen"><li>{item.listenFor}</li></ul></div>
                      <div><div className="label">Follow-up</div><ul className="concerning"><li>Ask for one concrete example, artifact, or tradeoff.</li></ul></div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="panel prep-panel">
              <h2>Stage 2 <em>technical panel</em></h2>
              <p className="sub">Designed for the hiring panel: probe how the candidate thinks with AI in the actual work.</p>
              <div className="qgroup">
                {interviewQuestions.stageTwo.map((item, index) => (
                  <div key={`${item.question}-${index}`} className="q-card">
                    <div className="q-num">QUESTION {String(index + 1).padStart(2, '0')} · {item.source}</div>
                    <div className="q-text">{item.question}</div>
                    <div className="q-meta">
                      <div><div className="label">Strong signal</div><ul className="listen"><li>{item.listenFor}</li></ul></div>
                      <div><div className="label">Concern</div><ul className="concerning"><li>Vague answers without links to code, prompts, or decisions.</li></ul></div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className={`pane ${activeTab === 'notes' ? 'active' : ''}`} data-p="notes" data-internal-only>
          <div className="two-col">
            <div className="panel">
              <h2>Team <em>notes</em></h2>
              <p className="sub">Internal recruiter and hiring-team context stays out of interviewer share mode.</p>
              <div className="note">
                <div className="who">Taali <span className="when">system note</span></div>
                <p>{reportModel?.recruiterSummaryText || 'Add recruiter notes after the panel review.'}</p>
              </div>
              <div className="note-input">
                <textarea placeholder="Add a private team note" disabled />
                <button type="button" className="btn btn-outline btn-sm" disabled>Save</button>
              </div>
            </div>
            <div className="panel">
              <h2>Activity <em>timeline</em></h2>
              <p className="sub">Application events from the role pipeline and assessment lifecycle.</p>
              <div className="act">
                {timelineItems.map((item, index) => (
                  <div key={`${item.title}-${index}`} className="row">
                    <div className="dot">{index + 1}</div>
                    <div>
                      <div className="t">{item.title}</div>
                      <div className="s">{item.detail}</div>
                    </div>
                    <div className="when">{item.when ? new Date(item.when).toLocaleDateString() : '—'}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CandidateStandingReportPage;
