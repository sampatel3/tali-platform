import { render, screen, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';

import CandidateEvidenceCard from './CandidateEvidenceCard';

// Build a one-candidate card payload with the given criteria rows.
function cardWith(criteria) {
  return {
    candidates: [
      {
        application_id: 1,
        rank: 1,
        candidate_name: 'Saurabh Zambare',
        taali_score: 78,
        criteria,
      },
    ],
    spec: {},
    rank_by: 'taali',
    shown: 1,
  };
}

test('a grounded MET criterion shows its verbatim quote', () => {
  render(
    <CandidateEvidenceCard
      data={cardWith([
        {
          criterion: 'banking domain experience',
          status: 'met',
          grounded: true,
          evidence: [{ quote: 'Senior Data Engineer at Virtusa ENBD', source: 'cv' }],
          note: '',
        },
      ])}
    />,
  );
  expect(screen.getByText('Met')).toBeInTheDocument();
  expect(screen.getByText(/Virtusa ENBD/)).toBeInTheDocument();
});

test('a genuine MISSING criterion shows the no-evidence line', () => {
  render(
    <CandidateEvidenceCard
      data={cardWith([
        {
          criterion: 'banking domain experience',
          status: 'missing',
          grounded: false,
          evidence: [],
          note: '',
        },
      ])}
    />,
  );
  expect(screen.getByText('Missing')).toBeInTheDocument();
  expect(
    screen.getByText(/No supporting evidence in the CV or notes\./),
  ).toBeInTheDocument();
});

test('a self-referential "Taali score >= N" criterion is decided from the score, not the CV', () => {
  // Back-compat for snapshots minted before the backend decided these: the
  // grounder can't find a "Taali score" quote in the CV, so the stored verdict
  // is "missing" — but the candidate scored 78, so it must render as Met.
  render(
    <CandidateEvidenceCard
      data={cardWith([
        {
          criterion: 'Taali score >= 60',
          status: 'missing',
          grounded: false,
          evidence: [],
          note: '',
        },
      ])}
    />,
  );
  expect(screen.getByText('Met')).toBeInTheDocument();
  expect(screen.getByText(/Taali score 78/)).toBeInTheDocument();
  // The misleading "no supporting evidence" copy must NOT appear.
  expect(screen.queryByText(/No supporting evidence/)).toBeNull();
});

test('a "Taali score >= N" criterion the candidate misses renders as Not met', () => {
  render(
    <CandidateEvidenceCard
      data={cardWith([
        { criterion: 'Taali score >= 90', status: 'missing', grounded: false, evidence: [], note: '' },
      ])}
    />,
  );
  expect(screen.getByText('Not met')).toBeInTheDocument();
  expect(screen.getByText(/below the ≥ 90 threshold/)).toBeInTheDocument();
});

test('rediscovery mode shows the requirement-fit framing and screened/capped disclosure', () => {
  render(
    <CandidateEvidenceCard
      data={{
        mode: 'rediscovery',
        total_matched: 523,
        screened: 30,
        capped: true,
        shown: 1,
        spec: {},
        candidates: [
          {
            application_id: 1,
            rank: 1,
            candidate_name: 'Saurabh Zambare',
            taali_score: 78,
            criteria: [
              {
                criterion: 'banking domain experience',
                status: 'met',
                grounded: true,
                evidence: [{ quote: 'Senior Data Engineer at ENBD', source: 'cv' }],
                note: '',
              },
            ],
          },
        ],
      }}
    />,
  );
  expect(screen.getByText(/Rediscovery/)).toBeInTheDocument();
  expect(screen.getByText(/ranked by fit to your requirement/)).toBeInTheDocument();
  // Honest disclosure of what was deep-checked vs database retrieval.
  expect(screen.getByText(/30\/523 candidates deep-checked/)).toBeInTheDocument();
  expect(screen.getByText('Partial evidence')).toBeInTheDocument();
  // The grounded verdict + the candidate's score still render.
  expect(screen.getByText('Met')).toBeInTheDocument();
  expect(screen.getByText('Taali 78')).toBeInTheDocument();
});

test('an ERROR criterion shows "couldn’t verify" — NOT the false no-evidence line', () => {
  // The Saurabh bug: a failed/timed-out check must never read as a data gap.
  render(
    <CandidateEvidenceCard
      data={cardWith([
        {
          criterion: 'banking domain experience',
          status: 'error',
          grounded: false,
          evidence: [],
          note: 'Evidence check failed — will retry.',
        },
      ])}
    />,
  );
  expect(screen.getByText('Unverified')).toBeInTheDocument();
  expect(screen.getByText(/Couldn’t verify/)).toBeInTheDocument();
  // The misleading "no supporting evidence" copy must NOT appear for a failed check.
  expect(screen.queryByText(/No supporting evidence/)).toBeNull();
});

test('rediscovery stays focused on evidence and does not expose sourcing actions', () => {
  render(
    <CandidateEvidenceCard
      data={{
        mode: 'rediscovery',
        shown: 2,
        total_matched: 10,
        rank_by: 'fit',
        role_id: 7,
        candidates: [
          { application_id: 101, rank: 1, candidate_name: 'A', criteria: [] },
          { application_id: 102, rank: 2, candidate_name: 'B', criteria: [] },
        ],
        spec: { query: 'senior backend' },
      }}
    />,
  );

  expect(screen.queryByRole('button', { name: /start outreach/i })).not.toBeInTheDocument();
  expect(screen.queryByText(/campaign/i)).not.toBeInTheDocument();
});

test('a public snapshot without an internal URL renders a name, not a dead link', () => {
  render(<CandidateEvidenceCard data={cardWith([])} />);

  expect(screen.getByText('Saurabh Zambare')).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /Saurabh Zambare/ })).toBeNull();
});

test('labels a fully covered evidence run as a grounded report', () => {
  render(
    <CandidateEvidenceCard
      data={{
        ...cardWith([{
          criterion: 'Led a platform launch',
          status: 'met',
          grounded: true,
          evidence: [{ quote: 'Led the launch across three regions.', source: 'cv' }],
        }]),
        database_matches: 1,
        criteria_requested: ['Led a platform launch'],
        criteria_checked: ['Led a platform launch'],
        criteria_unchecked: [],
        deep_checked: 1,
        evidence_succeeded: 1,
        qualified: 1,
        capped: false,
        evidence_model: 'grounder-v1',
        report_url: '/report/grounded-shortlist',
      }}
    />,
  );

  expect(screen.getByText('Evidence complete')).toBeInTheDocument();
  expect(screen.getByText('Grounded report')).toBeInTheDocument();
  expect(screen.getAllByText(/1\/1 candidates deep-checked/)).toHaveLength(2);
  expect(screen.getAllByText(/1\/1 criteria checked/)).toHaveLength(2);
  expect(screen.getAllByText(/1 fully met checked criteria/)).toHaveLength(2);
  expect(
    screen.getByRole('link', { name: 'Open shareable grounded candidate report' }),
  ).toHaveAttribute('href', '/report/grounded-shortlist');
});

test('labels capped, failed, and unchecked evidence as partial and names the gaps', () => {
  render(
    <CandidateEvidenceCard
      data={{
        ...cardWith([{
          criterion: 'Led a platform launch',
          status: 'met',
          grounded: true,
          evidence: [{ quote: 'Led the launch across three regions.', source: 'cv' }],
        }]),
        database_matches: 8,
        criteria_requested: ['Led a platform launch', 'Managed an on-call team'],
        criteria_checked: ['Led a platform launch'],
        criteria_unchecked: ['Managed an on-call team'],
        deep_checked: 3,
        evidence_succeeded: 2,
        qualified: 1,
        capped: true,
        warnings: [{ message: 'Verification stopped at the bounded evidence window.' }],
        report_url: '/report/partial-shortlist',
      }}
    />,
  );

  expect(screen.getByText('Partial evidence')).toBeInTheDocument();
  expect(screen.getByText('Partially grounded report')).toBeInTheDocument();
  expect(screen.getAllByText(/3\/8 candidates deep-checked/)).toHaveLength(2);
  expect(screen.getAllByText(/2\/3 evidence checks succeeded/)).toHaveLength(2);
  expect(screen.getAllByText(/1\/2 criteria checked/)).toHaveLength(2);
  expect(screen.getByText(/Unchecked criteria:/).parentElement).toHaveTextContent(
    'Unchecked criteria: Managed an on-call team',
  );
  expect(screen.getByText('Verification stopped at the bounded evidence window.')).toBeInTheDocument();
  expect(screen.queryByText('Grounded report')).not.toBeInTheDocument();
  expect(
    screen.getByRole('link', { name: 'Open shareable partially grounded candidate report' }),
  ).toHaveAttribute('href', '/report/partial-shortlist');
});

test('labels a requested but unavailable evidence pass as unverified', () => {
  render(
    <CandidateEvidenceCard
      data={{
        ...cardWith([]),
        database_matches: 8,
        criteria_requested: ['Managed an on-call team'],
        criteria_checked: ['Managed an on-call team'],
        criteria_unchecked: [],
        deep_checked: 0,
        evidence_succeeded: 0,
        qualified: null,
        capped: true,
        warnings: [{ message: 'Grounding unavailable; not filtered.' }],
        report_url: '/report/unverified-shortlist',
      }}
    />,
  );

  expect(screen.getByText('Evidence unavailable')).toBeInTheDocument();
  expect(screen.getByText('Unverified shortlist')).toBeInTheDocument();
  expect(screen.getAllByText(/0\/8 candidates deep-checked/)).toHaveLength(2);
  expect(screen.getAllByText(/evidence unavailable for 1 criterion/)).toHaveLength(2);
  expect(screen.getByText('Grounding unavailable; not filtered.')).toBeInTheDocument();
  expect(screen.queryByText(/fully met checked criteria/)).not.toBeInTheDocument();
  expect(
    screen.getByRole('link', { name: 'Open shareable candidate report with unverified evidence' }),
  ).toHaveAttribute('href', '/report/unverified-shortlist');
});

test('labels a score-only result without implying an evidence pass', () => {
  render(
    <CandidateEvidenceCard
      data={{
        ...cardWith([]),
        criteria_requested: [],
        criteria_checked: [],
        criteria_unchecked: [],
        deep_checked: 0,
        evidence_succeeded: 0,
        qualified: null,
        evidence_basis: 'score_only',
        report_url: '/report/score-only-shortlist',
      }}
    />,
  );

  expect(screen.getByText('Score only')).toBeInTheDocument();
  expect(screen.getByText('Score-ranked shortlist')).toBeInTheDocument();
  expect(screen.getAllByText('Ranked by Taali fit; no qualitative evidence check')).toHaveLength(2);
  expect(
    screen.getByRole('link', { name: 'Open shareable score-ranked candidate report' }),
  ).toHaveAttribute('href', '/report/score-only-shortlist');
});

test('distinguishes complete reused role evidence from a fresh grounding pass', () => {
  render(
    <CandidateEvidenceCard
      data={{
        ...cardWith([{
          criterion: 'Platform ownership',
          status: 'met',
          grounded: true,
          evidence: [{ quote: 'Owned the platform roadmap.', source: 'role_requirement' }],
        }]),
        criteria_requested: [],
        criteria_checked: [],
        criteria_unchecked: [],
        deep_checked: 0,
        evidence_succeeded: 1,
        evidence_basis: 'stored_role_requirements',
        evidence_reused: 1,
        report_url: '/report/stored-evidence-shortlist',
      }}
    />,
  );

  expect(screen.getByText('Stored evidence')).toBeInTheDocument();
  expect(screen.getByText('Evidence-backed report')).toBeInTheDocument();
  expect(screen.getAllByText('1/1 candidates with stored role evidence')).toHaveLength(2);
  expect(
    screen.getByRole('link', { name: 'Open shareable evidence-backed candidate report' }),
  ).toHaveAttribute('href', '/report/stored-evidence-shortlist');
});

test('does not label a mixed stored-evidence shortlist as fully grounded', () => {
  render(
    <CandidateEvidenceCard
      data={{
        candidates: [
          {
            application_id: 1,
            rank: 1,
            candidate_name: 'Candidate with evidence',
            criteria: [{
              criterion: 'Platform ownership',
              status: 'met',
              grounded: true,
              evidence: [{ quote: 'Owned the platform roadmap.', source: 'role_requirement' }],
            }],
          },
          {
            application_id: 2,
            rank: 2,
            candidate_name: 'Score-only candidate',
            criteria: [],
          },
        ],
        shown: 2,
        rank_by: 'taali',
        criteria_requested: [],
        criteria_checked: [],
        criteria_unchecked: [],
        deep_checked: 0,
        evidence_succeeded: 1,
        evidence_basis: 'stored_role_requirements',
        evidence_reused: 1,
        report_url: '/report/mixed-evidence-shortlist',
      }}
    />,
  );

  expect(screen.getByText('Partial evidence')).toBeInTheDocument();
  expect(screen.getByText('Partially grounded report')).toBeInTheDocument();
  expect(screen.getAllByText('1/2 candidates with stored role evidence')).toHaveLength(2);
  expect(screen.queryByText('Grounded report')).not.toBeInTheDocument();
});

test('does not show the report affordance without a report_url', () => {
  render(<CandidateEvidenceCard data={cardWith([])} />);

  expect(screen.queryByText('Grounded report')).not.toBeInTheDocument();
  expect(
    screen.queryByRole('link', { name: 'Open shareable grounded candidate report' }),
  ).not.toBeInTheDocument();
});
