// CV document rendering for the candidate standing report's CV tab.
//
// Owns the whole "render the candidate's CV inline" path: mime inference,
// the heuristic CV-text section parser (deriveRawCvSections /
// normalizeCvSections), the branded parsed-sections view (CvDocumentContent),
// and the viewer shell that fetches/downloads the original file
// (CvDocumentViewer). Extracted verbatim from CandidateStandingReportPage.jsx
// to keep the page file under the frontend architecture line cap.
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Download, Github, Globe, Linkedin, Mail, MapPin, Phone } from 'lucide-react';

import { getCachedDocumentBlob } from '../../shared/api/documentCache';
import { Spinner } from '../../shared/ui/TaaliPrimitives';
import { asArray, asCleanText, splitInlineList } from './candidatesUiUtils';

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

const sanitizeDownloadName = (value, fallback = 'candidate-cv') => {
  const cleaned = String(value || '').replace(/[\\/:*?"<>|]+/g, ' ').replace(/\s+/g, ' ').trim();
  return cleaned || fallback;
};

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

// Leading icon per contact / link, matching report-preview's contact row
// (mail / map-pin / phone / linkedin / github / world). Inferred from the value
// itself so it works for both structured contacts and free-text links.
const contactIcon = (item) => {
  const value = String(item || '').toLowerCase();
  if (value.includes('@') && !/^https?:\/\//.test(value)) return Mail;
  if (value.includes('linkedin.com')) return Linkedin;
  if (value.includes('github.com')) return Github;
  if (/^https?:\/\//.test(value) || /\b[a-z0-9-]+\.[a-z]{2,}(\/|$)/.test(value)) return Globe;
  if (/^[+(]?\d[\d\s().-]{6,}$/.test(value.replace(/[•·]/g, ''))) return Phone;
  // Phone numbers can be partially masked (e.g. "+971 5• ••• ••••").
  if (/[•·]/.test(value) && /\d/.test(value)) return Phone;
  return MapPin;
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
            const Icon = contactIcon(item);
            const inner = (
              <>
                <Icon size={14} className="ci" aria-hidden="true" />
                {item}
              </>
            );
            return (
              <React.Fragment key={`${item}-${index}`}>
                {href
                  ? <a className="clink" href={href} target={isLink ? '_blank' : undefined} rel={isLink ? 'noopener noreferrer' : undefined}>{inner}</a>
                  : <span className="clink">{inner}</span>}
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

export { CvDocumentViewer };
