import React from 'react';

import { LegalLayout } from './LegalLayout';

// Verified 2026-07-23. Facts are fixed content — do not edit without
// re-verifying each subprocessor's function, location, and transfer mechanism.
const SUBPROCESSORS = [
  {
    name: 'Anthropic (Claude)',
    function: 'AI analysis of CVs, role criteria, assessments and chat',
    location: 'US',
    transfer: 'EU–US Data Privacy Framework + UK Extension (certified)',
  },
  {
    name: 'Railway',
    function: 'Application hosting, database, cache (us-east4)',
    location: 'US',
    transfer: 'EU–US DPF + UK Extension + Swiss–US DPF (certified)',
  },
  {
    name: 'Vercel',
    function: 'Web frontend hosting / CDN',
    location: 'US (global edge)',
    transfer: 'EU–US DPF + UK Extension + Swiss–US DPF (certified)',
  },
  {
    name: 'Resend',
    function: 'Transactional email',
    location: 'US',
    transfer: 'EU–US DPF + UK Extension (certified)',
  },
  {
    name: 'Stripe',
    function: 'Billing (customer billing data only — no candidate data)',
    location: 'US',
    transfer: 'EU–US DPF + UK Extension (certified); SCCs as fallback',
  },
  {
    name: 'GitHub (Microsoft)',
    function: 'Assessment repositories',
    location: 'US',
    transfer: 'EU–US DPF + UK Extension + Swiss–US DPF (certified)',
  },
  {
    name: 'E2B',
    function: 'Assessment sandbox runtime',
    location: 'US',
    transfer: 'Standard Contractual Clauses via DPA',
  },
  {
    name: 'Fireflies.ai',
    function: 'Interview transcription (only where the customer connects it)',
    location: 'US',
    transfer: 'EU–US DPF (certified); SCCs as fallback per its DPA',
  },
  {
    name: 'Neo4j (Aura)',
    function: 'Candidate evidence graph (only where enabled)',
    location: 'US',
    transfer: 'EU–US DPF + UK Extension + Swiss–US DPF (certified)',
  },
  {
    name: 'Voyage AI (MongoDB)',
    function: 'Embeddings for the evidence graph (only where enabled)',
    location: 'US',
    transfer: 'EU–US DPF + UK Extension + Swiss–US DPF (MongoDB group certification)',
  },
];

export const SubprocessorsPage = () => (
  <LegalLayout kicker="Legal" title="Subprocessors" updated="23 July 2026">
    <p className="legal-lede">
      Taali uses the following subprocessors to deliver the service. All are bound by
      data-processing agreements. Transfer mechanism shown is for UK/EU personal data.
      Questions: <a href="mailto:hello@taali.ai">hello@taali.ai</a>. Last updated 23 July 2026.
    </p>

    <div className="legal-table-wrap">
      <table className="legal-table">
        <thead>
          <tr>
            <th scope="col">Subprocessor</th>
            <th scope="col">Function</th>
            <th scope="col">Location</th>
            <th scope="col">Transfer mechanism</th>
          </tr>
        </thead>
        <tbody>
          {SUBPROCESSORS.map((sub) => (
            <tr key={sub.name}>
              <td>{sub.name}</td>
              <td>{sub.function}</td>
              <td>{sub.location}</td>
              <td>{sub.transfer}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>

    <p>
      We will update this page before adding or replacing a subprocessor. Customers with a
      signed DPA are notified of changes per its terms.
    </p>
  </LegalLayout>
);

export default SubprocessorsPage;
