import {
  Check,
  Copy,
  ExternalLink,
  FileText,
  GitFork,
  RefreshCw,
  Rocket,
  Share2,
} from 'lucide-react';

import { MotionSpinner } from '../../shared/motion';
import { atsProviderLabel } from '../jobs/atsType';

const requiredFieldsTitle = (count) => (
  count > 0 ? `${count} required field${count === 1 ? '' : 's'} still needed` : undefined
);

function RelatedRoleActions({
  applied,
  brief,
  onNavigate,
  onPublish,
  preview,
  publishing,
  requiredRemaining,
}) {
  return (
    <div className="rq-related-card">
      <div className="rq-related-source">
        <span className="rq-related-flag"><GitFork size={14} /> Related to</span>
        <button
          type="button"
          className="rq-related-source-link"
          onClick={() => onNavigate?.('job-pipeline', {
            roleId: brief.source_role?.role_id || brief.source_role_id,
          })}
        >
          {brief.source_role?.name || `Job #${brief.source_role_id}`}
        </button>
        {brief.source_role?.ats_provider ? (
          <span className="rq-related-provider">
            {atsProviderLabel(brief.source_role.ats_provider)}
          </span>
        ) : null}
      </div>
      {!applied && preview ? (
        <div className="rq-related-metrics" aria-label="Related role scoring preview">
          <span><strong>{preview.candidates_total ?? 0}</strong> shared candidates</span>
          <span><strong>{preview.candidates_with_cv ?? 0}</strong> ready to score</span>
          <span><strong>${Number(preview.estimated_cost_usd || 0).toFixed(2)}</strong> estimated</span>
        </div>
      ) : null}
      <p className="rq-related-hint">
        {applied
          ? 'This Taali scoring role remains coupled to the original ATS job for candidate stages and actions.'
          : 'Edit the cloned specification in this chat. Creating it makes a separate Taali scoring view while candidate stages and actions stay coupled to the original ATS job.'}
      </p>
      <div className="rq-published-actions">
        {applied && brief.job?.role_id ? (
          <button
            type="button"
            className="rq-btn-sm is-primary"
            onClick={() => onNavigate?.('job-pipeline', { roleId: brief.job.role_id })}
          >
            <Rocket size={13} /> Open related role
          </button>
        ) : (
          <button
            type="button"
            className="rq-publish-btn"
            onClick={onPublish}
            disabled={publishing || requiredRemaining > 0}
            title={requiredFieldsTitle(requiredRemaining)}
          >
            {publishing
              ? <MotionSpinner className="rq-motion-spinner" size={15} />
              : <GitFork size={15} />}{' '}
            Create and score candidates
          </button>
        )}
        {!applied && requiredRemaining > 0 ? (
          <span className="rq-publish-hint">{requiredFieldsTitle(requiredRemaining)}</span>
        ) : null}
      </div>
    </div>
  );
}

function StandardRequisitionActions({
  activeAts,
  activeAtsLabel,
  applied,
  atsBridge,
  atsSpec,
  atsSpecCopied,
  careersCopied,
  careersUrl,
  clientCopied,
  clientLink,
  clientLinkUrl,
  clientLinking,
  copied,
  jobPage,
  jobPageUrl,
  linkedExternalJobId,
  linkedExternalJobLive,
  linkedExternalJobState,
  linkedJob,
  linkedJobOpen,
  onCopyAtsSpec,
  onCopyCareersUrl,
  onCopyClientUrl,
  onCopyJobUrl,
  onMakeClientLink,
  onNavigate,
  onPublish,
  publishing,
  refCode,
  requiredRemaining,
}) {
  return (
    <>
      {clientLink ? (
        <div className="rq-clientlink">
          <div className="rq-clientlink-top">
            <span className="rq-clientlink-flag"><Share2 size={14} /> Hiring-manager link</span>
            <a
              className="rq-published-url"
              href={clientLinkUrl}
              target="_blank"
              rel="noopener noreferrer"
              title={clientLinkUrl}
            >
              {clientLinkUrl}
            </a>
          </div>
          <div className="rq-published-actions">
            <span className="rq-clientlink-hint">Send this to the hiring manager — no login needed.</span>
            <button type="button" className="rq-btn-sm is-ghost" onClick={onCopyClientUrl}>
              {clientCopied ? <Check size={13} /> : <Copy size={13} />} {clientCopied ? 'Copied' : 'Copy'}
            </button>
            <a className="rq-btn-sm is-ghost" href={clientLinkUrl} target="_blank" rel="noopener noreferrer">
              <ExternalLink size={13} /> Open
            </a>
          </div>
        </div>
      ) : !applied ? (
        <button
          type="button"
          className="rq-btn-sm is-ghost rq-share-btn"
          onClick={onMakeClientLink}
          disabled={clientLinking}
          title="Get a no-login link to send to the hiring manager"
        >
          {clientLinking
            ? <MotionSpinner className="rq-motion-spinner" size={15} />
            : <Share2 size={14} />}{' '}
          Share with hiring manager
        </button>
      ) : null}

      {jobPage ? (
        <div className="rq-published">
          <div className="rq-published-top">
            <span className="rq-published-flag">
              <Check size={15} /> {linkedJobOpen ? 'Live · accepting applications' : 'Preview ready · applications open after Turn on'}
            </span>
            <a
              className="rq-published-url"
              href={jobPageUrl}
              target="_blank"
              rel="noopener noreferrer"
              title={jobPageUrl}
            >
              {jobPageUrl}
            </a>
          </div>
          <div className="rq-published-actions">
            {linkedJob?.role_id ? (
              <button
                type="button"
                className="rq-btn-sm is-primary"
                onClick={() => onNavigate?.('job-pipeline', { roleId: linkedJob.role_id })}
              >
                <Rocket size={13} /> {linkedJobOpen ? 'Open job' : 'Open job to turn on'}
              </button>
            ) : null}
            <button type="button" className="rq-btn-sm is-ghost" onClick={onCopyJobUrl}>
              {copied ? <Check size={13} /> : <Copy size={13} />}{' '}
              {copied ? 'Copied' : (linkedJobOpen ? 'Copy' : 'Copy preview')}
            </button>
            <a className="rq-btn-sm is-ghost" href={jobPageUrl} target="_blank" rel="noopener noreferrer">
              <ExternalLink size={13} /> {linkedJobOpen ? 'View job page' : 'View preview'}
            </a>
            <button
              type="button"
              className="rq-btn-sm is-ghost"
              onClick={onPublish}
              disabled={publishing || requiredRemaining > 0}
              title={requiredFieldsTitle(requiredRemaining)}
            >
              {publishing
                ? <MotionSpinner className="rq-motion-spinner" size={15} />
                : <RefreshCw size={13} />}{' '}
              Re-publish
            </button>
          </div>
          {careersUrl ? (
            <div className="rq-careers-row">
              <a
                className="rq-careers-link"
                href={careersUrl}
                target="_blank"
                rel="noopener noreferrer"
                title={careersUrl}
              >
                {linkedJobOpen ? 'Live on your careers page' : 'Appears on your careers page after Turn on'} <ExternalLink size={12} />
              </a>
              <button type="button" className="rq-btn-sm is-ghost" onClick={onCopyCareersUrl}>
                {careersCopied ? <Check size={13} /> : <Copy size={13} />} {careersCopied ? 'Copied' : 'Copy'}
              </button>
            </div>
          ) : null}
          {activeAts ? (
            <div className="rq-workable-row">
              <div className="rq-workable-head">
                <span className={`rq-job-status ${linkedExternalJobId && linkedExternalJobLive !== false ? 'is-open' : 'is-draft'}`}>
                  {linkedExternalJobId
                    ? `Linked to ${activeAtsLabel} · ${linkedExternalJobLive === false ? (linkedExternalJobState || 'not live') : 'Open'}`
                    : `Taali job ready · ${activeAtsLabel} optional`}
                </span>
                {refCode ? <code className="rq-ref-code" title="Job reference code">{refCode}</code> : null}
              </div>
              <p className="rq-workable-hint">{atsBridge.hint}</p>
              {atsBridge.copyLabel ? (
                <button
                  type="button"
                  className="rq-btn-sm is-ghost"
                  onClick={onCopyAtsSpec}
                  disabled={!atsSpec}
                >
                  {atsSpecCopied ? <Check size={13} /> : <FileText size={13} />}{' '}
                  {atsSpecCopied ? 'Copied' : atsBridge.copyLabel}
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : (
        <div className="rq-publish-wrap">
          <button
            type="button"
            className="rq-publish-btn"
            onClick={onPublish}
            disabled={publishing || requiredRemaining > 0}
            title={requiredFieldsTitle(requiredRemaining)}
          >
            {publishing
              ? <MotionSpinner className="rq-motion-spinner" size={15} />
              : <Rocket size={15} />}{' '}
            Publish job page
          </button>
          {requiredRemaining > 0 ? (
            <span className="rq-publish-hint">{requiredFieldsTitle(requiredRemaining)}</span>
          ) : null}
        </div>
      )}
    </>
  );
}

export function RequisitionHeaderActions({ relatedRoleDraft, ...props }) {
  return relatedRoleDraft
    ? <RelatedRoleActions {...props} />
    : <StandardRequisitionActions {...props} />;
}

export default RequisitionHeaderActions;
