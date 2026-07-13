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
  // Honest disclosure of what was deep-checked vs the whole scored pool.
  expect(screen.getByText(/deep-checked 30 of 523 scored/)).toBeInTheDocument();
  expect(screen.getByText(/refine to narrow/)).toBeInTheDocument();
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
