import React from 'react';

import { LegalLayout } from './LegalLayout';
import { PageLink } from '../../shared/ui/PageLink';

// NOTE: Pre-counsel draft — review pending before publication. Bracketed items
// (liability cap, governing law) mark commercial/legal decisions that still
// need to be confirmed by the business and reviewed by counsel.

export const TermsPage = () => (
  <LegalLayout kicker="Legal" title="Terms of Service" updated="23 July 2026">
    <p className="legal-lede">
      These terms govern your use of Taali. By creating an account or using the service, you
      agree to them on behalf of your organisation.
    </p>

    <h2>1. The service</h2>
    <p>
      Taali is an AI-assisted hiring platform. It helps recruiting teams screen applicants,
      run AI-native work-sample assessments, and reach hiring decisions. Taali produces
      recommendations against criteria you define; human decision control stays with you.
      Every recommendation is queued for review, and no advance or reject action is taken
      without a person on your team confirming it.
    </p>

    <h2>2. Accounts and acceptable use</h2>
    <p>
      You are responsible for your account, for keeping credentials secure, and for the
      activity of users you invite. You agree to use Taali only for legitimate recruiting, in
      compliance with applicable employment, data-protection, and anti-discrimination law, and
      not to misuse the service — for example by attempting to disrupt it, reverse-engineer
      it, or access data you are not authorised to access.
    </p>

    <h2>3. Billing</h2>
    <p>
      Taali is billed on a usage basis in credits, charged through our payment processor,
      Stripe. Unless otherwise agreed in writing, charges are payable in advance and are
      non-refundable except where required by law. You are responsible for any taxes that
      apply to your use of the service.
    </p>

    <h2>4. Customer data and data processing</h2>
    <p>
      As between the parties, you own the data you and your candidates put into Taali. You
      grant us the rights needed to host and process that data to provide the service. Where
      Taali processes personal data on your behalf, a Data Processing Agreement is available
      at <a href="mailto:hello@taali.ai">hello@taali.ai</a> and forms part of these terms once
      executed. How we handle personal data is described in our{' '}
      <PageLink page="privacy">Privacy Notice</PageLink>, and the subprocessors we use are
      listed at <PageLink page="subprocessors">taali.ai/subprocessors</PageLink>.
    </p>

    <h2>5. Assessments</h2>
    <p>
      Where you invite candidates to complete a work-sample assessment, you are responsible
      for how the assessment is used in your hiring process and for telling candidates what to
      expect. Candidates must complete assessments themselves and in good faith. Taali records
      the assessment session — prompts, AI responses, file changes, and validation runs — to
      produce the result; it does not record screen, camera, or microphone.
    </p>

    <h2>6. Intellectual property</h2>
    <p>
      Taali, including the platform, its software, and its brand, is and remains our property.
      These terms do not transfer any of our intellectual property to you beyond the right to
      use the service during your subscription. Your data and your brand remain yours.
    </p>

    <h2>7. Confidentiality</h2>
    <p>
      Each party may receive confidential information from the other. Each party agrees to use
      the other&apos;s confidential information only to perform under these terms and to
      protect it with at least reasonable care, except where disclosure is required by law.
    </p>

    <h2>8. Availability</h2>
    <p>
      We work to keep Taali available and performant on a best-efforts basis, but we do not
      commit to a specific uptime service level unless one is agreed with you in writing. We
      may occasionally need to suspend the service for maintenance or to protect its security
      and integrity.
    </p>

    <h2>9. Limitation of liability</h2>
    <p>
      To the fullest extent permitted by law, neither party is liable for indirect,
      incidental, or consequential losses, and each party&apos;s total liability arising out
      of or relating to these terms is{' '}
      <strong>[capped at the fees you paid to Taali in the 12 months before the event giving
      rise to the claim]</strong>. Nothing in these terms limits liability that cannot be
      limited by law.
    </p>

    <h2>10. Termination</h2>
    <p>
      Either party may terminate for material breach that is not cured within a reasonable
      period after notice. You may stop using Taali and close your account at any time. On
      termination, your right to use the service ends; we will make your data available for
      export for a reasonable period and then delete it in line with our{' '}
      <PageLink page="privacy">Privacy Notice</PageLink> and any Data Processing Agreement.
    </p>

    <h2>11. Changes to these terms</h2>
    <p>
      We may update these terms from time to time. If we make a material change, we will take
      reasonable steps to let you know. Your continued use of Taali after a change takes effect
      means you accept the updated terms.
    </p>

    <h2>12. Governing law</h2>
    <p>
      These terms are governed by the laws of{' '}
      <strong>[England and Wales — pending confirmation by counsel]</strong>, and the courts of
      that jurisdiction have exclusive jurisdiction over any dispute, except where mandatory
      local law provides otherwise.
    </p>

    <hr />
    <p>
      Questions about these terms? Email <a href="mailto:hello@taali.ai">hello@taali.ai</a>.
    </p>
  </LegalLayout>
);

export default TermsPage;
