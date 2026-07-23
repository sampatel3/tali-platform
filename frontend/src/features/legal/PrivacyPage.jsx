import React from 'react';

import { LegalLayout } from './LegalLayout';
import { PageLink } from '../../shared/ui/PageLink';
import { WORKSPACE_SIGNAL_SUMMARY } from '../../shared/assessment/sessionDisclosure';

// NOTE: Pre-counsel draft — review pending before publication. The operative
// facts (roles, data categories, transfer mechanisms, retention guidance) are
// verified against product behaviour as of 23 July 2026, but the wording has
// not yet been reviewed by legal counsel.

export const PrivacyPage = () => (
  <LegalLayout kicker="Legal" title="Privacy Notice" updated="23 July 2026">
    <p className="legal-lede">
      This notice explains how Taali handles personal data across the two roles it plays:
      as the controller of our own customer and site data, and as a processor acting on
      behalf of the organisations that use Taali to hire.
    </p>

    <h2>1. Who we are</h2>
    <p>
      Taali (<a href="https://www.taali.ai">taali.ai</a>) operates an AI-assisted hiring
      platform. You can reach us about anything in this notice at{' '}
      <a href="mailto:hello@taali.ai">hello@taali.ai</a>.
    </p>

    <h2>2. Our two roles</h2>
    <p>
      Taali processes personal data in two distinct capacities, and different rules apply to
      each:
    </p>
    <ul>
      <li>
        <strong>Controller.</strong> For recruiter and customer account data, how you use the
        site, and billing, we are the controller — we decide why and how that data is
        processed.
      </li>
      <li>
        <strong>Processor.</strong> For candidate data processed inside the platform, we act
        as a processor on behalf of the recruitment agency or employer using Taali. That
        organisation is the controller. If you are a candidate, you should also consult that
        organisation&apos;s own privacy notice: it, not Taali, decides why candidate data is
        processed and for how long.
      </li>
    </ul>

    <h2>3. Candidate data we process on customers&apos; behalf</h2>
    <p>
      When a customer uses Taali to run a hiring process, we process the following candidate
      data on their instructions:
    </p>
    <ul>
      <li>CVs and applications;</li>
      <li>screening answers;</li>
      <li>CV-to-role scoring results, with the evidence citations behind them;</li>
      <li>
        work-sample assessment session records — the prompts sent to the AI assistant, the
        AI responses, file changes, and validation runs;
      </li>
      <li>
        assessment integrity metrics, logged from the assessment browser tab to deter
        and detect cheating &mdash; {WORKSPACE_SIGNAL_SUMMARY}. Each event records the
        workspace surface it came from, a character count and the repo file path, never
        the content of what a candidate types or copies, and they are not an input to
        scoring. We do <strong>not</strong> record screen, camera, or microphone;
      </li>
      <li>interview transcripts, where the customer connects a transcription integration;</li>
      <li>hiring decisions and the full decision history.</li>
    </ul>

    <h2>4. Automated decision-making</h2>
    <p>
      Taali produces rule-based advance/reject recommendations against role criteria defined
      by the recruiter, and every decision records the rule path and any human override.
    </p>
    <p>
      <strong>Advancing</strong> a candidate &mdash; progressing them, sending an assessment, or
      any other positive step &mdash; is always queued for a person on the recruiting team to
      confirm before it happens.
    </p>
    <p>
      <strong>Rejection at the pre-screen stage can be automatic.</strong> Where a candidate
      does not meet the screening rules the recruiter wrote, or scores below the pre-screen
      threshold the recruiter set, the recruiting organisation can have that rejection applied
      without a person confirming it individually. This setting is on by default and each
      recruiting organisation can turn it off for any role. Pre-screen scoring is assisted by
      an AI model; the reject rule applied to it is deterministic.
    </p>
    <p>
      If a decision about you was made this way, you have the right to obtain human review of
      it. Contact the recruiting organisation, or use the route below and we will pass your
      request to them.
    </p>
    <p>Candidates are entitled to:</p>
    <ul>
      <li>information about the logic involved;</li>
      <li>making representations;</li>
      <li>obtaining human intervention; and</li>
      <li>contesting a decision.</li>
    </ul>
    <p>
      To exercise these, contact the recruiting organisation, or email{' '}
      <a href="mailto:hello@taali.ai">hello@taali.ai</a> and we will route the request to them.
      We aim to acknowledge within 3 working days and resolve within 30 days.
    </p>

    <h2>5. AI processing</h2>
    <p>
      We use Anthropic&apos;s Claude via API for AI analysis. We do not use candidate data to
      train AI models.
    </p>

    <h2>6. International transfers</h2>
    <p>
      The platform is hosted in the United States. Transfers of UK/EU personal data rely on
      the EU–US Data Privacy Framework and UK Extension where the subprocessor is certified,
      and on Standard Contractual Clauses otherwise. The full list of subprocessors, their
      locations, and the transfer mechanism for each is at{' '}
      <PageLink page="subprocessors">taali.ai/subprocessors</PageLink>.
    </p>

    <h2>7. Retention</h2>
    <p>
      Candidate data is retained according to the controlling customer&apos;s configuration.
      Our default guidance is to keep the data of unsuccessful candidates for 6–12 months
      after a hiring process closes, and longer only with a documented reason or the
      candidate&apos;s opt-in (for example, a talent pool). Account data is retained for the
      life of the account plus any statutory retention periods.
    </p>

    <h2>8. Your rights</h2>
    <p>
      Subject to applicable law, you have the right to access, rectification, erasure,
      restriction, portability, and objection in relation to your personal data. To exercise
      any of these, contact the recruiting organisation (where Taali is the processor) or
      email <a href="mailto:hello@taali.ai">hello@taali.ai</a>. You also have the right to
      complain to a supervisory authority — in the UK, the Information Commissioner&apos;s
      Office (ICO); in the EU, your local supervisory authority.
    </p>

    <h2>9. Security</h2>
    <p>
      We protect personal data with tenant isolation, role-based access, time-limited and
      revocable share links, an audit and decision history, and encryption in transit.
    </p>

    <h2>10. Cookies</h2>
    <p>
      The platform sets only the cookies and browser storage needed to operate it — for
      authentication and to keep your session. We do not use third-party analytics,
      advertising, or cross-site tracking cookies.
    </p>

    <h2>11. Last updated</h2>
    <p>23 July 2026.</p>
  </LegalLayout>
);

export default PrivacyPage;
