import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { Copy, Download, ExternalLink, Eye, Sparkles } from 'lucide-react';

import * as apiClient from '../../shared/api';
import { viewShareLink } from '../../shared/api';
import { getCachedDocumentBlob } from '../../shared/api/documentCache';
import { useToast } from '../../context/ToastContext';
import {
  Button,
  Input,
  Panel,
  Select,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';
import { AgentHeader } from '../../shared/layout/AgentHeader';
import { CandidateDecisionStrip } from './CandidateDecisionStrip';
import { OverrideModal } from '../home/OverrideModal';
import { TeachModal } from '../home/TeachModal';
import { DECISION_ACTIONS } from '../../shared/decisions/decisionActions';
import { buildClientReportFilenameStem } from './clientReportUtils';
import { computeFluencyAxes } from '../../shared/assessment/fluencyRollup';
import { readFluency4d } from '../../shared/assessment/fluency4d';
import { RadarChart } from '../../shared/ui/RadarChart';
import { ScoreRing } from '../../shared/ui/ScoreRing';
import { ScoreProvenance } from './ScoreProvenance';
import { ErrorBoundary } from '../../shared/ui/ErrorBoundary';
import { buildStandingCandidateReportModel, COMPLETED_ASSESSMENT_STATUSES, mapAssessmentToCandidateView } from './assessmentViewModels';
// ApplicationDecisionPanel intentionally NOT imported — PR3 retired the decision
// recorder from the report body; the candidate's decision lives on the header
// strip (CandidateDecisionStrip). The component file is kept for reference.
import { AssessmentEvidencePanels, EvaluatePanel, InterviewTranscriptCapture } from './CandidateAssessmentDetailPanels';
import { CandidateSnapshotCard } from './CandidateSnapshotCard';
import {
  getErrorMessage,
  resolveCvMatchDetails,
  extractRequirementEvidence,
  extractRequirementKey,
} from './candidatesUiUtils';
import {
  AI_SHOWCASE_APPLICATION,
  AI_SHOWCASE_APPLICATION_EVENTS,
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

// Candidate file is the single canonical candidate page. Base tabs are
// always present; assessment-only tabs (requiresAssessment) reveal once a
// completed assessment is linked — replacing the separate /assessments/:id
// page. Visibility flags:
//   internalOnly  — recruiter app only; hidden on every share link.
//   recruiterOnly — recruiter app + recruiter share link; hidden from
//                   external client shares.
const REPORT_TABS = [
  { id: 'overview', label: 'Overview' },
  // PR3 (decision-surface unification): the standalone Evaluate tab is retired.
  // The candidate's DECISION lives on the report header (CandidateDecisionStrip,
  // PR2), and the Evaluate tab's assessment EVIDENCE (criteria ratings, manual
  // rubric, strengths/improvements, chat log) now renders inside this Assessment
  // pane via <EvaluatePanel hideDecision />.
  { id: 'assessment', label: 'Assessment', internalOnly: true, requiresAssessment: true },
  { id: 'cv', label: 'CV' },
  { id: 'prep', label: 'Interview prep', recruiterOnly: true },
  // "Notes & context" is the unified add-info surface: freeform notes, the
  // interview transcript capture, and ranking / link quick-adds.
  { id: 'notes', label: 'Notes & context', recruiterOnly: true },
];

const INTERNAL_TABS = new Set(REPORT_TABS.filter((tab) => tab.internalOnly).map((tab) => tab.id));
const CLIENT_HIDDEN_TABS = new Set(
  REPORT_TABS.filter((tab) => tab.internalOnly || tab.recruiterOnly).map((tab) => tab.id),
);
const REPORT_TAB_IDS = new Set(REPORT_TABS.map((tab) => tab.id));

// Stable empty-rubric reference so the Evaluate panel's draft-init effect
// (keyed on the rubric identity) doesn't reset recruiter input every render.
const EMPTY_RUBRIC = Object.freeze({});

const CV_TEXT_PREVIEW_LIMIT = 18000;

const inferCvMime = (filename) => {
  const ext = String(filename || '').split('.').pop().toLowerCase();
  if (ext === 'pdf') return 'application/pdf';
  if (ext === 'png') return 'image/png';
  if (ext === 'jpg' || ext === 'jpeg') return 'image/jpeg';
  if (ext === 'webp') return 'image/webp';
  if (ext === 'gif') return 'image/gif';
  if (ext === 'docx') return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
  if (ext === 'doc') return 'application/msword';
  if (ext === 'txt') return 'text/plain';
  return '';
};

const isCvImageMime = (mime) => String(mime || '').startsWith('image/');

const asCleanText = (value) => String(value || '').replace(/\s+/g, ' ').trim();

const sanitizeDownloadName = (value, fallback = 'candidate-cv') => {
  const cleaned = String(value || '').replace(/[\\/:*?"<>|]+/g, ' ').replace(/\s+/g, ' ').trim();
  return cleaned || fallback;
};

const asArray = (value) => (Array.isArray(value) ? value.filter(Boolean) : []);

const splitInlineList = (value) => String(value || '')
  .split(/[,;|•\n]/)
  .map((item) => asCleanText(item).replace(/^[-*]\s*/, ''))
  .filter((item) => item && item.length <= 80);

const CV_SECTION_ALIASES = [
  { key: 'summary', label: 'Profile', pattern: /^(profile|summary|about|objective|professional summary)$/i },
  { key: 'experience', label: 'Experience', pattern: /^(experience|work experience|employment|career history|professional experience)$/i },
  { key: 'education', label: 'Education', pattern: /^(education|academic background)$/i },
  { key: 'skills', label: 'Skills', pattern: /^(skills|technical skills|core skills|technologies|tools)$/i },
  { key: 'certifications', label: 'Certifications', pattern: /^(certifications|certificates|licenses)$/i },
  { key: 'languages', label: 'Languages', pattern: /^languages$/i },
  { key: 'links', label: 'Links', pattern: /^(links|portfolio|projects|publications|notable writing)$/i },
];

const detectCvSectionHeading = (line) => {
  const cleaned = asCleanText(line).replace(/:$/, '');
  if (!cleaned || cleaned.length > 44) return null;
  return CV_SECTION_ALIASES.find((item) => item.pattern.test(cleaned)) || null;
};

const deriveRawCvSections = (cvText) => {
  const text = String(cvText || '').slice(0, CV_TEXT_PREVIEW_LIMIT);
  const lines = text.replace(/\r/g, '\n').split('\n').map((line) => line.trim());
  const introLines = [];
  const sections = [];
  let current = null;

  lines.forEach((line) => {
    if (!line) {
      if (current && current.lines.length && current.lines[current.lines.length - 1] !== '') current.lines.push('');
      return;
    }
    const heading = detectCvSectionHeading(line);
    if (heading) {
      if (current && current.lines.length) sections.push(current);
      current = { key: heading.key, title: heading.label, lines: [] };
      return;
    }
    if (current) {
      current.lines.push(line);
    } else {
      introLines.push(line);
    }
  });

  if (current && current.lines.length) sections.push(current);
  if (!sections.length && introLines.length) {
    sections.push({ key: 'raw', title: 'CV text', lines: introLines.length > 4 ? introLines.slice(4) : introLines });
  }
  return { introLines, sections };
};

const normalizeExperienceEntries = (entries) => asArray(entries).map((entry, index) => {
  if (typeof entry === 'string') {
    return { key: `experience-${index}`, title: entry, company: '', start: '', end: '', bullets: [] };
  }
  const title = asCleanText(entry?.title || entry?.role || entry?.position) || 'Role';
  const company = asCleanText(entry?.company || entry?.employer || entry?.organization);
  const start = asCleanText(entry?.start || entry?.start_date);
  const end = asCleanText(entry?.end || entry?.end_date || (entry?.current ? 'Present' : ''));
  const summary = asCleanText(entry?.summary || entry?.description || entry?.notes);
  const bullets = asArray(entry?.bullets).map(asCleanText).filter(Boolean);
  if (summary && !bullets.length) bullets.push(summary);
  return {
    key: `${company}-${title}-${index}`,
    title,
    company,
    companyUnverified: Boolean(entry?.company_unverified),
    start,
    end,
    bullets,
  };
});

const normalizeEducationEntries = (entries) => asArray(entries).map((entry, index) => {
  if (typeof entry === 'string') {
    return { key: `education-${index}`, title: entry, detail: '', date: '', notes: '' };
  }
  const degree = asCleanText(entry?.degree);
  const field = asCleanText(entry?.field || entry?.field_of_study);
  const institution = asCleanText(entry?.institution || entry?.school);
  const title = [degree, field].filter(Boolean).join(' · ') || institution || 'Education';
  const detail = institution && institution !== title ? institution : '';
  const start = asCleanText(entry?.start || entry?.start_date);
  const end = asCleanText(entry?.end || entry?.end_date);
  return {
    key: `${institution}-${title}-${index}`,
    title,
    detail,
    date: [start, end].filter(Boolean).join(' - '),
    notes: asCleanText(entry?.notes),
  };
});

const normalizeCvSections = ({ parsedSections, cvText, application }) => {
  const parsed = parsedSections && typeof parsedSections === 'object' && !parsedSections.parse_failed
    ? parsedSections
    : {};
  const raw = deriveRawCvSections(cvText);
  const rawByKey = raw.sections.reduce((acc, section) => {
    if (!acc[section.key]) acc[section.key] = section;
    return acc;
  }, {});
  const rawSummary = rawByKey.summary?.lines?.join(' ') || raw.introLines.slice(1, 4).join(' ');
  const name = application?.candidate_name || application?.candidate_email || 'Candidate';
  const headline = asCleanText(parsed.headline || application?.candidate_headline || application?.candidate_position || application?.role_name);
  const summary = asCleanText(parsed.summary || application?.candidate_summary || rawSummary);
  // Prefer the LLM-structured skills (cv_sections) when present — they're
  // clean, discrete tags. Only fall back to splitting the raw CV text by
  // heading when there are no structured skills, because that split turns a
  // column-scrambled PDF's "skills" region into sentence fragments (the raw
  // text interleaves the summary paragraph with the skills sidebar).
  const parsedSkills = asArray(parsed.skills).map(asCleanText).filter(Boolean);
  const skills = parsedSkills.length
    ? [...parsedSkills, ...asArray(application?.candidate_skills).map(asCleanText)]
    : [
        ...asArray(application?.candidate_skills).map(asCleanText),
        ...splitInlineList(rawByKey.skills?.lines?.join('\n') || ''),
      ];
  const uniqueSkills = Array.from(new Set(skills.map((skill) => skill.trim()).filter(Boolean))).slice(0, 28);

  return {
    name,
    headline,
    summary,
    contact: [
      application?.candidate_email,
      application?.candidate_location,
      application?.candidate_phone,
    ].map(asCleanText).filter(Boolean),
    links: Array.from(new Set([
      ...asArray(parsed.links).map(asCleanText),
      ...asArray(application?.candidate_social_profiles).map((item) => asCleanText(item?.url || item?.name || item)),
      application?.candidate_profile_url,
    ].map(asCleanText).filter(Boolean))).slice(0, 6),
    experience: normalizeExperienceEntries(
      asArray(parsed.experience).length ? parsed.experience : application?.candidate_experience
    ),
    education: normalizeEducationEntries(
      asArray(parsed.education).length ? parsed.education : application?.candidate_education
    ),
    projects: asArray(parsed.projects)
      .map((entry, index) => ({
        key: `${asCleanText(entry?.name)}-${index}`,
        name: asCleanText(entry?.name),
        bullets: asArray(entry?.bullets).map(asCleanText).filter(Boolean),
      }))
      .filter((entry) => entry.name || entry.bullets.length),
    skills: uniqueSkills,
    certifications: asArray(parsed.certifications).map(asCleanText).filter(Boolean),
    languages: asArray(parsed.languages).map(asCleanText).filter(Boolean),
    rawSections: raw.sections.filter((section) => !['summary', 'skills'].includes(section.key)),
  };
};

const renderRawCvLines = (lines) => {
  const blocks = [];
  let current = [];
  const flush = () => {
    if (current.length) {
      blocks.push(current);
      current = [];
    }
  };
  asArray(lines).forEach((line) => {
    if (!line) {
      flush();
      return;
    }
    current.push(line);
  });
  flush();

  return blocks.map((block, index) => {
    const isList = block.every((line) => /^[-*•]/.test(line));
    if (isList) {
      return (
        <ul key={`raw-list-${index}`} className="cv-raw-list">
          {block.map((line, lineIndex) => (
            <li key={`${line}-${lineIndex}`}>{line.replace(/^[-*•]\s*/, '')}</li>
          ))}
        </ul>
      );
    }
    return <p key={`raw-p-${index}`} className="cv-profile">{block.join(' ')}</p>;
  });
};

const CvDocumentContent = ({ cvModel, matchingSkills }) => {
  const matchedSkillSet = new Set(asArray(matchingSkills).map((skill) => asCleanText(skill).toLowerCase()).filter(Boolean));

  return (
    <>
      <div className="cv-doc-meta"><span className="pg">CV</span></div>
      <h2 className="cv-name">{cvModel.name}</h2>
      {cvModel.headline ? <p className="cv-tagline">{cvModel.headline}</p> : null}
      {(cvModel.contact.length || cvModel.links.length) ? (
        <div className="cv-contact">
          {[...cvModel.contact, ...cvModel.links].map((item, index) => {
            const isLink = /^https?:\/\//i.test(item) || item.includes('linkedin.com') || item.includes('github.com');
            const href = item.includes('@') && !isLink ? `mailto:${item}` : (isLink ? item : '');
            return (
              <React.Fragment key={`${item}-${index}`}>
                {index ? <span className="sep">·</span> : null}
                {href ? <a href={href} target={isLink ? '_blank' : undefined} rel={isLink ? 'noopener noreferrer' : undefined}>{item}</a> : <span>{item}</span>}
              </React.Fragment>
            );
          })}
        </div>
      ) : null}

      {cvModel.summary ? (
        <section className="cv-section">
          <h4>Profile</h4>
          <p className="cv-profile">{cvModel.summary}</p>
        </section>
      ) : null}

      {cvModel.experience.length ? (
        <section className="cv-section">
          <h4>Experience</h4>
          {cvModel.experience.map((entry) => (
            <div key={entry.key} className="cv-role" data-evidence={entry.bullets.length ? '' : undefined}>
              <div className="cv-role-top">
                <div>
                  <span className="cv-role-title">{entry.title}</span>
                  {entry.company ? <span className="cv-role-co"> · {entry.company}</span> : null}
                  {entry.company && entry.companyUnverified ? (
                    <span
                      className="cv-role-unverified"
                      title="Employer name not found in the CV text — auto-extracted, treat as unverified."
                    >
                      Unverified
                    </span>
                  ) : null}
                </div>
                {(entry.start || entry.end) ? <span className="cv-role-date">{[entry.start, entry.end].filter(Boolean).join(' - ')}</span> : null}
              </div>
              {entry.bullets.length ? (
                <ul className="cv-raw-list">
                  {entry.bullets.map((bullet, index) => <li key={`${bullet}-${index}`}>{bullet}</li>)}
                </ul>
              ) : null}
            </div>
          ))}
        </section>
      ) : null}

      {cvModel.projects?.length ? (
        <section className="cv-section">
          <h4>Projects</h4>
          {cvModel.projects.map((entry) => (
            <div key={entry.key} className="cv-role" data-evidence={entry.bullets.length ? '' : undefined}>
              {entry.name ? (
                <div className="cv-role-top">
                  <div><span className="cv-role-title">{entry.name}</span></div>
                </div>
              ) : null}
              {entry.bullets.length ? (
                <ul className="cv-raw-list">
                  {entry.bullets.map((bullet, index) => <li key={`${bullet}-${index}`}>{bullet}</li>)}
                </ul>
              ) : null}
            </div>
          ))}
        </section>
      ) : null}

      {cvModel.education.length ? (
        <section className="cv-section">
          <h4>Education</h4>
          {cvModel.education.map((entry) => (
            <div key={entry.key} className="cv-edu">
              <div className="row">
                <span className="t">{entry.title}</span>
                {entry.date ? <span className="d">{entry.date}</span> : null}
              </div>
              {entry.detail ? <p>{entry.detail}</p> : null}
              {entry.notes ? <p>{entry.notes}</p> : null}
            </div>
          ))}
        </section>
      ) : null}

      {cvModel.skills.length ? (
        <section className="cv-section">
          <h4>Skills</h4>
          <div className="cv-skills">
            {cvModel.skills.map((skill, index) => {
              const matched = matchedSkillSet.has(asCleanText(skill).toLowerCase());
              return <span key={`${skill}-${index}`} className={`sk ${matched ? 'match' : ''}`}>{skill}</span>;
            })}
          </div>
        </section>
      ) : null}

      {cvModel.certifications.length ? (
        <section className="cv-section">
          <h4>Certifications</h4>
          <div className="cv-skills">
            {cvModel.certifications.map((item, index) => <span key={`${item}-${index}`} className="sk">{item}</span>)}
          </div>
        </section>
      ) : null}

      {cvModel.languages.length ? (
        <section className="cv-section">
          <h4>Languages</h4>
          <div className="cv-skills">
            {cvModel.languages.map((item, index) => <span key={`${item}-${index}`} className="sk">{item}</span>)}
          </div>
        </section>
      ) : null}

      {cvModel.rawSections.map((section, sectionIndex) => (
        <section key={`${section.key}-${sectionIndex}`} className="cv-section">
          <h4>{section.title}</h4>
          {renderRawCvLines(section.lines)}
        </section>
      ))}
    </>
  );
};

const toBulletList = (value) => {
  if (Array.isArray(value)) return value.filter(Boolean).map(asCleanText).filter(Boolean);
  const text = asCleanText(value);
  return text ? [text] : [];
};

// PrepQuestionCard — canvas cand-prep card layout:
//   QUESTION NN · {source}    (mono purple kicker)
//   {question}                 (display, weight 500)
//   LISTEN FOR (green mono)    |  CONCERNING IF (red mono)
//   {listenFor bullets}        |  {concern bullets}
const PrepQuestionCard = ({ item, number, listenLabel, concernLabel, fallbackConcern }) => {
  const listenItems = toBulletList(item?.listenFor);
  const concernItems = toBulletList(item?.redFlags || item?.followUp);
  const evidenceText = asCleanText(item?.evidence);
  const contextText = asCleanText(item?.context);
  return (
    <div className="mc-prep-card">
      <div className="mc-prep-card-kicker">
        QUESTION {String(number).padStart(2, '0')} · {item?.source || 'Standing report'}
      </div>
      <div className="mc-prep-card-question">{item?.question}</div>
      {contextText ? (
        <div className="mc-prep-card-context">{contextText}</div>
      ) : null}
      <div className="mc-prep-card-grid">
        <div>
          <div className="mc-prep-card-label is-listen">{listenLabel}</div>
          <ul className="mc-prep-card-list">
            {(listenItems.length ? listenItems : ['Specific examples tied to the candidate evidence.']).map((line, idx) => (
              <li key={`listen-${idx}`}>{line}</li>
            ))}
          </ul>
        </div>
        <div>
          <div className="mc-prep-card-label is-concern">{concernLabel}</div>
          <ul className="mc-prep-card-list">
            {(concernItems.length ? concernItems : [fallbackConcern]).map((line, idx) => (
              <li key={`concern-${idx}`}>{line}</li>
            ))}
          </ul>
        </div>
      </div>
      {evidenceText ? (
        <div className="mc-prep-card-evidence">
          <div className="mc-prep-card-evidence-label">ANCHOR IN</div>
          <div>{evidenceText}</div>
        </div>
      ) : null}
    </div>
  );
};

const CvDocumentViewer = ({
  applicationId,
  candidateId,
  filename,
  uploadedAt,
  rolesApi,
  candidatesApi,
  parsedSections,
  cvText,
  application,
  cvMatchDetails,
  autoPreview = false,
}) => {
  const [blobUrl, setBlobUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  const mime = inferCvMime(filename);
  const isImage = isCvImageMime(mime);
  const downloadName = sanitizeDownloadName(filename, 'candidate-cv');
  const cvModel = useMemo(() => normalizeCvSections({ parsedSections, cvText, application }), [application, cvText, parsedSections]);
  const hasTextFallback = Boolean(cvText || parsedSections || cvModel.summary || cvModel.rawSections.length);

  // Note: blobUrl ownership belongs to the module-level documentCache —
  // we deliberately do NOT revoke on unmount because the same URL is
  // reused across mounts (instant re-open) and across the candidate
  // list hover-prefetch path. The cache TTL handles cleanup.

  const ensureBlob = useCallback(async () => {
    if (blobUrl) return blobUrl;
    if (!applicationId && !candidateId) return '';
    setLoading(true);
    setErrorMessage('');
    try {
      const result = await getCachedDocumentBlob({ applicationId, candidateId, docType: 'cv' });
      if (!result?.url) return '';
      setBlobUrl(result.url);
      return result.url;
    } catch (err) {
      const status = err?.response?.status;
      const detail = err?.response?.data;
      // The download endpoints return JSON in detail, but axios may
      // surface it as ArrayBuffer because the request expects a blob.
      let parsedDetail = detail;
      if (detail instanceof ArrayBuffer) {
        try {
          parsedDetail = JSON.parse(new TextDecoder().decode(detail));
        } catch (_) {
          parsedDetail = null;
        }
      }
      const reason = parsedDetail?.detail?.reason || parsedDetail?.reason;
      const message = parsedDetail?.detail?.message || parsedDetail?.message;
      if (status === 410 || reason === 'file_storage_unavailable') {
        setErrorMessage(message || 'CV file expired from storage. Re-upload from Workable to restore it.');
      } else {
        setErrorMessage('Failed to load CV.');
      }
      return '';
    } finally {
      setLoading(false);
    }
  }, [applicationId, blobUrl, candidateId]);

  useEffect(() => {
    // Auto-fetch only for image-typed CVs (.png/.jpg/.webp), since the
    // <img> branch needs the blob to render. PDFs always render via
    // CvDocumentContent (parsed sections + cv_text) — no need to pull
    // the binary just to display, and skipping the fetch saves a
    // request and keeps the original-file Download button purely
    // user-initiated.
    if (!autoPreview || !filename || !isImage || blobUrl || loading || errorMessage) return;
    void ensureBlob();
  }, [autoPreview, blobUrl, isImage, ensureBlob, filename, loading, errorMessage]);

  const handleDownload = useCallback(async () => {
    if (!applicationId && !candidateId) return;
    setDownloading(true);
    setErrorMessage('');
    let downloadUrl = '';
    let createdLocalUrl = false;
    try {
      // Reuse the cached inline blob when present — saves a re-download
      // when the user has already previewed. Falls back to a fresh
      // attachment-disposition request otherwise.
      const cached = await getCachedDocumentBlob({ applicationId, candidateId, docType: 'cv' });
      if (cached?.url) {
        downloadUrl = cached.url;
      } else {
        const res = applicationId && rolesApi?.downloadApplicationDocument
          ? await rolesApi.downloadApplicationDocument(applicationId, 'cv', { params: { download: true } })
          : await candidatesApi?.downloadDocument?.(candidateId, 'cv');
        if (!res) return;
        const contentType = res?.headers?.['content-type'] || mime || 'application/octet-stream';
        const blob = res.data instanceof Blob ? res.data : new Blob([res.data], { type: contentType });
        downloadUrl = URL.createObjectURL(blob);
        createdLocalUrl = true;
      }
      const anchor = document.createElement('a');
      anchor.href = downloadUrl;
      anchor.download = downloadName;
      anchor.rel = 'noopener';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } catch (err) {
      const status = err?.response?.status;
      const detail = err?.response?.data;
      let parsedDetail = detail;
      if (detail instanceof ArrayBuffer) {
        try {
          parsedDetail = JSON.parse(new TextDecoder().decode(detail));
        } catch (_) {
          parsedDetail = null;
        }
      }
      const reason = parsedDetail?.detail?.reason || parsedDetail?.reason;
      const message = parsedDetail?.detail?.message || parsedDetail?.message;
      if (status === 410 || reason === 'file_storage_unavailable') {
        setErrorMessage(message || 'CV file expired from storage. Re-upload from Workable to restore it.');
      } else {
        setErrorMessage('Failed to download CV.');
      }
    } finally {
      // Only revoke URLs we created locally; cached URLs are managed by
      // documentCache so other components (and re-opens) keep working.
      if (createdLocalUrl && downloadUrl) {
        window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
      }
      setDownloading(false);
    }
  }, [applicationId, candidateId, candidatesApi, downloadName, mime, rolesApi]);

  if (!filename) {
    return (
      <div className="cv-doc empty">
        <div className="cv-doc-empty">
          <div>
            <div className="sub">Candidate CV</div>
            <div className="headline">No CV on file</div>
          </div>
          <p>Fetch the CV from Workable or upload one manually to show the document beside the match evidence.</p>
        </div>
      </div>
    );
  }

  // Render priority:
  //  1. Parsed-sections HTML (CvDocumentContent) whenever cv_text or
  //     cv_sections gives us something — same branded view for every
  //     candidate, no Chrome PDF-viewer chrome, no layout drift between
  //     PDF/.docx/.txt sources. The original file is always one click
  //     away via the Download button below.
  //  2. <img> for image-only CVs that have no text fallback (rare —
  //     scanned-photo resumes etc.).
  //  3. Empty state for everything else.
  const showImageFallback = blobUrl && isImage && !hasTextFallback;
  return (
    <article className="cv-doc">
      {hasTextFallback ? (
        <CvDocumentContent cvModel={cvModel} matchingSkills={cvMatchDetails?.matching_skills || []} />
      ) : showImageFallback ? (
        <img src={blobUrl} alt="Candidate CV" className="cv-viewer-image" />
      ) : isImage && loading ? (
        <div className="cv-doc-loading">
          <Spinner size={18} />
          <span>Loading CV preview...</span>
        </div>
      ) : (
        <div className="cv-doc-empty">
          <div>
            <div className="sub">Candidate CV fetched</div>
            <div className="headline">{filename}</div>
          </div>
          <p>This file type cannot be previewed inline yet. Download the original CV to inspect it.</p>
        </div>
      )}
      {errorMessage ? <div className="cv-viewer-error">{errorMessage}</div> : null}
      <div className="cv-doc-filebar">
        <span>{filename}{uploadedAt ? ` · updated ${new Date(uploadedAt).toLocaleDateString()}` : ''}</span>
        <button type="button" className="btn btn-outline btn-sm" onClick={handleDownload} disabled={downloading}>
          <Download size={13} />
          {downloading ? 'Downloading...' : 'Download original'}
        </button>
      </div>
    </article>
  );
};

// Status order for the unified requirement list: positives (met) first, then
// partial, then unclear, with gaps last.
const REQ_STATUS_RANK = { met: 0, partially_met: 1, unknown: 2, missing: 3 };

const reqStatusKey = (status) => {
  const value = String(status || '').toLowerCase();
  if (value === 'met') return 'met';
  if (value === 'partially_met') return 'partially_met';
  if (value === 'unknown') return 'unknown';
  return 'missing';
};

// Purple-forward, not traffic-light: "met" reads as brand purple so a strong
// candidate isn't a wall of green ticks. Only true gaps go amber.
const REQ_STATUS_META = {
  met: { label: 'Met', dot: 'var(--purple)' },
  partially_met: { label: 'Partial', dot: 'color-mix(in oklab, var(--purple) 45%, var(--bg-2))' },
  missing: { label: 'Gap', dot: 'var(--amber)' },
  unknown: { label: 'Unclear', dot: 'var(--mute)' },
};

const CvMatchReview = ({
  application,
  cvMatchDetails,
  matchedRequirements,
  missingRequirements,
  onJumpToPrep,
}) => {
  // Build one list, scored requirements preferred. Fall back to raw skill
  // strings when the role hasn't been scored against criteria yet.
  const hasRequirements = Array.isArray(cvMatchDetails?.requirements_assessment)
    && cvMatchDetails.requirements_assessment.length > 0;
  const items = hasRequirements
    ? [...missingRequirements, ...matchedRequirements]
    : [
      ...asArray(cvMatchDetails?.missing_skills).map((skill) => ({
        requirement: skill, status: 'missing', evidence_quote: 'Probe this in the interview loop.',
      })),
      ...asArray(cvMatchDetails?.matching_skills).map((skill) => ({
        requirement: skill, status: 'met', evidence_quote: 'Skill matched in the candidate profile.',
      })),
    ];
  // Stable sort keeps the existing recruiter-first / priority order within a status.
  const ordered = [...items].sort(
    (a, b) => REQ_STATUS_RANK[reqStatusKey(a?.status)] - REQ_STATUS_RANK[reqStatusKey(b?.status)]
  );
  const counts = ordered.reduce((acc, item) => {
    const key = reqStatusKey(item?.status);
    if (key === 'met') acc.met += 1;
    else if (key === 'partially_met') acc.partial += 1;
    else acc.missing += 1;
    return acc;
  }, { met: 0, partial: 0, missing: 0 });
  const total = ordered.length;

  const scoredAt = application?.cv_match_scored_at || application?.updated_at || null;
  const roleName = application?.role_name || application?.candidate_position || 'target role';

  // Integrity & corroboration — the second readout beside the match score:
  // the pre-screen fraud check, the integrity layer and graph+GitHub
  // corroboration, summarised as a trust band + verbatim warnings. Never alters
  // the score; it tells the recruiter how much to trust the match.
  const integrity = application?.score_summary?.integrity || null;
  const integrityBand = String(integrity?.trust_band || 'high');
  const integrityBandMeta = {
    high: { label: 'High trust', color: 'var(--purple)' },
    medium: { label: 'Verify', color: 'var(--amber)' },
    low: { label: 'Verify before advancing', color: 'var(--amber)' },
  }[integrityBand] || { label: integrityBand, color: 'var(--muted)' };
  const integrityWarnings = Array.isArray(integrity?.warnings) ? integrity.warnings : [];

  return (
    <section className="cv-rail cv-match-summary cv-match-review" aria-label="CV match summary">
      {integrity ? (
        <div
          className="rail-card cvm-integrity"
          style={{ marginBottom: 12, borderLeft: `3px solid ${integrityBandMeta.color}` }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div className="mc-kicker">INTEGRITY &amp; CORROBORATION</div>
            <span
              style={{
                marginLeft: 'auto', fontSize: 11, fontWeight: 700, color: integrityBandMeta.color,
                border: `1px solid ${integrityBandMeta.color}`, borderRadius: 999, padding: '2px 10px',
              }}
            >
              {integrityBandMeta.label}{integrity.to_verify ? ` · ${integrity.to_verify} to verify` : ''}
            </span>
          </div>
          {integrityWarnings.length ? (
            <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
              {integrityWarnings.map((warning, index) => (
                <li key={`integrity-${index}`} style={{ fontSize: 13, margin: '3px 0' }}>{warning}</li>
              ))}
            </ul>
          ) : (
            <div className="meta" style={{ marginTop: 6 }}>No integrity concerns — claims corroborate the CV.</div>
          )}
        </div>
      ) : null}
      {total ? (
        <div className="rail-card cvm-body">
          <div className="cvm-head">
            <div className="mc-kicker">CV MATCH</div>
            <div className="meta" style={{ marginTop: 4 }}>
              vs <b>{roleName}</b>{scoredAt ? ` · Scored ${new Date(scoredAt).toLocaleDateString()}` : ''}
            </div>
          </div>
          <div className="cvm-coverage">
            <div className="cvm-bar" aria-hidden="true">
              {counts.met ? <span style={{ flex: counts.met, background: 'var(--purple)' }} /> : null}
              {counts.partial ? <span style={{ flex: counts.partial, background: REQ_STATUS_META.partially_met.dot }} /> : null}
              {counts.missing ? <span style={{ flex: counts.missing, background: 'var(--amber)' }} /> : null}
            </div>
            <div className="cvm-legend">
              <span><i style={{ background: 'var(--purple)' }} /><b>{counts.met}</b> met</span>
              <span><i style={{ background: REQ_STATUS_META.partially_met.dot }} /><b>{counts.partial}</b> partial</span>
              <span><i style={{ background: 'var(--amber)' }} /><b>{counts.missing}</b> {counts.missing === 1 ? 'gap' : 'gaps'}</span>
            </div>
          </div>

          <div className="cvm-list">
            {ordered.map((item, index) => {
              const key = reqStatusKey(item?.status);
              const meta = REQ_STATUS_META[key] || REQ_STATUS_META.missing;
              const evidence = item?.impact
                || extractRequirementEvidence(item)
                || item?.evidence_quote
                || (key === 'met' ? 'Matched evidence on file.' : 'Probe this live.');
              const isRecruiter = String(item?.requirement_id || '').startsWith('crit_');
              return (
                <div key={extractRequirementKey(item, index)} className={`cvm-row is-${key}`}>
                  <span className="cvm-status" data-s={key}>
                    <i style={{ background: meta.dot }} />
                    {meta.label}
                  </span>
                  <div className="cvm-req">
                    <div className="cvm-req-top">
                      <span className="cvm-req-name">{item.requirement || item}</span>
                      {isRecruiter ? <span className="cvm-tag">Recruiter</span> : null}
                    </div>
                    <span className="cvm-ev">{evidence}</span>
                  </div>
                </div>
              );
            })}
          </div>

          <button type="button" className="rail-jump" onClick={onJumpToPrep}>
            View interview prep →
          </button>
        </div>
      ) : (
        <div className="rail-card">
          <div className="rail-empty">No requirements have been scored against this role yet.</div>
        </div>
      )}
    </section>
  );
};

export const CandidateStandingReportPage = ({ onNavigate, NavComponent = null }) => {
  const { showToast } = useToast();
  // ``shareToken`` is set when the SPA is mounted via the public
  // ``/share/:shareToken`` route. ``applicationId`` is set on the
  // recruiter-side ``/c/:applicationId`` and ``/candidates/:applicationId``
  // routes. Exactly one is present at a time.
  const { applicationId, shareToken: routeShareToken } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const rolesApi = 'roles' in apiClient ? apiClient.roles : null;
  const assessmentsApi = 'assessments' in apiClient ? apiClient.assessments : null;
  const candidatesApi = 'candidates' in apiClient ? apiClient.candidates : null;
  const organizationsApi = 'organizations' in apiClient ? apiClient.organizations : null;

  const [application, setApplication] = useState(null);
  const [completedAssessment, setCompletedAssessment] = useState(null);
  const [orgData, setOrgData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyAction, setBusyAction] = useState('');
  // Tracks which share button is mid-mint so we can disable it + show a
  // "Copying…" label. '' when idle, 'recruiter' or 'client' when busy.
  const [sharingMode, setSharingMode] = useState('');
  const [applicationEvents, setApplicationEvents] = useState([]);
  // Notes & context tab — local note draft + a tick that lets us refetch
  // the events feed after a successful save without a full page reload.
  const [noteDraft, setNoteDraft] = useState('');
  const [savingNote, setSavingNote] = useState(false);
  // Per-candidate notes default to agent-visible — they're almost always
  // guidance the agent should weigh ("already interviewed — not suitable").
  // Untick for pure team chatter the agent shouldn't read.
  const [noteForAgent, setNoteForAgent] = useState(true);
  const [eventsRefetchTick, setEventsRefetchTick] = useState(0);
  // PR3 add-info quick-adds, stored via the same note endpoint with a `kind`:
  //   ranking — a 1–5 score + optional comment (kind: 'ranking')
  //   link    — a URL + optional label          (kind: 'link')
  // Both default to agent-visible alongside the freeform note box.
  const [rankingValue, setRankingValue] = useState('');
  const [rankingComment, setRankingComment] = useState('');
  const [savingRanking, setSavingRanking] = useState(false);
  const [linkUrl, setLinkUrl] = useState('');
  const [linkLabel, setLinkLabel] = useState('');
  const [savingLink, setSavingLink] = useState(false);
  // View mode received from the backend when loaded via /share/:token —
  // "client" (scrubbed external view) or "recruiter" (full report). Null
  // when not on a share route (recruiter is logged in and viewing /c/:id).
  const [shareViewMode, setShareViewMode] = useState(null);
  // PR2 (decision-surface unification): the candidate's own pending agent
  // decision, surfaced in the header strip with the SAME Approve / Override /
  // Teach controls as the home hub. Recruiter-view only (the fetch + render
  // are both gated on !isClientView && !isInterviewView below).
  const [agentDecision, setAgentDecision] = useState(null);
  const [decisionBusy, setDecisionBusy] = useState(false);
  // Modal targets — mirrors HomeNow's teachFor / alternativeFor. ``alternativeFor``
  // drives OverrideModal for both overrides AND the primary-advance confirm.
  const [teachFor, setTeachFor] = useState(null);
  const [alternativeFor, setAlternativeFor] = useState(null);

  const routeApplicationKey = String(applicationId || '').trim();
  const sharedRouteToken = String(routeShareToken || '').trim();
  const isShareRoute = Boolean(sharedRouteToken);
  const numericApplicationId = Number(routeApplicationKey);
  const isClientView = shareViewMode === 'client';
  // Any share-route recipient (client OR recruiter view) hides internal
  // recruiter-only controls like "Rescore" and "Share" actions.
  const isInterviewView = isShareRoute;
  const hiddenTabs = isClientView
    ? CLIENT_HIDDEN_TABS
    : (isInterviewView ? INTERNAL_TABS : new Set());
  const requestedTab = searchParams.get('tab') || 'overview';
  // Back-link source of truth is ?from. ?from=jobs/<id> → role pipeline;
  // anything else (including ?from=home or absent) → /home. Using
  // application.role_id here would always go to the job pipeline since
  // every application has a role, even when the user arrived from /home.
  const backFromRoleId = useMemo(() => {
    const match = (searchParams.get('from') || '').match(/^jobs\/(\d+)$/);
    return match ? Number(match[1]) : null;
  }, [searchParams]);
  const [activeTab, setActiveTab] = useState(
    REPORT_TAB_IDS.has(requestedTab) ? requestedTab : 'overview'
  );

  // Assessment-only tabs reveal once a completed assessment is linked.
  // `completedAssessment` is only fetched when the latest attempt is in a
  // completed status (see loadStandingReport), so this mirrors "appears on
  // completion" without an extra flag.
  const hasAssessmentDetail = Boolean(completedAssessment);
  const availableTabIds = useMemo(() => new Set(
    REPORT_TABS
      .filter((tab) => !hiddenTabs.has(tab.id) && (!tab.requiresAssessment || hasAssessmentDetail))
      .map((tab) => tab.id)
  ), [hiddenTabs, hasAssessmentDetail]);

  useEffect(() => {
    document.body.classList.toggle('interview-view', isInterviewView);
    return () => {
      document.body.classList.remove('interview-view');
    };
  }, [isInterviewView]);

  useEffect(() => {
    const nextTab = REPORT_TAB_IDS.has(requestedTab) ? requestedTab : 'overview';
    setActiveTab(availableTabIds.has(nextTab) ? nextTab : 'overview');
  }, [availableTabIds, requestedTab]);

  const activateTab = useCallback((tabId) => {
    const safeTab = availableTabIds.has(tabId) ? tabId : 'overview';
    setActiveTab(safeTab);
    const nextParams = new URLSearchParams(searchParams);
    if (safeTab === 'overview') {
      nextParams.delete('tab');
    } else {
      nextParams.set('tab', safeTab);
    }
    setSearchParams(nextParams, { replace: true });
  }, [availableTabIds, searchParams, setSearchParams]);

  const loadStandingReport = useCallback(async () => {
    if (routeApplicationKey === 'demo') {
      setApplication(AI_SHOWCASE_APPLICATION);
      setCompletedAssessment(AI_SHOWCASE_COMPLETED_ASSESSMENT);
      setOrgData(null);
      setApplicationEvents(AI_SHOWCASE_APPLICATION_EVENTS);
      setShareViewMode(null);
      setError('');
      setLoading(false);
      return;
    }

    const canLoadById = !isShareRoute && rolesApi?.getApplication && Number.isFinite(numericApplicationId);
    const canLoadByShare = Boolean(isShareRoute && sharedRouteToken);
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
      let nextApplication = null;
      if (isShareRoute) {
        // /share/:token unauth flow — backend returns the full
        // application payload (client-safe scrubbed when mode=client)
        // plus the view mode. One round-trip, no separate fetch.
        const shareRes = await viewShareLink(sharedRouteToken);
        const payload = shareRes?.data || {};
        nextApplication = payload.application || null;
        setShareViewMode(payload.view === 'client' ? 'client' : 'recruiter');
      } else {
        const appRes = await rolesApi.getApplication(numericApplicationId, { params: { include_cv_text: true } });
        nextApplication = appRes?.data || null;
        setShareViewMode(null);
      }
      setApplication(nextApplication);

      const assessmentId = resolveAssessmentId(nextApplication);
      const hasCompletedAssessment = Boolean(
        assessmentId
        && COMPLETED_ASSESSMENT_STATUSES.has(resolveAssessmentStatus(nextApplication))
      );
      const canUseInternalApis = !isShareRoute;

      const [assessmentRes, orgRes, eventsRes, decisionRes] = await Promise.all([
        canUseInternalApis && hasCompletedAssessment && assessmentsApi?.get
          ? assessmentsApi.get(Number(assessmentId))
          : Promise.resolve(null),
        canUseInternalApis && organizationsApi?.get
          ? organizationsApi.get()
          : Promise.resolve(null),
        canUseInternalApis && rolesApi?.listApplicationEvents && nextApplication?.id
          ? rolesApi.listApplicationEvents(nextApplication.id)
          : Promise.resolve(null),
        // The candidate's own pending agent decision for the header strip.
        // Recruiter-view only (canUseInternalApis ⇒ non-share route). A
        // failure here must not blank the report, so swallow it to null.
        canUseInternalApis && apiClient.agent?.listDecisions && nextApplication?.id
          ? apiClient.agent
              .listDecisions({ application_id: nextApplication.id, status: 'pending', limit: 1 })
              .catch(() => null)
          : Promise.resolve(null),
      ]);

      setCompletedAssessment(assessmentRes?.data || null);
      setOrgData(orgRes?.data || null);
      setAgentDecision(Array.isArray(decisionRes?.data) ? (decisionRes.data[0] || null) : null);
      // Recruiter shares can't call the auth-only /events endpoint, so the
      // backend embeds the audit timeline in the share payload instead.
      const sharedEvents = Array.isArray(nextApplication?.application_events)
        ? nextApplication.application_events
        : [];
      setApplicationEvents(
        Array.isArray(eventsRes?.data)
          ? eventsRes.data
          : (eventsRes?.data?.items || sharedEvents)
      );
    } catch (err) {
      const message = getErrorMessage(err, 'Failed to load candidate report.');
      setApplication(null);
      setCompletedAssessment(null);
      setApplicationEvents([]);
      setError(message);
      // Don't toast on share-route failures — the page is unauth and
      // the visible error message is the whole story. Toast was a
      // recruiter-side affordance.
      if (!isShareRoute) showToast(message, 'error');
    } finally {
      setLoading(false);
    }
  }, [assessmentsApi, isShareRoute, numericApplicationId, organizationsApi, rolesApi, routeApplicationKey, sharedRouteToken, showToast]);

  // Refetch JUST the candidate's pending decision (after an approve / override /
  // teach) without reloading the whole report. Recruiter-view only.
  const loadAgentDecision = useCallback(async () => {
    if (isShareRoute || !apiClient.agent?.listDecisions || !numericApplicationId) return;
    try {
      const res = await apiClient.agent.listDecisions({
        application_id: numericApplicationId,
        status: 'pending',
        limit: 1,
      });
      setAgentDecision(Array.isArray(res?.data) ? (res.data[0] || null) : null);
    } catch {
      // A refetch failure shouldn't surface — the strip just keeps its
      // last-known state until the next full report load reconciles it.
    }
  }, [isShareRoute, numericApplicationId]);

  // 409 decision_stale — same shape HomeNow keys its stale messaging on.
  const isDecisionStaleError = useCallback((err) => {
    const detail = err?.response?.data?.detail;
    const code = typeof detail === 'object' && detail !== null ? detail.code : detail;
    return err?.response?.status === 409 && code === 'decision_stale';
  }, []);

  // Approve — mirrors HomeNow.handleApprove. Decision types whose action spec
  // carries a ``primary`` (i.e. advance_to_interview) open OverrideModal in
  // approve mode so the recruiter picks the Workable stage; everything else
  // approves the recommendation directly. No optimistic queue mechanics here —
  // there's a single decision on this page, not a queue to advance through.
  const handleDecisionApprove = useCallback(async (decision) => {
    if (!decision) return;
    const spec = DECISION_ACTIONS[decision.decision_type];
    if (spec?.primary) {
      setAlternativeFor({ decision, alternative: spec.primary });
      return;
    }
    setDecisionBusy(true);
    try {
      await apiClient.agent.approveDecision(decision.id, {}, { force: Boolean(decision.is_stale) });
      showToast('Approved.', 'success');
      await Promise.all([loadAgentDecision(), loadStandingReport()]);
    } catch (err) {
      if (isDecisionStaleError(err)) {
        showToast("This decision's inputs changed — re-evaluate to refresh it.", 'warning');
      } else {
        showToast(getErrorMessage(err, "Couldn't approve this decision."), 'error');
      }
    } finally {
      setDecisionBusy(false);
    }
  }, [isDecisionStaleError, loadAgentDecision, loadStandingReport, showToast]);

  // Override — open OverrideModal for the chosen alternative (the POST happens
  // inside the modal once the recruiter fills in the required "why").
  const handleDecisionAlternative = useCallback((decision, alternative) => {
    setAlternativeFor({ decision, alternative });
  }, []);

  const handleDecisionSnooze = useCallback(async (decision) => {
    if (!decision) return;
    setDecisionBusy(true);
    try {
      await apiClient.agent.snoozeDecision(decision.id);
      showToast('Snoozed for 1h.', 'success');
      await loadAgentDecision();
    } catch (err) {
      showToast(getErrorMessage(err, 'Snooze failed'), 'error');
    } finally {
      setDecisionBusy(false);
    }
  }, [loadAgentDecision, showToast]);

  const handleDecisionReEvaluate = useCallback(async (decision) => {
    if (!decision) return;
    setDecisionBusy(true);
    try {
      await apiClient.agent.reEvaluateDecision(decision.id);
      showToast('Re-evaluating with fresh inputs…', 'success');
      await Promise.all([loadAgentDecision(), loadStandingReport()]);
    } catch (err) {
      showToast(getErrorMessage(err, 'Re-evaluate failed'), 'error');
    } finally {
      setDecisionBusy(false);
    }
  }, [loadAgentDecision, loadStandingReport, showToast]);

  useEffect(() => {
    void loadStandingReport();
    // `eventsRefetchTick` is bumped after a recruiter saves a note so the
    // standing report reloads with the new event in the timeline.
  }, [loadStandingReport, eventsRefetchTick]);

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
  // Mapped assessment view for the Assessment + Evaluate tabs (shared shape
  // with the legacy /assessments page). Memoized so the leaf components and
  // the Evaluate draft-init effect see a stable `candidate` reference.
  const candidateView = useMemo(
    () => mapAssessmentToCandidateView(completedAssessment),
    [completedAssessment]
  );
  const evaluationRubric = (completedAssessment?.evaluation_rubric && typeof completedAssessment.evaluation_rubric === 'object')
    ? completedAssessment.evaluation_rubric
    : EMPTY_RUBRIC;
  // Strengths and risks are now derived from the same
  // requirements_assessment data that drives the Matched / Missing
  // cards on the CV & match tab — so what shows on Overview matches
  // what shows on CV & match. Recruiter-added crit_* surfaces ahead of
  // JD-extracted jd_req_* (recruiter signal > scraped signal).
  const cvMatchDetails = resolveCvMatchDetails({
    application,
    completedAssessment,
    fallback: reportModel?.roleFitModel,
  });
  // A pre-screen reject is deterministic and is recorded on the application
  // (``pre_screen_recommendation`` / ``pre_screen_evidence``) the moment the
  // cheap Stage-1 gate runs — independent of the agent, and of whether the
  // expensive full cv_match score ever ran. Surface it here even when
  // ``cv_match_details`` is empty (the Stage-1-only path deliberately never
  // writes cv_match_*), so a screened-out candidate shows the verdict + reason
  // instead of a blank "No Hire / 0.0".
  const preScreenEvidence = (application?.pre_screen_evidence && typeof application.pre_screen_evidence === 'object')
    ? application.pre_screen_evidence
    : {};
  const hasFullScore = application?.cv_match_score != null;
  const preScreenDecision = String(
    cvMatchDetails?.pre_screen_decision
    || preScreenEvidence.decision
    || ''
  ).toLowerCase();
  const isPreScreenedOut = !hasFullScore && (
    preScreenDecision === 'no'
    || String(application?.pre_screen_recommendation || '').trim().toLowerCase() === 'below threshold'
  );
  // Field names that the API actually serializes (ApplicationResponse):
  // top-level ``pre_screen_score`` (populated for fully/filtered-scored rows),
  // else the genuine LLM score carried in ``pre_screen_evidence.llm_score_100``
  // (the Stage-1-only path — where cv_match_* is empty), else the cv_match copy.
  const preScreenScore = (
    application?.pre_screen_score
    ?? preScreenEvidence.llm_score_100
    ?? cvMatchDetails?.pre_screen_score_100
    ?? null
  );
  const preScreenReason = String(
    cvMatchDetails?.pre_screen_reason
    || preScreenEvidence.summary
    || ''
  ).trim();
  const handleRunFullEvaluation = useCallback(async () => {
    if (!application?.id || !rolesApi?.scoreSelected || !application?.role_id) return;
    setBusyAction('rescore');
    try {
      // ``bypassPreScreen`` is the whole point of this button: the candidate
      // is sitting here *because* the cheap pre-screen filtered them, so a
      // plain rescore would just re-filter on the same evidence. Force the
      // full v3 cv_match score past the gate.
      await rolesApi.scoreSelected(application.role_id, [application.id], { force: true, bypassPreScreen: true });
      showToast('Full CV evaluation queued. Refresh in a few seconds.', 'success');
      void loadStandingReport();
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to start full evaluation.'), 'error');
    } finally {
      setBusyAction('');
    }
  }, [application?.id, application?.role_id, loadStandingReport, rolesApi, showToast]);
  // Sort so recruiter-added criteria (id prefix ``crit_``) surface
  // ahead of JD-extracted ones (``jd_req_``), then by priority. Show
  // every requirement — silently truncating recruiter must-haves at 4
  // was hiding the user's own criteria from their own report.
  const PRIORITY_RANK = { must_have: 0, strong_preference: 1, nice_to_have: 2, constraint: 3 };
  const sortRequirements = (items) => [...items].sort((a, b) => {
    const aRecruiter = String(a?.requirement_id || '').startsWith('crit_') ? 0 : 1;
    const bRecruiter = String(b?.requirement_id || '').startsWith('crit_') ? 0 : 1;
    if (aRecruiter !== bRecruiter) return aRecruiter - bRecruiter;
    const aPri = PRIORITY_RANK[String(a?.priority || '').toLowerCase()] ?? 4;
    const bPri = PRIORITY_RANK[String(b?.priority || '').toLowerCase()] ?? 4;
    return aPri - bPri;
  });
  const matchedRequirements = useMemo(() => {
    const requirements = Array.isArray(cvMatchDetails?.requirements_assessment)
      ? cvMatchDetails.requirements_assessment
      : [];
    return sortRequirements(
      requirements.filter((item) => String(item?.status || '').toLowerCase() === 'met')
    );
  }, [cvMatchDetails]);
  const missingRequirements = useMemo(() => {
    const requirements = Array.isArray(cvMatchDetails?.requirements_assessment)
      ? cvMatchDetails.requirements_assessment
      : [];
    return sortRequirements(
      requirements.filter((item) => String(item?.status || '').toLowerCase() !== 'met')
    );
  }, [cvMatchDetails]);
  const strengthItems = useMemo(() => {
    const met = matchedRequirements.slice(0, 4).map((item, idx) => ({
      key: `strength-${item.requirement_id || idx}`,
      label: item.requirement || '',
      value: null,
      source: String(item.requirement_id || '').startsWith('crit_') ? 'recruiter' : 'jd',
      detail: item.impact || item.reasoning || '',
    })).filter((item) => item.label);
    if (met.length) return met;
    // Fallback when no requirements are scored yet (pre-scoring state).
    const highlights = Array.isArray(reportModel?.roleFitModel?.experienceHighlights)
      ? reportModel.roleFitModel.experienceHighlights
      : [];
    return highlights
      .map((label, idx) => ({
        key: `cv-highlight-${idx}`,
        label: String(label || '').trim(),
        value: null,
        source: 'cv_match',
      }))
      .filter((item) => item.label)
      .slice(0, 4);
  }, [matchedRequirements, reportModel?.roleFitModel?.experienceHighlights]);
  const riskItems = useMemo(() => {
    // Top non-met requirements (missing > partial > unknown), recruiter
    // criteria first. Mirrors the order the user sees on the Missing /
    // Partial / Unclear card.
    const STATUS_RANK = { missing: 0, partially_met: 1, unknown: 2 };
    const ranked = [...missingRequirements].sort((a, b) => {
      const aRecruiter = String(a?.requirement_id || '').startsWith('crit_') ? 0 : 1;
      const bRecruiter = String(b?.requirement_id || '').startsWith('crit_') ? 0 : 1;
      if (aRecruiter !== bRecruiter) return aRecruiter - bRecruiter;
      const aSt = STATUS_RANK[String(a?.status || '').toLowerCase()] ?? 3;
      const bSt = STATUS_RANK[String(b?.status || '').toLowerCase()] ?? 3;
      return aSt - bSt;
    });
    return ranked.slice(0, 3).map((item) => ({
      title: item.requirement,
      description: item.impact || item.reasoning || 'Validate this gap during the panel loop.',
    }));
  }, [missingRequirements]);
  const interviewQuestions = useMemo(() => {
    const override = application?.interview_prep;
    if (override && (Array.isArray(override.stageOne) || Array.isArray(override.stageTwo))) {
      return {
        stageOne: Array.isArray(override.stageOne) ? override.stageOne : [],
        stageTwo: Array.isArray(override.stageTwo) ? override.stageTwo : [],
      };
    }
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
  }, [application?.interview_prep, application?.role_name, riskItems, strengthItems]);
  const timelineItems = useMemo(() => {
    if (applicationEvents.length) {
      return applicationEvents.slice(0, 8).map((event) => {
        const type = String(event?.event_type || '').toLowerCase();
        let title;
        if (type === 'cv_scored') {
          const meta = event?.metadata || {};
          const score = Number(meta.role_fit_score);
          const rec = String(meta.recommendation || '').replace(/_/g, ' ').trim();
          const scoreLabel = Number.isFinite(score) ? `${Math.round(score)}%` : '—';
          title = `CV scored — ${rec ? `${rec} ` : ''}(${scoreLabel})`;
        } else {
          title = String(event?.event_type || 'Activity').replace(/_/g, ' ');
        }
        return {
          title,
          detail: event?.reason || event?.description || event?.metadata?.note || 'Candidate activity recorded.',
          when: event?.created_at,
        };
      });
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

  // Report PDF export removed per HANDOFF v2 §3 — share links replace PDFs
  // entirely; do not reintroduce a download path. All sharing now flows
  // through ShareModal → the share_links table → the public /share/:token
  // SPA route.

  // Save a recruiter note as a `recruiter_note` event on the application
  // timeline — works with or without a linked assessment (the legacy
  // assessment-timeline path dead-ended when none was linked). When
  // `noteForAgent` the note rides in the agent's get_application payload as
  // standing per-candidate guidance. We fall back to the assessment-note
  // endpoint only if there's no application id. After save we bump
  // eventsRefetchTick so the timeline picks up the new event.
  const handleSaveNote = useCallback(async () => {
    const note = noteDraft.trim();
    if (!note) return;
    const appId = application?.id;
    if (!appId && !(assessmentId && assessmentsApi?.addNote)) {
      showToast('Could not save the note — no candidate record is linked yet.', 'info');
      return;
    }
    setSavingNote(true);
    try {
      if (appId && rolesApi?.addApplicationNote) {
        await rolesApi.addApplicationNote(appId, note, noteForAgent);
      } else {
        await assessmentsApi.addNote(assessmentId, note);
      }
      setNoteDraft('');
      setEventsRefetchTick((prev) => prev + 1);
      showToast(
        noteForAgent ? 'Note saved — your hiring agent will see it.' : 'Note added to the timeline.',
        'success',
      );
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to add note.'), 'error');
    } finally {
      setSavingNote(false);
    }
  }, [application?.id, rolesApi, assessmentId, assessmentsApi, noteDraft, noteForAgent, showToast]);

  // Ranking quick-add — a 1–5 score + optional comment, stored as a `ranking`
  // note via the same endpoint (kind: 'ranking'). Requires an application id
  // (the structured-kind endpoint is application-scoped) and a chosen score.
  const handleSaveRanking = useCallback(async () => {
    const appId = application?.id;
    const score = Number(rankingValue);
    if (!appId || !rolesApi?.addApplicationNote) return;
    if (!Number.isFinite(score) || score < 1 || score > 5) {
      showToast('Pick a 1–5 ranking first.', 'info');
      return;
    }
    const comment = rankingComment.trim();
    setSavingRanking(true);
    try {
      // The note body doubles as the human-readable `reason`; the agent-facing
      // payload renders "Ranking: N/5 — …" from the structured metadata.
      await rolesApi.addApplicationNote(appId, comment || `Ranking ${score}/5`, noteForAgent, {
        kind: 'ranking',
        ranking: score,
      });
      setRankingValue('');
      setRankingComment('');
      setEventsRefetchTick((prev) => prev + 1);
      showToast('Ranking added.', 'success');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to add ranking.'), 'error');
    } finally {
      setSavingRanking(false);
    }
  }, [application?.id, rolesApi, rankingValue, rankingComment, noteForAgent, showToast]);

  // Link quick-add — a URL + optional label, stored as a `link` note
  // (kind: 'link'). The note body is the label (or URL) so it's readable in the
  // timeline; the structured url/label ride in metadata for the clickable render.
  const handleSaveLink = useCallback(async () => {
    const appId = application?.id;
    const url = linkUrl.trim();
    if (!appId || !rolesApi?.addApplicationNote) return;
    if (!url) {
      showToast('Enter a URL to add a link.', 'info');
      return;
    }
    const label = linkLabel.trim();
    setSavingLink(true);
    try {
      await rolesApi.addApplicationNote(appId, label || url, noteForAgent, {
        kind: 'link',
        link_url: url,
        link_label: label || undefined,
      });
      setLinkUrl('');
      setLinkLabel('');
      setEventsRefetchTick((prev) => prev + 1);
      showToast('Link added.', 'success');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to add link.'), 'error');
    } finally {
      setSavingLink(false);
    }
  }, [application?.id, rolesApi, linkUrl, linkLabel, noteForAgent, showToast]);

  // One-click share: mint a fresh 7-day share-link of the requested mode
  // and copy the URL to the clipboard. Replaces the previous ShareModal
  // (which still exposed expiry presets, revoke, and audit history) —
  // user feedback was "just click share internally / share with client
  // and have a link copied." If revoke / manage-links is needed later
  // the backend endpoints (POST/GET/DELETE share-links) are untouched.
  //
  // Mint and clipboard-copy are deliberately separate try/catch blocks:
  // if the link is minted but the clipboard write fails (permission
  // denied, non-secure context, no clipboard API), we still surface the
  // URL so the user can copy manually. Treating clipboard errors as
  // mint errors would cause repeated retries to spawn orphan active
  // links on the backend (one per click).
  const handleMintAndCopyShareLink = useCallback(async (mode, successMessage) => {
    if (!application?.id || !rolesApi?.createApplicationShareLink) return;
    setSharingMode(mode);
    let url = '';
    try {
      const res = await rolesApi.createApplicationShareLink(application.id, { mode, expiry: '7d' });
      const token = res?.data?.token;
      if (!token || typeof window === 'undefined') throw new Error('Share link unavailable.');
      url = `${window.location.origin}/share/${token}`;
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to create share link.'), 'error');
      setSharingMode('');
      return;
    }
    try {
      await navigator.clipboard.writeText(url);
      showToast(successMessage, 'success');
    } catch {
      // Clipboard API unavailable / blocked — surface the URL so the
      // user can copy it manually instead of silently throwing away a
      // minted link.
      showToast(`Link ready, copy failed: ${url}`, 'info');
    } finally {
      setSharingMode('');
    }
  }, [application?.id, rolesApi, showToast]);

  // Recruiter lifecycle actions migrated from the legacy /assessments page.
  // Rendered in the (recruiter-only) Assessment pane, so they never reach a
  // share route. `resend` doubles as the candidate CV-request trigger.
  const normalizedAssessmentStatus = String(
    completedAssessment?.status || resolveAssessmentStatus(application) || ''
  ).toLowerCase();
  const canResendInvite = Boolean(assessmentId)
    && (normalizedAssessmentStatus === 'pending' || normalizedAssessmentStatus === 'expired');
  const hasCvOnFile = Boolean(
    application?.cv_filename || completedAssessment?.candidate_cv_filename || application?.cv_uploaded_at
  );
  const canRequestCvUpload = Boolean(
    assessmentId && !hasCvOnFile && (application?.candidate_email || completedAssessment?.candidate_email)
  );

  const handleResendInvite = useCallback(async () => {
    if (!assessmentId || !assessmentsApi?.resend) return;
    setBusyAction('resend');
    try {
      await assessmentsApi.resend(assessmentId);
      showToast('Assessment invite resent.', 'success');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to resend invite.'), 'error');
    } finally {
      setBusyAction('');
    }
  }, [assessmentId, assessmentsApi, showToast]);

  const handleRequestCvUpload = useCallback(async () => {
    if (!assessmentId || !assessmentsApi?.resend) return;
    setBusyAction('request-cv');
    try {
      await assessmentsApi.resend(assessmentId);
      showToast('CV request sent. The candidate can upload from the assessment link.', 'success');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to send CV request.'), 'error');
    } finally {
      setBusyAction('');
    }
  }, [assessmentId, assessmentsApi, showToast]);

  const handleDeleteAssessment = useCallback(async () => {
    if (!assessmentId || !assessmentsApi?.remove) return;
    if (typeof window !== 'undefined'
      && !window.confirm('Delete this assessment? This cannot be undone.')) return;
    setBusyAction('delete');
    try {
      await assessmentsApi.remove(assessmentId);
      showToast('Assessment deleted.', 'success');
      onNavigate('jobs');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to delete assessment.'), 'error');
      setBusyAction('');
    }
  }, [assessmentId, assessmentsApi, showToast, onNavigate]);

  if (loading) {
    return (
      <div>
        {NavComponent && !isInterviewView ? <NavComponent currentPage="candidates" onNavigate={onNavigate} /> : null}
        <div className="page">
          <div className="flex min-h-[17.5rem] items-center justify-center">
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

  // Back-link destination prefers the explicit ?from tag, then falls back
  // to the candidate's own role. Many entry points (the role board, the
  // triage drawer's new-tab link, search, deep links, …) don't attach
  // ?from, and defaulting those to "home" sent recruiters who opened a
  // candidate from a job back to the Hub. The role fallback only kicks in
  // when there is no origin tag, so explicit ?from=home still wins.
  //   ?from=jobs/<id> → "Back to job: <role_name>"
  //   ?from=home       → "Back to home" (explicit Hub origin)
  //   (no from)        → "Back to job: <role_name>" via application.role_id
  const cameFromHome = (searchParams.get('from') || '').trim() === 'home';
  const backTargetRoleId = backFromRoleId
    ?? (cameFromHome ? null : (application?.role_id ?? null));
  const targetRoleName = application?.role_name || 'job';
  const candidateLabel = application?.candidate_name || application?.candidate_email || 'Candidate';
  const candidateInitials = (() => {
    const seed = String(candidateLabel).trim();
    if (!seed) return 'C';
    const letters = seed.split(/\s+/).filter(Boolean).map((w) => w[0]).join('');
    return letters.slice(0, 2).toUpperCase() || 'C';
  })();
  const metaParts = [
    application?.candidate_email,
    application?.candidate_location,
    application?.role_name,
    application?.pipeline_stage
      ? `Application: ${String(application.pipeline_stage).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())}`
      : null,
  ].filter(Boolean);

  const breadcrumbItems = !isInterviewView
    ? (backTargetRoleId != null
        ? [
            { label: 'Jobs', page: 'jobs' },
            { label: targetRoleName, page: 'job-pipeline', options: { roleId: backTargetRoleId } },
            { label: candidateLabel },
          ]
        : [
            { label: 'Home', page: 'home' },
            { label: candidateLabel },
          ])
    : null;

  return (
    <div>
      {NavComponent && !isInterviewView ? <NavComponent currentPage="candidates" onNavigate={onNavigate} /> : null}
      {!isInterviewView ? (
        <AgentHeader
          kicker="Candidate standing report"
          title={candidateLabel}
          period={false}
          subtitle={metaParts.length ? metaParts.join(' · ') : 'Candidate standing report'}
          breadcrumbs={breadcrumbItems}
          preTitle={(
            <div className="ah-cand-pre">
              <div className="ah-cand-avatar" aria-hidden="true">{candidateInitials}</div>
            </div>
          )}
          actions={!isClientView ? (
            <>
              {application?.workable_profile_url ? (
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => window.open(application.workable_profile_url, '_blank', 'noopener,noreferrer')}
                >
                  <ExternalLink size={13} />
                  Open in Workable
                </button>
              ) : null}
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() => handleMintAndCopyShareLink('recruiter', 'Internal share link copied (expires in 7 days).')}
                disabled={!application?.id || sharingMode === 'recruiter'}
              >
                <Copy size={13} />
                {sharingMode === 'recruiter' ? 'Copying…' : 'Share internally'}
              </button>
              <button
                type="button"
                className="btn btn-purple btn-sm"
                onClick={() => handleMintAndCopyShareLink('client', 'Client share link copied (expires in 7 days).')}
                disabled={!application?.id || sharingMode === 'client'}
              >
                {sharingMode === 'client' ? 'Copying…' : 'Share with client'}
              </button>
            </>
          ) : null}
        />
      ) : null}
      {/* Decision strip — the agent's recommendation for THIS candidate with
          the same Approve / Override / Teach controls as the home hub.
          Recruiter-view only (hidden on every share link). */}
      {!isClientView && !isInterviewView ? (
        <div className="page" style={{ paddingTop: 0, paddingBottom: 0 }}>
          <CandidateDecisionStrip
            decision={agentDecision}
            application={application}
            recommendation={reportModel?.recommendation}
            busy={decisionBusy}
            onApprove={handleDecisionApprove}
            onAlternative={handleDecisionAlternative}
            onReEvaluate={handleDecisionReEvaluate}
            onSnooze={handleDecisionSnooze}
            onTeach={(d) => setTeachFor(d)}
            onNavigate={onNavigate}
          />
        </div>
      ) : null}
      <div className="page">
        {isInterviewView ? (
          <div className="iv-banner">
            <Eye size={16} />
            {isClientView ? (
              <span><b>Client view.</b> External, client-safe summary — recruiter notes, scoring breakdown, and interview prep are hidden.</span>
            ) : (
              <span><b>Recruiter view.</b> Full internal report — includes recruiter notes, timeline, and interview prep. Don&apos;t share with candidates.</span>
            )}
          </div>
        ) : null}
        {isPreScreenedOut ? (
          <div
            data-internal-only
            style={{
              marginTop: '4px',
              marginBottom: '14px',
              padding: '12px 14px',
              borderRadius: '12px',
              background: 'var(--taali-surface-subtle, rgba(100,116,139,0.08))',
              border: '1px solid var(--taali-border, rgba(100,116,139,0.2))',
              display: 'flex',
              gap: '12px',
              alignItems: 'center',
              justifyContent: 'space-between',
              flexWrap: 'wrap',
            }}
          >
            <div style={{ fontSize: '13.5px', color: 'var(--ink-2)', lineHeight: 1.5, maxWidth: 600 }}>
              <strong>Filtered out by pre-screen{preScreenScore != null ? ` · ${Math.round(preScreenScore)}/100` : ''}.</strong>{' '}
              {preScreenReason || 'A cheap pre-screen decided this CV did not plausibly meet the role must-haves.'}
            </div>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={handleRunFullEvaluation}
              disabled={busyAction === 'rescore'}
            >
              {busyAction === 'rescore' ? 'Queuing…' : 'Run full evaluation'}
            </button>
          </div>
        ) : null}

        {isClientView && application?.client_share_summary ? (
          <div className="report-card" style={{ marginTop: 18, borderLeft: '4px solid var(--taali-accent, #4f46e5)' }}>
            <div className="kicker">Why we&apos;re sharing this candidate</div>
            <h2 style={{ fontSize: '20px', margin: '8px 0 6px' }}>
              {application.client_share_summary.verdict}
            </h2>
            <p style={{ fontSize: '14px', color: 'var(--ink-2)', margin: '0 0 12px' }}>
              {`Shared for ${application.client_share_summary.role}.`}
              {Number.isFinite(Number(application.client_share_summary.score_100))
                ? ` TAALI score: ${Math.round(Number(application.client_share_summary.score_100))}/100.`
                : ''}
            </p>
            {Array.isArray(application.client_share_summary.highlights)
              && application.client_share_summary.highlights.length > 0 ? (
                <ul style={{ paddingLeft: 18, margin: '0 0 8px', fontSize: '14px', lineHeight: 1.6 }}>
                  {application.client_share_summary.highlights.map((highlight, idx) => (
                    <li key={idx}>{highlight}</li>
                  ))}
                </ul>
              ) : null}
          </div>
        ) : null}

        <div className="tabs report-tabs" role="tablist" aria-label="Candidate report sections">
          {REPORT_TABS.filter((tab) => availableTabIds.has(tab.id)).map((tab) => (
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
        {/* HANDOFF v2 §5.1 / canvas cand-overview — Overview tab is:
            (1) hero band: ScoreRing | RECOMMENDATION + body | SIGNAL list,
            (2) two-up: STRONGEST SIGNAL · WORTH PROBING,
            (3) DIMENSION SCORES — six rolled-up bars (0–100),
            (4) four evidence cards: AI USAGE · CODE & GIT · TIMELINE · DOCUMENTS.
            All scores render as integer "nn / 100" per HANDOFF v2 §6. */}
        {(() => {
          const fluencyAxes = computeFluencyAxes(completedAssessment);
          // Anthropic AI-Fluency 4 Ds rollup (additive; null until a task's
          // rubric adopts the new lenses — see fluency4d.js / summarize_fluency_4d).
          const fluency4d = readFluency4d(completedAssessment);
          const taaliScore = reportModel?.summaryModel?.taaliScore;
          const roleFitScoreVal = reportModel?.summaryModel?.roleFitScore;
          const assessmentScore = reportModel?.summaryModel?.assessmentScore;
          const recommendationLabel = reportModel?.recommendation?.label || 'Continue review';
          const reqMet = matchedRequirements.length;
          const reqTotal = matchedRequirements.length + missingRequirements.length;

          // 6-dimension labels (long form, matches canvas DIMENSION SCORES)
          const DIM_LONG_LABELS = {
            sysdesign: 'Systems design',
            codecraft: 'Code craft',
            reasoning: 'Reasoning under pressure',
            aicollab: 'AI collaboration',
            release: 'Release safety',
            communication: 'Communication',
          };
          const dimensions = fluencyAxes
            ? fluencyAxes.map((axis) => ({
                key: axis.k,
                label: DIM_LONG_LABELS[axis.k] || axis.label,
                value: Math.round(Number(axis.v || 0)),
                hasSignal: axis.hasSignal,
              }))
            : [];

          return (
            <>
              {/* (0) At-a-glance snapshot strip — years exp, tech stack, recent roles.
                  Sits above the hero band so recruiters and external clients can
                  scan candidate basics in 3 seconds without scrolling the full CV. */}
              {reportModel?.candidateSnapshot ? (
                <div className="mb-3">
                  <CandidateSnapshotCard snapshot={reportModel.candidateSnapshot} variant="page" />
                </div>
              ) : null}

              {/* (1) Recommendation card — copy + highlighted signal rings */}
              <div className="mc-overview-hero">
                <div className="mc-overview-hero-body">
                  <div className="mc-kicker">RECOMMENDATION</div>
                  <div className="mc-overview-hero-recommendation">{recommendationLabel}</div>
                  <p className="mc-overview-hero-summary">
                    {reportModel?.recruiterSummaryText
                      || 'Recommendation copy will populate once role-fit and assessment evidence are scored.'}
                  </p>
                </div>
                <div className="mc-overview-hero-rings">
                  <ScoreRing score={Number(taaliScore) || 0} label="TAALI" size={120} />
                  <ScoreRing score={Number(roleFitScoreVal) || 0} label="ROLE FIT" size={120} />
                  <ScoreRing score={Number(assessmentScore) || 0} label="ASSESSMENT" size={120} />
                  {reqTotal ? (
                    <ScoreRing score={(reqMet / reqTotal) * 100} display={`${reqMet}/${reqTotal}`} label="REQUIREMENTS" size={120} />
                  ) : null}
                </div>
                <ScoreProvenance
                  provenance={application?.score_summary?.score_provenance}
                  className="mc-overview-hero-provenance"
                />
              </div>

              {/* (2) CV match review — full requirement breakdown, gaps first */}
              <CvMatchReview
                application={application}
                cvMatchDetails={cvMatchDetails}
                matchedRequirements={matchedRequirements}
                missingRequirements={missingRequirements}
                onJumpToPrep={() => activateTab('prep')}
              />

              {/* (3) Dimension scores — six bars */}
              {dimensions.length ? (
                <div className="mc-overview-dimensions">
                  <div className="mc-kicker">DIMENSION SCORES</div>
                  <div className="mc-overview-dimensions-grid">
                    {dimensions.map((dim) => (
                      <div key={dim.key} className="mc-overview-dim-row">
                        <span className="mc-overview-dim-label">{dim.label}</span>
                        <div className="mc-overview-dim-bar" aria-hidden="true">
                          <i style={{ width: `${Math.max(0, Math.min(100, dim.value))}%` }} />
                        </div>
                        <span className="mc-overview-dim-score">
                          {dim.hasSignal ? dim.value : '—'}
                          {dim.hasSignal ? <span className="mc-overview-dim-suffix">/100</span> : null}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="mc-overview-dimensions mc-overview-dimensions-empty">
                  <div className="mc-kicker">DIMENSION SCORES</div>
                  <p className="mc-overview-dim-empty">
                    Six-dimension breakdown (Systems design, Code craft, Reasoning under pressure, AI
                    collaboration, Release safety, Communication) appears once the candidate completes
                    the assessment.
                  </p>
                </div>
              )}

              {/* (3b) AI Fluency — Anthropic's 4 Ds (+ Deliverable). Additive
                  view derived from the rubric grades; renders only when the
                  assessment carries the fluency_4d rollup. Axes with no signal
                  yet (task hasn't adopted the lens) show "—". */}
              {fluency4d ? (
                <div className="mc-overview-dimensions">
                  <div className="mc-kicker">AI FLUENCY · ANTHROPIC 4 Ds</div>
                  <div className="mc-overview-dimensions-grid">
                    {fluency4d.map((axis) => (
                      <div key={axis.key} className="mc-overview-dim-row" title={axis.blurb}>
                        <span className="mc-overview-dim-label">{axis.label}</span>
                        <div className="mc-overview-dim-bar" aria-hidden="true">
                          <i style={{ width: `${axis.hasSignal ? Math.max(0, Math.min(100, Math.round(axis.score))) : 0}%` }} />
                        </div>
                        <span className="mc-overview-dim-score">
                          {axis.hasSignal ? Math.round(axis.score) : '—'}
                          {axis.hasSignal ? <span className="mc-overview-dim-suffix">/100</span> : null}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {/* (4) Evidence row — four cards */}
              <div className="mc-overview-evidence">
                {[
                  { kicker: 'AI USAGE', section: reportModel?.evidenceSections?.aiUsage },
                  { kicker: 'CODE & GIT', section: reportModel?.evidenceSections?.codeAndGit },
                  { kicker: 'TIMELINE', section: reportModel?.evidenceSections?.timeline },
                  { kicker: 'DOCUMENTS', section: reportModel?.evidenceSections?.documents },
                ].map(({ kicker, section }) => {
                  const headline = section?.items?.[0]
                    || section?.title
                    || 'Evidence pending';
                  const description = section?.description
                    || 'Evidence appears here once the candidate is scored.';
                  return (
                    <div key={kicker} className="mc-overview-evidence-card">
                      <div className="mc-kicker">{kicker}</div>
                      <div className="mc-overview-evidence-headline">{headline}</div>
                      <p className="mc-overview-evidence-body">{description}</p>
                    </div>
                  );
                })}
              </div>

              {/* Internal-only footer: Workable comparison + quick links + status.
                  Hidden on isClient external shared views. */}
              {!isClientView ? (
                <div className="mc-overview-footer" data-internal-only>
                  <div className="mc-overview-footer-row">
                    <div className="mc-overview-footer-status">
                      <div className="mc-kicker is-mute">STANDING REPORT STATUS</div>
                      <p>
                        {completedAssessment
                          ? 'Assessment completed. This report combines role-fit evidence with final assessment signal.'
                          : 'Assessment not completed yet. This report stays anchored to CV, role-fit, and recruiter-facing evidence already on file.'}
                      </p>
                    </div>
                    <div className="mc-overview-footer-links">
                      {canOpenAssessmentDetail ? (
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => onNavigate('candidate-detail', { candidateDetailAssessmentId: assessmentId })}
                        >
                          Open assessment detail
                          <ExternalLink size={13} />
                        </Button>
                      ) : null}
                      {application?.workable_profile_url ? (
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => window.open(application.workable_profile_url, '_blank', 'noopener,noreferrer')}
                        >
                          View on Workable
                          <ExternalLink size={13} />
                        </Button>
                      ) : null}
                    </div>
                  </div>
                </div>
              ) : null}
            </>
          );
        })()}
        </div>

        <div className={`pane ${activeTab === 'assessment' ? 'active' : ''}`} data-p="assessment">
          {assessmentId ? (
            <div className="report-recruiter-actions mb-3 flex flex-wrap gap-2" data-internal-only>
              {canResendInvite ? (
                <button type="button" className="btn btn-outline btn-sm" onClick={handleResendInvite} disabled={busyAction !== ''}>
                  {busyAction === 'resend' ? 'Resending…' : 'Resend invite'}
                </button>
              ) : null}
              {canRequestCvUpload ? (
                <button type="button" className="btn btn-outline btn-sm" onClick={handleRequestCvUpload} disabled={busyAction !== ''}>
                  {busyAction === 'request-cv' ? 'Sending…' : 'Request CV upload'}
                </button>
              ) : null}
              <button type="button" className="btn btn-outline btn-sm" onClick={handleDeleteAssessment} disabled={busyAction !== ''}>
                {busyAction === 'delete' ? 'Deleting…' : 'Delete assessment'}
              </button>
            </div>
          ) : null}
          {(reportModel?.dimensionEntries || []).length > 0 ? (() => {
            const dimCount = (reportModel?.dimensionEntries || []).length;
            const firstName = String(application?.candidate_name || '').trim().split(/\s+/)[0];
            const score = reportModel?.summaryModel?.assessmentScore;
            return (
              <div className="abar abar-on abar-block" data-internal-only>
                <span className="ab-spark"><Sparkles size={15} strokeWidth={2} /></span>
                <span className="ab-label">Agent assessed</span>
                <span className="ab-tick">
                  {firstName
                    ? `Scored ${dimCount} dimensions from ${firstName}’s work sample`
                    : `Scored ${dimCount} dimensions from the work sample`}
                </span>
                <span className="ab-assess">
                  <b>{score != null ? Math.round(score) : '—'}</b><span>/100</span>
                </span>
              </div>
            );
          })() : null}
          <div className="two-col">
            <div className={`panel${(reportModel?.dimensionEntries || []).length > 0 ? ' agent-scored' : ''}`}>
              <h2>Scored <em>dimensions</em></h2>
              <p className="sub">The assessment read separates delivery from how the candidate worked with AI.</p>
              {(reportModel?.dimensionEntries || []).map((item) => (
                <div key={item.key} className="dim">
                  <div className="dim-row">
                    <span className="dim-name">{item.label}</span>
                    <span className="dim-score">{Math.round(Number(item.value || 0) * 10)} / 100</span>
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
            </div>
          </div>

          {/* Full assessment evidence migrated from the legacy /assessments
              page: AI-usage analytics, code/git, and the prompt-by-prompt
              timeline. Recruiter-only (this pane is internalOnly). */}
          {candidateView ? (
            <ErrorBoundary
              fallback={
                <div className="mc-notes-empty">
                  Scoring is incomplete for this assessment, so the evidence can’t be rendered.
                  Try “Rescore” from the assessment, or refresh.
                </div>
              }
            >
              <AssessmentEvidencePanels candidate={candidateView} />
            </ErrorBoundary>
          ) : null}

          {/* Assessment-evaluation evidence migrated from the retired Evaluate
              tab (PR3): role-criteria ratings, the manual excellent/good/poor
              rubric, strengths / improvements, and the chat-log evidence. The
              DECISION recorder is intentionally dropped (`hideDecision`) — the
              candidate's decision lives on the header strip now. */}
          {candidateView ? (
            <ErrorBoundary
              fallback={
                <div className="mc-notes-empty">
                  This evaluation can’t be rendered — the assessment scoring may be incomplete.
                  Try “Rescore”, or refresh.
                </div>
              }
            >
              <section className="report-assessment-rubric mt-4" data-internal-only>
                <div className="mc-kicker">ASSESSMENT EVALUATION</div>
                <EvaluatePanel
                  candidate={candidateView}
                  evaluationRubric={evaluationRubric}
                  assessmentId={assessmentId}
                  assessmentsApi={assessmentsApi}
                  roleFitCriteria={reportModel?.roleFitModel?.requirementsAssessment || []}
                  recommendation={reportModel?.recommendation}
                  recruiterSummary={reportModel?.recruiterSummaryText || ''}
                  hideDecision
                />
              </section>
            </ErrorBoundary>
          ) : null}
        </div>

        <div className={`pane ${activeTab === 'cv' ? 'active' : ''}`} data-p="cv">
          <div className="cv-doc-actions">
            <span className="name">
              {(application?.candidate_name || application?.candidate_email || 'Candidate')} · CV
              {application?.cv_uploaded_at ? ` · uploaded ${new Date(application.cv_uploaded_at).toLocaleDateString()}` : ''}
            </span>
            {application?.workable_profile_url ? (
              <button
                type="button"
                className="btn btn-outline btn-sm"
                data-internal-only
                onClick={() => window.open(application.workable_profile_url, '_blank', 'noopener,noreferrer')}
              >
                View on Workable
              </button>
            ) : null}
          </div>
          <div className="cv-layout">
            <CvDocumentViewer
              applicationId={application?.id || null}
              candidateId={application?.candidate_id || completedAssessment?.candidate_id || null}
              filename={application?.cv_filename || completedAssessment?.candidate_cv_filename || ''}
              uploadedAt={application?.cv_uploaded_at || null}
              rolesApi={rolesApi}
              candidatesApi={candidatesApi}
              parsedSections={application?.cv_sections || null}
              cvText={application?.cv_text || ''}
              application={application}
              cvMatchDetails={cvMatchDetails}
              autoPreview={activeTab === 'cv'}
            />
          </div>
        </div>

        <div className={`pane ${activeTab === 'prep' ? 'active' : ''}`} data-p="prep">
          {/* HANDOFF v2 §5.1 / canvas cand-prep — Interview prep is:
              (1) purple-soft hero banner: READY FOR YOUR PANEL · {N} questions,
                  anchored in {candidate}'s actual evidence
              (2) STAGE 1 · RECRUITER SCREEN kicker + question cards
              (3) STAGE 2 · HIRING PANEL kicker + question cards
              Each card: mono kicker "QUESTION NN · {source}" + question +
              two-column LISTEN FOR (green) / CONCERNING IF (red). */}
          {(() => {
            const totalQs = (interviewQuestions.stageOne?.length || 0) + (interviewQuestions.stageTwo?.length || 0);
            const candidateFirstName = String(application?.candidate_name || '').trim().split(/\s+/)[0] || 'this candidate';
            return (
              <div className="mc-prep-hero">
                <div className="mc-kicker">READY FOR YOUR PANEL</div>
                <div className="mc-prep-hero-title">
                  {totalQs > 0
                    ? <>{totalQs} questions, anchored in {candidateFirstName}'s actual <em>evidence</em>.</>
                    : <>Interview prep <em>builds</em> after the candidate is scored.</>}
                </div>
                <p className="mc-prep-hero-body">
                  {totalQs > 0
                    ? 'Each question cites the moment in the assessment it came from. Listen-for and concerning-if are calibrated to your role rubric.'
                    : 'Once the assessment is scored, this tab populates with stage-1 screen and stage-2 panel questions tied to evidence.'}
                </p>
              </div>
            );
          })()}

          <div className="mc-prep-stage">
            <div className="mc-kicker">STAGE 1 · RECRUITER SCREEN</div>
            <div className="mc-prep-stage-grid">
              {interviewQuestions.stageOne.map((item, index) => (
                <PrepQuestionCard
                  key={`${item.question}-${index}`}
                  item={item}
                  number={index + 1}
                  listenLabel="LISTEN FOR"
                  concernLabel="CONCERNING IF"
                  fallbackConcern="Ask for one concrete example, artifact, or tradeoff."
                />
              ))}
            </div>
          </div>

          <div className="mc-prep-stage">
            <div className="mc-kicker">STAGE 2 · HIRING PANEL</div>
            <div className="mc-prep-stage-grid">
              {interviewQuestions.stageTwo.map((item, index) => (
                <PrepQuestionCard
                  key={`${item.question}-${index}`}
                  item={item}
                  number={index + 1}
                  listenLabel="LISTEN FOR"
                  concernLabel="CONCERNING IF"
                  fallbackConcern="Vague answers without links to code, prompts, or decisions."
                />
              ))}
            </div>
          </div>
          {/* Interview transcript capture moved to the "Notes & context" tab
              (PR3) — it's add-info, not prep reference material. */}
        </div>

        <div className={`pane ${activeTab === 'notes' ? 'active' : ''}`} data-p="notes" data-internal-only={isClientView ? '' : undefined}>
          {/* HANDOFF v2 §5.1 / canvas cand-notes — "Notes & context" is the
              unified add-info surface (PR3):
              (1) HIRING TEAM NOTES column — note cards (who · role · time + body),
                  the freeform note box + agent-visible toggle, the ranking and
                  link quick-adds, and the interview-transcript capture.
              (2) AUDIT TIMELINE column — vertical line + colored dots,
                  each event has TIME · title · description.
              We synthesize "hiring team notes" from `recruiter_note` events on
              the application timeline; freeform notes + the ranking/link
              quick-adds all save via rolesApi.addApplicationNote (a
              `recruiter_note` event, optionally carrying a `kind`) and bump
              eventsRefetchTick so the timeline reloads. */}
          {(() => {
            // Recruiter notes are persisted by POST /assessments/{id}/notes,
            // which appends `{event_type: "note", text, author, timestamp}`
            // to `assessment.timeline` (a JSON column). They are NOT
            // emitted to the application_events table. So we read both
            // sources: assessment.timeline first (real persisted notes)
            // and applicationEvents as a fallback for any future
            // recruiter_note event-type emissions.
            const timelineNotes = (() => {
              // Recruiter shares don't fetch the assessment (auth-only), so the
              // backend embeds the note-type timeline entries on the payload.
              const entries = Array.isArray(completedAssessment?.timeline)
                ? completedAssessment.timeline
                : (Array.isArray(application?.recruiter_notes_timeline)
                  ? application.recruiter_notes_timeline
                  : []);
              return entries
                .filter((entry) => {
                  const type = String(entry?.event_type || entry?.type || '').toLowerCase();
                  if (type !== 'note' && type !== 'recruiter_note') return false;
                  return Boolean((entry?.text || entry?.prompt || '').trim());
                })
                .map((entry, idx) => ({
                  key: `tl-note-${entry.timestamp || entry.time || idx}`,
                  who: entry?.author || 'Recruiter',
                  role: 'Hiring team',
                  time: entry?.timestamp || entry?.time,
                  body: entry?.text || entry?.prompt || '',
                }))
                .filter((note) => note.body && note.body.trim());
            })();
            const eventNotes = applicationEvents
              .filter((event) => {
                const type = String(event?.event_type || '').toLowerCase();
                return type === 'recruiter_note'
                  || type === 'note_added'
                  || (event?.metadata && typeof event.metadata.note === 'string' && event.metadata.note.trim());
              })
              .map((event) => {
                const meta = event?.metadata || {};
                const kind = String(meta.kind || 'note').toLowerCase();
                const linkUrlMeta = String(meta.link_url || '').trim();
                const linkLabelMeta = String(meta.link_label || '').trim();
                // A link note may have an empty comment — fall back to the
                // label, then the URL, so the card always shows something.
                const body = kind === 'link'
                  ? (String(meta.note || '').trim() || linkLabelMeta || linkUrlMeta)
                  : (meta.note || event?.reason || event?.description || '');
                return {
                  key: `evt-note-${event.id || event.created_at}`,
                  who: event?.actor_name || meta.actor_name || 'Recruiter',
                  role: event?.actor_role || meta.actor_role || 'Hiring team',
                  time: event?.created_at,
                  body,
                  kind,
                  ranking: meta.ranking != null ? Number(meta.ranking) : null,
                  linkUrl: linkUrlMeta,
                  linkLabel: linkLabelMeta,
                };
              })
              .filter((note) => note.body && note.body.trim());
            // Newest first across both sources.
            const recruiterNotes = [...timelineNotes, ...eventNotes].sort((a, b) => {
              const ta = a.time ? new Date(a.time).getTime() : 0;
              const tb = b.time ? new Date(b.time).getTime() : 0;
              return tb - ta;
            });

            const fmtRelative = (ts) => {
              if (!ts) return '';
              const diffMs = Date.now() - new Date(ts).getTime();
              if (Number.isNaN(diffMs)) return '';
              const diffMin = Math.round(diffMs / 60000);
              if (diffMin < 1) return 'just now';
              if (diffMin < 60) return `${diffMin}m ago`;
              const diffHr = Math.round(diffMin / 60);
              if (diffHr < 24) return `${diffHr}h ago`;
              const diffDay = Math.round(diffHr / 24);
              if (diffDay < 14) return `${diffDay}d ago`;
              return new Date(ts).toLocaleDateString();
            };

            const eventDotColor = (event) => {
              const type = String(event?.event_type || '').toLowerCase();
              if (type.includes('reject')) return 'var(--red, #dc2626)';
              if (type.includes('advance') || type.includes('approved')) return 'var(--green, #16a34a)';
              if (type.includes('assess')) return '#2563eb';
              if (type.includes('cv_scored') || type.includes('invite')) return 'var(--purple)';
              return 'var(--mute)';
            };

            // Synced-from-Workable surfaces, exposed on ApplicationDetailResponse.
            // These are read-only here: Workable comments + the activity log come
            // from the recruiter's Workable account, questionnaire answers are the
            // candidate's own LinkedIn/Workable-apply responses. The hiring-team
            // note box above stays Tali-internal (never posted back to Workable).
            const workableComments = Array.isArray(application?.workable_comments)
              ? application.workable_comments
              : [];
            const workableAnswers = Array.isArray(application?.workable_questionnaire_answers)
              ? application.workable_questionnaire_answers
              : [];
            const workableActivity = Array.isArray(application?.workable_activity_log)
              ? application.workable_activity_log
              : [];

            return (
              <div className="mc-notes-grid">
                <div className="mc-notes-col">
                  <div className="mc-kicker">HIRING TEAM NOTES</div>
                  {recruiterNotes.length === 0 ? (
                    <div className="mc-notes-empty">
                      {isInterviewView
                        ? 'No hiring team notes yet.'
                        : 'No notes yet. Drop a note below — tell the hiring agent what it should know (e.g. “already interviewed, not suitable”). It lands in the audit timeline too.'}
                    </div>
                  ) : (
                    recruiterNotes.map((note) => {
                      const isRanking = note.kind === 'ranking' && Number.isFinite(note.ranking);
                      const isLink = note.kind === 'link' && note.linkUrl;
                      return (
                        <div key={note.key} className="mc-notes-card" data-kind={note.kind || 'note'}>
                          <div className="mc-notes-card-head">
                            <span className="mc-notes-card-who">
                              {note.who}
                              <span className="mc-notes-card-role"> · {note.role}</span>
                            </span>
                            <span className="mc-notes-card-time">{fmtRelative(note.time)}</span>
                          </div>
                          <div className="mc-notes-card-body">
                            {isRanking ? (
                              <span
                                className="mc-notes-rank"
                                style={{ color: 'var(--purple)', fontWeight: 600, marginRight: 6 }}
                                title={`Ranked ${note.ranking} out of 5`}
                              >
                                ★ {note.ranking}/5
                              </span>
                            ) : null}
                            {isLink ? (
                              <a
                                href={note.linkUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="mc-notes-link"
                                style={{ color: 'var(--purple)', textDecoration: 'underline' }}
                              >
                                {note.linkLabel || note.body || note.linkUrl}
                              </a>
                            ) : (
                              // For ranking, the body is the optional comment;
                              // don't repeat it if it was only the auto label.
                              (isRanking && note.body === `Ranking ${note.ranking}/5`)
                                ? null
                                : note.body
                            )}
                          </div>
                        </div>
                      );
                    })
                  )}
                  {/* Adding notes hits an auth-only endpoint, so the input is
                      recruiter-app only — share recipients see notes read-only.
                      Notes save against the application, so they work with or
                      without a linked assessment. */}
                  {isInterviewView ? null : (() => {
                    const canAddNote = Boolean(application?.id || assessmentId);
                    return (
                    <div className="mc-notes-input">
                      <textarea
                        value={noteDraft}
                        onChange={(event) => setNoteDraft(event.target.value)}
                        placeholder={canAddNote
                          ? 'Add a note on this candidate — e.g. “already interviewed, not suitable” or “lacks the technical depth”…'
                          : 'Notes open once this candidate has an application record.'}
                        disabled={!canAddNote || savingNote}
                        rows={3}
                      />
                      <label className="mc-notes-agent-toggle">
                        <input
                          type="checkbox"
                          checked={noteForAgent}
                          onChange={(event) => setNoteForAgent(event.target.checked)}
                          disabled={!canAddNote || savingNote}
                        />
                        <span>Visible to the hiring agent — it’ll weigh this as standing guidance on this candidate.</span>
                      </label>
                      <div className="mc-notes-input-actions">
                        <button
                          type="button"
                          className="btn btn-purple btn-sm"
                          onClick={handleSaveNote}
                          disabled={!canAddNote || savingNote || !noteDraft.trim()}
                        >
                          {savingNote ? 'Adding…' : 'Add note'}
                        </button>
                      </div>
                    </div>
                    );
                  })()}

                  {/* Add-info quick-adds (PR3): a 1–5 ranking and an external
                      link, both stored via the note endpoint with a `kind` and
                      visible to the agent alongside freeform notes. These need
                      a real application id (the structured-kind endpoint is
                      application-scoped), so they're hidden on share routes and
                      when no application record exists. */}
                  {!isInterviewView && application?.id ? (
                    <div className="mc-notes-addinfo" style={{ marginTop: 14, display: 'grid', gap: 12 }}>
                      <div className="mc-notes-input">
                        <div className="mc-kicker" style={{ marginBottom: 6 }}>QUICK RANKING</div>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                          <Select
                            bare
                            triggerClassName="max-w-[120px]"
                            value={rankingValue}
                            onChange={(event) => setRankingValue(event.target.value)}
                            disabled={savingRanking}
                            aria-label="Ranking out of 5"
                          >
                            <option value="">★ Rank…</option>
                            <option value="1">★ 1/5</option>
                            <option value="2">★ 2/5</option>
                            <option value="3">★ 3/5</option>
                            <option value="4">★ 4/5</option>
                            <option value="5">★ 5/5</option>
                          </Select>
                          <input
                            type="text"
                            className="taali-input"
                            value={rankingComment}
                            onChange={(event) => setRankingComment(event.target.value)}
                            placeholder="Optional comment (why this ranking)…"
                            disabled={savingRanking}
                            style={{ flex: 1, minWidth: 180 }}
                          />
                        </div>
                        <div className="mc-notes-input-actions">
                          <button
                            type="button"
                            className="btn btn-outline btn-sm"
                            onClick={handleSaveRanking}
                            disabled={savingRanking || !rankingValue}
                          >
                            {savingRanking ? 'Adding…' : 'Add ranking'}
                          </button>
                        </div>
                      </div>

                      <div className="mc-notes-input">
                        <div className="mc-kicker" style={{ marginBottom: 6 }}>ADD A LINK</div>
                        <div style={{ display: 'grid', gap: 8 }}>
                          <input
                            type="url"
                            className="taali-input"
                            value={linkUrl}
                            onChange={(event) => setLinkUrl(event.target.value)}
                            placeholder="https://… (portfolio, GitHub, reference)"
                            disabled={savingLink}
                          />
                          <input
                            type="text"
                            className="taali-input"
                            value={linkLabel}
                            onChange={(event) => setLinkLabel(event.target.value)}
                            placeholder="Optional label (e.g. “Portfolio”)"
                            disabled={savingLink}
                          />
                        </div>
                        <div className="mc-notes-input-actions">
                          <button
                            type="button"
                            className="btn btn-outline btn-sm"
                            onClick={handleSaveLink}
                            disabled={savingLink || !linkUrl.trim()}
                          >
                            {savingLink ? 'Adding…' : 'Add link'}
                          </button>
                        </div>
                      </div>
                    </div>
                  ) : null}

                  {/* Interview transcript capture (Fireflies link / manual
                      paste), moved here from the Prep tab (PR3) — it's add-info,
                      not prep reference. Recruiter-only: it calls authed APIs, so
                      it's not mounted on unauth share routes. */}
                  {!isShareRoute ? (
                    <div className="mc-notes-input" data-internal-only style={{ marginTop: 14 }}>
                      <div className="mc-kicker" style={{ marginBottom: 6 }}>INTERVIEW TRANSCRIPT</div>
                      <InterviewTranscriptCapture
                        application={application}
                        firefliesConnected={Boolean(orgData?.fireflies_config?.connected)}
                        rolesApi={rolesApi}
                        onRefresh={loadStandingReport}
                      />
                    </div>
                  ) : null}

                  {workableComments.length > 0 ? (
                    <>
                      <div className="mc-kicker" style={{ marginTop: 18 }}>WORKABLE COMMENTS</div>
                      {workableComments.map((comment, idx) => {
                        const body = String(comment?.body || '').trim();
                        if (!body) return null;
                        const author = String(comment?.author || '').trim() || 'Workable';
                        return (
                          <div key={`wk-comment-${comment?.created_at || idx}`} className="mc-notes-card">
                            <div className="mc-notes-card-head">
                              <span className="mc-notes-card-who">
                                {author}
                                <span className="mc-notes-card-role"> · Workable</span>
                              </span>
                              <span className="mc-notes-card-time">{fmtRelative(comment?.created_at)}</span>
                            </div>
                            <div className="mc-notes-card-body">{body}</div>
                          </div>
                        );
                      })}
                    </>
                  ) : null}

                  {workableAnswers.length > 0 ? (
                    <>
                      <div className="mc-kicker" style={{ marginTop: 18 }}>QUESTIONNAIRE RESPONSES</div>
                      {workableAnswers.map((entry, idx) => {
                        const question = String(entry?.question || '').trim();
                        const answer = String(entry?.answer || '').trim();
                        if (!question && !answer) return null;
                        return (
                          <div key={`wk-answer-${idx}`} className="mc-notes-card">
                            {question ? <div className="mc-notes-card-who">{question}</div> : null}
                            {answer ? <div className="mc-notes-card-body">{answer}</div> : null}
                          </div>
                        );
                      })}
                    </>
                  ) : null}
                </div>

                <div className="mc-notes-col">
                  <div className="mc-kicker">AUDIT TIMELINE</div>
                  {applicationEvents.length === 0 ? (
                    <div className="mc-notes-empty">
                      Audit events will appear here as the candidate moves through the pipeline.
                    </div>
                  ) : (
                    <div className="mc-audit-timeline">
                      {applicationEvents.slice(0, 12).map((event, idx) => {
                        const type = String(event?.event_type || 'activity').replace(/_/g, ' ');
                        const meta = event?.metadata || {};
                        let title = type.charAt(0).toUpperCase() + type.slice(1);
                        if (String(event?.event_type || '').toLowerCase() === 'cv_scored') {
                          const score = Number(meta.role_fit_score);
                          if (Number.isFinite(score)) title = `CV scored — ${Math.round(score)} / 100`;
                        }
                        const detail = event?.reason || event?.description || meta.note || '';
                        return (
                          <div key={event.id || `${event.event_type}-${idx}`} className="mc-audit-row">
                            <span
                              className="mc-audit-dot"
                              aria-hidden="true"
                              style={{ background: eventDotColor(event) }}
                            />
                            <div>
                              <div className="mc-audit-time">{fmtRelative(event?.created_at).toUpperCase()}</div>
                              <div className="mc-audit-title">{title}</div>
                              {detail ? <div className="mc-audit-detail">{detail}</div> : null}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}

                  {workableActivity.length > 0 ? (
                    <>
                      <div className="mc-kicker" style={{ marginTop: 18 }}>WORKABLE ACTIVITY</div>
                      <div className="mc-audit-timeline">
                        {workableActivity.map((entry, idx) => {
                          const action = String(entry?.action || '').replace(/_/g, ' ').trim();
                          const stage = String(entry?.stage || '').trim();
                          const body = String(entry?.body || '').trim();
                          const title = [action, stage].filter(Boolean).join(' · ')
                            || (body ? 'Comment' : 'Workable activity');
                          return (
                            <div key={`wk-activity-${entry?.created_at || idx}`} className="mc-audit-row">
                              <span
                                className="mc-audit-dot"
                                aria-hidden="true"
                                style={{ background: 'var(--purple)' }}
                              />
                              <div>
                                <div className="mc-audit-time">{fmtRelative(entry?.created_at).toUpperCase()}</div>
                                <div className="mc-audit-title">{title}</div>
                                {body && body !== title ? <div className="mc-audit-detail">{body}</div> : null}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </>
                  ) : null}
                </div>
              </div>
            );
          })()}
        </div>
      </div>

      {/* Decision modals — mirror HomeNow's wiring. Rendered at the page root
          so the strip's Override / Teach actions open the SAME flows as the
          home hub. On submit, refetch the candidate's decision + reload the
          report so the strip reflects the new state. */}
      {teachFor ? (
        <TeachModal
          decision={teachFor}
          defaultScope="decision"
          onClose={() => setTeachFor(null)}
          onSubmitted={async () => {
            showToast('Feedback recorded. Decision returned to the queue.', 'success');
            await Promise.all([loadAgentDecision(), loadStandingReport()]);
          }}
        />
      ) : null}

      {alternativeFor ? (
        <OverrideModal
          decision={alternativeFor.decision}
          alternative={alternativeFor.alternative}
          // The candidate report doesn't carry the per-shortcode Workable-stage
          // map the home hub lazy-loads; pass the application's own stage list
          // when present, else [] (OverrideModal advances on the internal stage
          // when there are no Workable stages to pick).
          workableStages={application?.workable_stages || []}
          onClose={() => setAlternativeFor(null)}
          onSubmitted={async () => {
            showToast(
              `${alternativeFor.alternative.confirmLabel || 'Decision'} dispatched.`,
              'success',
            );
            await Promise.all([loadAgentDecision(), loadStandingReport()]);
          }}
        />
      ) : null}
    </div>
  );
};

export default CandidateStandingReportPage;
