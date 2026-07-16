import React from 'react';

import { MarketingNav } from '../../shared/layout/TaaliLayout';
import { PageLink } from '../../shared/ui/PageLink';

const LAST_UPDATED = '15 July 2026';

const TERMS = [
  ['Using Taali', 'You must be legally able to enter an agreement, provide accurate account information, protect your credentials, and use Taali only for lawful hiring and assessment activity. You are responsible for the people you invite and the instructions, content, and integrations configured in your workspace.'],
  ['Candidate decisions', 'Taali provides evidence, workflow automation, and recommendations. It does not replace your obligation to make fair, lawful, and appropriately reviewed employment decisions. Do not use the service to unlawfully discriminate or make a solely automated decision where applicable law requires human review.'],
  ['Your data', 'You retain ownership of content you submit. You grant Taali the limited rights needed to host, process, secure, and transmit that content to provide the service. Candidate and customer data is not used to train general-purpose AI models.'],
  ['Acceptable use', 'Do not probe or disrupt the service, bypass access controls or usage limits, upload malware, infringe rights, scrape the service, share credentials outside your workspace, or use Taali for unlawful surveillance or high-impact decisions without required safeguards.'],
  ['Third-party services', 'Features may connect to services such as applicant tracking, email, source control, hosting, and AI providers. Their availability and separate terms may affect those features. You authorize Taali to exchange the minimum data needed for integrations you enable.'],
  ['Fees and suspension', 'Paid usage, prices, credits, and limits shown in the service or an order form apply to your workspace. We may pause affected functionality to protect the service, comply with law, address security risk, or prevent material non-payment, and will restore access when the issue is resolved where reasonably possible.'],
  ['Service and liability', 'The service is provided with reasonable care, but uninterrupted or error-free operation is not guaranteed. To the extent allowed by law, neither party is liable for indirect or consequential loss. Nothing here excludes liability that cannot legally be excluded. Any negotiated order form or data-processing agreement controls where it conflicts with these online terms.'],
  ['Ending use and changes', 'You may stop using Taali and request account or data deletion, subject to legal retention obligations. We may update these terms and will post the effective date; material changes will be communicated through the service or account contact.'],
];

const PRIVACY = [
  ['What we collect', 'We process account and workspace details, role and candidate records, assessment content and transcripts, uploaded documents, integration data, support communications, device and security logs, and usage or billing records needed to operate Taali.'],
  ['Why we process it', 'We use data to provide and secure the service, authenticate users, run requested hiring workflows and assessments, support customers, prevent abuse, meter usage, improve reliability, and comply with legal obligations. We rely on contract, legitimate interests, consent, or legal obligation as applicable.'],
  ['AI processing', 'When a feature uses an AI provider, Taali sends the context needed to perform the requested task. Candidate and customer data is not used by Taali to train general-purpose models. Workspace administrators should provide candidates with appropriate notices and obtain any consent required for assessment or automated assistance.'],
  ['Who receives data', 'Authorized members of your workspace can access data according to their role. We also use vetted infrastructure, communications, analytics, security, payment, integration, and AI subprocessors. We disclose data when required by law or as part of a protected corporate transaction. We do not sell personal data.'],
  ['International transfers', 'Service providers may process data in more than one country. Where required, transfers use recognized safeguards such as contractual clauses and supplementary security measures. Contact us for the current processing location and subprocessor information for your deployment.'],
  ['Retention and security', 'We retain data for the customer relationship, configured product retention, dispute and legal needs, and limited backup periods. We use access controls, tenant isolation, encryption in transit, monitoring, and operational safeguards, while recognizing that no system can guarantee absolute security.'],
  ['Your choices and rights', 'Depending on location, people may request access, correction, deletion, restriction, portability, or objection, and may withdraw consent. Workspace customers normally act as controller for candidate data, so candidate requests may be referred to the relevant employer or recruiter.'],
  ['Contact', 'Email privacy@taali.ai for privacy requests or questions. Include enough information to identify the relevant workspace; we may verify identity before completing a request. You may also complain to your local data-protection authority.'],
];

export function LegalPage({ kind = 'privacy', onNavigate }) {
  const isTerms = kind === 'terms';
  const title = isTerms ? 'Terms of Service' : 'Privacy Notice';
  const sections = isTerms ? TERMS : PRIVACY;

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
      <MarketingNav onNavigate={onNavigate} />
      <main className="mx-auto max-w-3xl px-6 py-14 md:py-20">
        <p className="kicker">LEGAL · TAALI</p>
        <h1 className="mt-3 font-[var(--font-display)] text-4xl font-semibold tracking-tight md:text-5xl">{title}</h1>
        <p className="mt-4 text-sm text-[var(--mute)]">Last updated: {LAST_UPDATED}</p>
        <p className="mt-7 text-base leading-7 text-[var(--ink-2)]">
          This notice applies to Taali&apos;s website and hiring platform. If your organization has a signed order form or data-processing agreement, that agreement also applies.
        </p>
        <div className="mt-10 space-y-9">
          {sections.map(([heading, body]) => (
            <section key={heading}>
              <h2 className="font-[var(--font-display)] text-2xl font-semibold">{heading}</h2>
              <p className="mt-3 text-base leading-7 text-[var(--ink-2)]">{body}</p>
            </section>
          ))}
        </div>
        <div className="mt-14 flex flex-wrap gap-4 border-t border-[var(--line)] pt-7 text-sm">
          <PageLink page={isTerms ? 'privacy' : 'terms'} className="font-semibold text-[var(--purple)]">
            Read our {isTerms ? 'Privacy Notice' : 'Terms of Service'}
          </PageLink>
          <a href="mailto:privacy@taali.ai" className="font-semibold text-[var(--purple)]">Contact privacy@taali.ai</a>
        </div>
      </main>
    </div>
  );
}

export default LegalPage;
