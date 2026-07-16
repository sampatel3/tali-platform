import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  CANDIDATE_CV_ACCEPT,
  CandidateSheet,
  isSupportedCandidateCvFile,
} from './CandidateSheet';

const role = { id: 42, name: 'Platform Engineer', job_spec_filename: 'role.pdf' };

describe('CandidateSheet CV policy', () => {
  it('accepts PDF/DOCX extensions and rejects the unsupported legacy DOC extension', () => {
    expect(isSupportedCandidateCvFile(new File(['pdf'], 'resume.PDF'))).toBe(true);
    expect(isSupportedCandidateCvFile(new File(['docx'], 'resume.docx'))).toBe(true);
    expect(isSupportedCandidateCvFile(new File(['doc'], 'resume.doc'))).toBe(false);
  });

  it('uses the same PDF/DOCX policy for picker and drag/drop', () => {
    render(
      <CandidateSheet
        open
        role={role}
        saving={false}
        error=""
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const input = screen.getByLabelText('Upload candidate CV');
    const dropTarget = input.closest('label');
    expect(input).toHaveAttribute('accept', CANDIDATE_CV_ACCEPT);
    expect(input.accept).toBe('.pdf,.docx');

    fireEvent.drop(dropTarget, {
      dataTransfer: { files: [new File(['doc'], 'legacy-resume.doc')] },
    });
    expect(screen.getByRole('alert')).toHaveTextContent('Upload a PDF or DOCX file.');
    expect(screen.queryByText('legacy-resume.doc')).not.toBeInTheDocument();

    fireEvent.drop(dropTarget, {
      dataTransfer: { files: [new File(['docx'], 'supported-resume.docx')] },
    });
    expect(screen.getByText('supported-resume.docx')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
