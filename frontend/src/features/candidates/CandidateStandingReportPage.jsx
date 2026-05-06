import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { AlertCircle, Check, Copy, Download, Mail, ExternalLink, Eye, X } from 'lucide-react';

import * as apiClient from '../../shared/api';
import { getCachedDocumentBlob } from '../../shared/api/documentCache';
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
import { ShareModal } from './ShareModal';
import { buildClientReportFilenameStem } from './clientReportUtils';
import { computeFluencyAxes } from '../../shared/assessment/fluencyRollup';
import { RadarChart } from '../../shared/ui/RadarChart';
import { ScoreRing } from '../../shared/ui/ScoreRing';
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

// HANDOFF v2 §5.1: candidate file is exactly 4 tabs.
// Overview · CV & match · Interview prep · Notes & timeline
// (the standalone "Assessment" tab was dropped — its content surfaces on
// Overview now.)
const REPORT_TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'cv', label: 'CV & match' },
  { id: 'prep', label: 'Interview prep', recruiterPrep: true },
  { id: 'notes', label: 'Notes & timeline', internalOnly: true },
];

const INTERNAL_TABS = new Set(REPORT_TABS.filter((tab) => tab.internalOnly).map((tab) => tab.id));
const CLIENT_HIDDEN_TABS = new Set(
  REPORT_TABS.filter((tab) => tab.internalOnly || tab.recruiterPrep).map((tab) => tab.id),
);
const REPORT_TAB_IDS = new Set(REPORT_TABS.map((tab) => tab.id));

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
  const skills = [
    ...asArray(parsed.skills).map(asCleanText),
    ...asArray(application?.candidate_skills).map(asCleanText),
    ...splitInlineList(rawByKey.skills?.lines?.join('\n') || ''),
  ].filter(Boolean);
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

const PrepQuestionCard = ({ item, number, listenLabel, concernLabel, fallbackConcern }) => {
  const listenItems = toBulletList(item?.listenFor);
  const concernItems = toBulletList(item?.redFlags || item?.followUp);
  const evidenceText = asCleanText(item?.evidence);
  const contextText = asCleanText(item?.context);
  return (
    <div className="q-card">
      <div className="q-num">QUESTION {String(number).padStart(2, '0')} · {item?.source || 'Standing report'}</div>
      <div className="q-text">{item?.question}</div>
      {contextText ? (
        <div className="q-context" style={{ marginTop: '6px', fontSize: '13.5px', lineHeight: 1.55, color: 'var(--mute)' }}>
          {contextText}
        </div>
      ) : null}
      <div className="q-meta">
        <div>
          <div className="label">{listenLabel}</div>
          <ul className="listen">
            {(listenItems.length ? listenItems : ['Specific examples tied to the candidate evidence.']).map((line, idx) => (
              <li key={`listen-${idx}`}>{line}</li>
            ))}
          </ul>
        </div>
        <div>
          <div className="label">{concernLabel}</div>
          <ul className="concerning">
            {(concernItems.length ? concernItems : [fallbackConcern]).map((line, idx) => (
              <li key={`concern-${idx}`}>{line}</li>
            ))}
          </ul>
        </div>
      </div>
      {evidenceText ? (
        <div className="q-evidence" style={{ marginTop: '12px', padding: '10px 12px', borderRadius: '10px', background: 'var(--taali-surface-subtle, rgba(124, 58, 237, 0.06))', fontSize: '13px', lineHeight: 1.55, color: 'var(--ink-2)' }}>
          <div className="label" style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--mute)', marginBottom: '4px' }}>
            Anchor in
          </div>
          {evidenceText}
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

const CvMatchRail = ({
  application,
  reportModel,
  cvMatchDetails,
  matchedRequirements,
  missingRequirements,
  onJumpToPrep,
}) => {
  const roleFitScore = reportModel?.summaryModel?.roleFitScore;
  // Don't truncate — the user explicitly wants every recruiter
  // requirement visible on their own report. Lists are pre-sorted by
  // recruiter-first then priority.
  const matchedItems = (
    matchedRequirements.length
      ? matchedRequirements
      : asArray(cvMatchDetails?.matching_skills).map((skill) => ({
        requirement: skill,
        evidence_quote: 'Skill matched in the candidate profile.',
      }))
  );
  const gapItems = (
    missingRequirements.length
      ? missingRequirements
      : asArray(cvMatchDetails?.missing_skills).map((skill) => ({
        requirement: skill,
        evidence_quote: 'Probe this in the interview loop.',
      }))
  );
  // Split gaps into "partial" (some evidence on file but incomplete) and
  // "missing" (no evidence found, or contradicted). Skill-string fallbacks
  // have no status so they default to missing.
  const partialItems = gapItems.filter((item) => (
    String(item?.status || '').toLowerCase() === 'partially_met'
  ));
  const missingItems = gapItems.filter((item) => (
    String(item?.status || '').toLowerCase() !== 'partially_met'
  ));
  const requirementTotal = Array.isArray(cvMatchDetails?.requirements_assessment)
    ? cvMatchDetails.requirements_assessment.length
    : matchedItems.length + gapItems.length;
  const scoredAt = application?.cv_match_scored_at || application?.updated_at || null;
  const summaryText = String(cvMatchDetails?.summary || '').trim();
  const summaryParagraphs = summaryText
    ? summaryText.split(/\n{2,}|\r\n{2,}/).map((p) => p.trim()).filter(Boolean)
    : [];

  const renderPartialItem = (item, index) => {
    const evidence = item?.impact || extractRequirementEvidence(item) || item?.evidence_quote || 'Probe this live.';
    return (
      <div key={extractRequirementKey(item, index)} className="rail-item gap">
        <span className="ic"><AlertCircle size={10} strokeWidth={3} /></span>
        <span>
          <span className="t">{item.requirement || item}</span>
          <span className="ev">{evidence}</span>
        </span>
      </div>
    );
  };

  const renderMissingItem = (item, index) => {
    const evidence = item?.impact || extractRequirementEvidence(item) || item?.evidence_quote || 'Probe this live.';
    return (
      <div key={extractRequirementKey(item, index)} className="rail-item bad">
        <span className="ic"><X size={10} strokeWidth={3} /></span>
        <span>
          <span className="t">{item.requirement || item}</span>
          <span className="ev">{evidence}</span>
        </span>
      </div>
    );
  };

  return (
    <section className="cv-rail cv-match-summary" aria-label="CV match summary">
      <div className="rail-card cv-summary-bar">
        <div className="rail-score">
          {roleFitScore != null ? (
            <ScoreRing score={Math.round(roleFitScore)} size={96} label="CV MATCH" />
          ) : (
            <div className="mc-report-snapshot-score-empty" style={{ width: 96, height: 96 }}>—</div>
          )}
          <div>
            <div className="mc-kicker" style={{ marginBottom: 6 }}>CV MATCH</div>
            <div style={{ fontFamily: 'var(--font-display)', fontSize: 20, fontWeight: 600, letterSpacing: '-0.015em', color: 'var(--ink)', lineHeight: 1.2 }}>
              {requirementTotal
                ? <>{matchedItems.length} of {requirementTotal} requirements <em style={{ fontStyle: 'normal', color: 'var(--purple)' }}>evidenced</em></>
                : <>CV evidence summary</>}
            </div>
            <div className="meta" style={{ marginTop: 4 }}>
              vs. <b>{application?.role_name || application?.candidate_position || 'target role'}</b>
            </div>
            <div className="rail-meta" style={{ marginTop: '4px', fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--mute)' }}>
              {scoredAt ? `Scored ${new Date(scoredAt).toLocaleDateString()}` : 'Awaiting CV score'}
            </div>
          </div>
        </div>
        {summaryParagraphs.length ? (
          <div className="cv-summary-text">
            {summaryParagraphs.map((paragraph, idx) => (
              <p key={`cv-summary-${idx}`}>{paragraph}</p>
            ))}
          </div>
        ) : null}
      </div>

      <div className="cv-rail-columns">
        <div className="rail-card">
          <div className="rail-section">
            <div className="rail-head">
              <span className="lbl">Matched · <b>{matchedItems.length}</b></span>
              <span className="dot ok" aria-hidden="true" />
            </div>
            {matchedItems.length ? matchedItems.map((item, index) => {
              const evidence = extractRequirementEvidence(item) || item?.evidence_quote || 'Matched evidence on file.';
              return (
                <div key={extractRequirementKey(item, index)} className="rail-item ok">
                  <span className="ic"><Check size={10} strokeWidth={3} /></span>
                  <span>
                    <span className="t">{item.requirement || item}</span>
                    <span className="ev">{evidence}</span>
                  </span>
                </div>
              );
            }) : (
              <div className="rail-empty">No matched requirements are attached yet.</div>
            )}
          </div>
        </div>

        <div className="rail-card">
          <div className="rail-section">
            <div className="rail-head">
              <span className="lbl">Partial · <b>{partialItems.length}</b></span>
              <span className="dot gap" aria-hidden="true" />
            </div>
            {partialItems.length ? partialItems.map(renderPartialItem) : (
              <div className="rail-empty">No partial matches.</div>
            )}
          </div>
        </div>

        <div className="rail-card">
          <div className="rail-section">
            <div className="rail-head">
              <span className="lbl">Missing · <b>{missingItems.length}</b></span>
              <span className="dot bad" aria-hidden="true" />
            </div>
            {missingItems.length ? missingItems.map(renderMissingItem) : (
              <div className="rail-empty">No missing requirements.</div>
            )}
          </div>

          <button type="button" className="rail-jump" onClick={onJumpToPrep}>
            View interview prep →
          </button>
        </div>
      </div>
    </section>
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
  const [shareModalOpen, setShareModalOpen] = useState(false);
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
  const viewParam = searchParams.get('view');
  const isClientView = viewParam === 'client';
  const isInterviewView = viewParam === 'interview' || isClientView;
  const hiddenTabs = isClientView
    ? CLIENT_HIDDEN_TABS
    : (isInterviewView ? INTERNAL_TABS : new Set());
  const requestedTab = searchParams.get('tab') || 'overview';
  const backFromRoleId = useMemo(() => {
    const match = (searchParams.get('from') || '').match(/^jobs\/(\d+)$/);
    return match ? Number(match[1]) : null;
  }, [searchParams]);
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
    setActiveTab(hiddenTabs.has(nextTab) ? 'overview' : nextTab);
  }, [hiddenTabs, requestedTab]);

  const activateTab = useCallback((tabId) => {
    const safeTab = hiddenTabs.has(tabId) ? 'overview' : tabId;
    setActiveTab(safeTab);
    const nextParams = new URLSearchParams(searchParams);
    if (safeTab === 'overview') {
      nextParams.delete('tab');
    } else {
      nextParams.set('tab', safeTab);
    }
    setSearchParams(nextParams, { replace: true });
  }, [hiddenTabs, searchParams, setSearchParams]);

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
        : await rolesApi.getApplication(numericApplicationId, { params: { include_cv_text: true } });
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
  const preScreenDecision = String(cvMatchDetails?.pre_screen_decision || '').toLowerCase();
  const isPreScreenedOut = preScreenDecision === 'no';
  const preScreenReason = String(cvMatchDetails?.pre_screen_reason || '').trim();
  const handleRunFullEvaluation = useCallback(async () => {
    if (!application?.id || !rolesApi?.scoreSelected || !application?.role_id) return;
    setBusyAction('rescore');
    try {
      await rolesApi.scoreSelected(application.role_id, [application.id], { force: true });
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

  // Report PDF export removed per HANDOFF v2 §3 — share links replace PDFs
  // entirely; do not reintroduce a download path.

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

  const handleCopyClientLink = async () => {
    try {
      const payload = await loadShareLink({ force: !shareUrl });
      const baseUrl = payload?.share_url || shareUrl || buildFallbackShareUrl(application?.id || routeApplicationKey, payload?.share_token || sharedRouteToken);
      if (!baseUrl) throw new Error('Share link unavailable.');
      const clientUrl = baseUrl.replace('view=interview', 'view=client');
      await navigator.clipboard.writeText(clientUrl);
      showToast('External client link copied (recruiter notes hidden).', 'success');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to copy client link.'), 'error');
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
            {isClientView ? (
              <span><b>Client view.</b> External, client-safe summary — recruiter notes, scoring breakdown, and interview prep are hidden.</span>
            ) : (
              <span><b>Interview view.</b> You are seeing the panel-safe version of this Taali report.</span>
            )}
          </div>
        ) : null}
        {(() => {
          // Prefer the role on the application itself — it's always
          // populated when the candidate is attached to a role, so the
          // back link works even when the user reloads /candidates/N
          // and loses the ?from=jobs/X query param. Fall back to the
          // ?from param (kept for legacy deep-links that pass it
          // explicitly), and finally to the all-candidates list.
          const targetRoleId = application?.role_id ?? backFromRoleId ?? null;
          const targetRoleName = application?.role_name || 'job';
          return (
            <button
              type="button"
              className="standing-back back"
              data-internal-only
              onClick={() => {
                if (targetRoleId != null) {
                  onNavigate('job-pipeline', { roleId: targetRoleId });
                  return;
                }
                onNavigate('candidates');
              }}
            >
              {targetRoleId != null
                ? `← Back to job: ${targetRoleName}`
                : '← Back to candidates'}
            </button>
          );
        })()}
        <div className="kicker" style={{ marginBottom: '10px' }}>Candidate standing report</div>

        <div className="report-hero">
          <div className="meta">
            <span className="kicker">STANDING REPORT · APPLICATION #{application?.id || '—'}</span>
            <span className={`chip ${reportModel?.recommendation?.variant === 'success' ? 'green' : reportModel?.recommendation?.variant === 'warning' ? 'amber' : 'purple'}`}>
              {reportModel?.recommendation?.label || 'Pending review'}
            </span>
            {isPreScreenedOut ? (
              <span className="chip" style={{ background: 'var(--taali-surface-subtle, rgba(100,116,139,0.15))', color: 'var(--ink-2)' }}>
                Pre-screened out
              </span>
            ) : null}
          </div>
          {isPreScreenedOut ? (
            <div
              data-internal-only
              style={{
                marginTop: '14px',
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
                <strong>Filtered out by pre-screen.</strong>{' '}
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
          <h1>
            {application?.candidate_name || application?.candidate_email || 'Candidate'}
            {application?.role_name ? (
              <> · <span style={{ color: 'var(--mute)' }}>{application.role_name}</span></>
            ) : null}
          </h1>
          <div className="report-hero-grid">
            <div className="c hi">
              <div className="k">TAALI score</div>
              <div className="v">{reportModel?.summaryModel?.taaliScore != null ? `${Math.round(reportModel.summaryModel.taaliScore)} / 100` : '—'}</div>
              <div className="d">{completedAssessment ? 'CV + assessment' : 'Pre-assessment'}</div>
            </div>
            <div className="c hi">
              <div className="k">Role fit</div>
              <div className="v">{reportModel?.summaryModel?.roleFitScore != null ? `${Math.round(reportModel.summaryModel.roleFitScore)} / 100` : '—'}</div>
              <div className="d">{application?.role_name || application?.candidate_position || 'Role evidence'}</div>
            </div>
            <div className="c">
              <div className="k">Assessment</div>
              <div className="v">{reportModel?.summaryModel?.assessmentScore != null ? `${Math.round(reportModel.summaryModel.assessmentScore)} / 100` : '—'}</div>
              <div className="d">{completedAssessment ? 'Completed signal present' : 'Pending completion'}</div>
            </div>
            <div className="c">
              <div className="k">Workable raw</div>
              <div className="v">{application?.workable_score_raw != null ? `${Math.round(application.workable_score_raw)} / 100` : '—'}</div>
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
            <button
              type="button"
              className="btn btn-purple btn-sm"
              onClick={() => setShareModalOpen(true)}
              disabled={shareState.loading || !application?.id}
            >
              <ExternalLink size={14} />
              Share report
            </button>
            <button type="button" className="btn btn-outline btn-sm" onClick={handleCopyLink} disabled={shareState.loading || !application?.id}>
              <Copy size={14} />
              Copy interview link
            </button>
            <button type="button" className="btn btn-outline btn-sm" onClick={handleEmailShare} disabled={shareState.loading || !application?.id}>
              <Mail size={14} />
              Email to panel
            </button>
            {/* No PDF surface anywhere on the candidate file per HANDOFF v2 §3. */}
          </div>
        </div>
        {shareState.error ? <p className="mt-3 text-xs text-[var(--taali-danger)]">{shareState.error}</p> : null}

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
          {REPORT_TABS.filter((tab) => !hiddenTabs.has(tab.id)).map((tab) => (
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
        {(() => {
          const fluencyAxes = computeFluencyAxes(completedAssessment);
          const compositeScore = (() => {
            if (Number.isFinite(Number(completedAssessment?.score))) {
              const s = Number(completedAssessment.score);
              return s <= 10 ? s * 10 : s;
            }
            if (Number.isFinite(Number(application?.cv_match_score))) return Number(application.cv_match_score);
            return null;
          })();
          if (compositeScore == null && !fluencyAxes) return null;
          return (
            <div className="mc-report-snapshot">
              <div className="mc-report-snapshot-score">
                {compositeScore != null ? (
                  <ScoreRing score={Math.round(compositeScore)} size={140} />
                ) : (
                  <div className="mc-report-snapshot-score-empty">Score pending</div>
                )}
                <div className="mc-report-snapshot-score-meta">
                  <div className="mc-kicker">COMPOSITE · 0–100</div>
                  <div className="mc-report-snapshot-score-label">
                    {reportModel?.recommendation?.label || 'Standing report'}
                  </div>
                </div>
              </div>
              <div className="mc-report-snapshot-radar">
                <div className="mc-kicker is-mute" style={{ marginBottom: 8 }}>AI FLUENCY · 6 DIMENSIONS</div>
                {fluencyAxes ? (
                  <RadarChart values={fluencyAxes} max={100} size={260} />
                ) : (
                  <div className="mc-report-snapshot-radar-empty">
                    <p><b>Scoring pending.</b></p>
                    <p>The fluency radar fills in once the candidate finishes the assessment runtime — we roll up prompt quality, error recovery, context utilization, independence, design thinking, and written communication into the six canvas axes.</p>
                  </div>
                )}
              </div>
            </div>
          );
        })()}
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
              {strengthItems.length ? strengthItems.map((item, index) => {
                const isCvHighlight = item.source === 'cv_match';
                const numericValue = Number.isFinite(Number(item?.value)) ? Number(item.value) : null;
                return (
                  <div key={item.key} className="rank-row">
                    <div className="rk">{String(index + 1).padStart(2, '0')}</div>
                    <div>
                      <div className="t">{item.label}</div>
                      <div className="s">
                        {isCvHighlight
                          ? 'Highlight extracted from the candidate CV during scoring. Probe for ownership and outcomes during interviews.'
                          : index === 0
                            ? reportModel?.strongestSignalDescription
                            : `Score signal remains strong in ${String(item.label || '').toLowerCase()} across the current evidence set.`}
                      </div>
                      {index === 0 && !isCvHighlight ? (
                        <div className="evidence-block">
                          <div className="turn">Evidence</div>
                          {reportModel?.evidenceSections?.roleFit?.description || reportModel?.evidenceSections?.assessment?.description || 'Standing report evidence is attached directly to the linked recruiter and assessment records.'}
                        </div>
                      ) : null}
                    </div>
                    <div className="pct">
                      {isCvHighlight ? <span className="chip purple">CV match</span> : (numericValue != null ? `${Math.round(numericValue * 10)} / 100` : '—')}
                    </div>
                  </div>
                );
              }) : (
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
                    <span className="dimension-score">{Math.round(Number(item.value || 0) * 10)} / 100</span>
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
            <CvMatchRail
              application={application}
              reportModel={reportModel}
              cvMatchDetails={cvMatchDetails}
              matchedRequirements={matchedRequirements}
              missingRequirements={missingRequirements}
              onJumpToPrep={() => activateTab('prep')}
            />
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
          <div className="prep-stack">
            <div className="panel prep-panel">
              <h2>Stage 1 <em>recruiter screen</em></h2>
              <p className="sub">Use these to validate claims quickly before the deeper panel loop.</p>
              <div className="qgroup">
                {interviewQuestions.stageOne.map((item, index) => (
                  <PrepQuestionCard
                    key={`${item.question}-${index}`}
                    item={item}
                    number={index + 1}
                    listenLabel="Listen for"
                    concernLabel="Follow-up"
                    fallbackConcern="Ask for one concrete example, artifact, or tradeoff."
                  />
                ))}
              </div>
            </div>
            <div className="panel prep-panel">
              <h2>Stage 2 <em>technical panel</em></h2>
              <p className="sub">Designed for the hiring panel: probe how the candidate thinks with AI in the actual work.</p>
              <div className="qgroup">
                {interviewQuestions.stageTwo.map((item, index) => (
                  <PrepQuestionCard
                    key={`${item.question}-${index}`}
                    item={item}
                    number={index + 1}
                    listenLabel="Strong signal"
                    concernLabel="Concern"
                    fallbackConcern="Vague answers without links to code, prompts, or decisions."
                  />
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
      <ShareModal
        open={shareModalOpen}
        onClose={() => setShareModalOpen(false)}
        applicationId={application?.id}
        initialToken={shareState?.token || ''}
      />
    </div>
  );
};

export default CandidateStandingReportPage;
