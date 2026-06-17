import { render, screen } from '@testing-library/react';

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
