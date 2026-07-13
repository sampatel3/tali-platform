import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { FormattedJobSpecSection, parseJobSpec } from './jobSpecFormatting';

const DATA_MODELER_SPEC = `# Data Modeler
**Application:** https://example.workable.com/jobs/DL34/candidates/new

## Description
DL34

DeepLight AI is a specialist AI and data consultancy with extensive experience implementing intelligent enterprise systems.

**Your responsibilities as the Data Modeler will include**

***Data Modeling***

Design conceptual, logical, and physical data models for Medallion architecture.

Implement dimensional modeling for analytical layers.

***Semantic Layer & Ontologies***

Design and implement a semantic layer for business-friendly data access.

Develop and maintain ontologies and taxonomies across domains.

Keep snake_case_field names stable across integrations.`;

describe('job spec formatting', () => {
  it('renders nested and single emphasis without leaking markdown markers', () => {
    const parsed = parseJobSpec(DATA_MODELER_SPEC, 'Data Modeler');
    const description = parsed.sections.find((section) => section.title === 'Description');

    const { container } = render(
      <FormattedJobSpecSection section={description} marker="01" />
    );

    const responsibilityHeading = screen.getByText('Your responsibilities as the Data Modeler will include');
    expect(responsibilityHeading.closest('.role-spec-subheading')).toBeInTheDocument();
    expect(responsibilityHeading.closest('strong')).toBeInTheDocument();

    const dataModeling = screen.getByText('Data Modeling');
    expect(dataModeling.closest('.role-spec-subheading')).toBeInTheDocument();
    expect(dataModeling.closest('strong')).toBeInTheDocument();
    expect(dataModeling.closest('em')).toBeInTheDocument();
    expect(container).not.toHaveTextContent('*Data Modeling*');
    expect(screen.getByText('Keep snake_case_field names stable across integrations.')).toBeInTheDocument();

    // An isolated Workable/reference code is prose, not an inferred one-item list.
    expect(screen.getByText('DL34').closest('p')).toBeInTheDocument();
    expect(screen.getByText('DL34').closest('li')).not.toBeInTheDocument();
  });

  it('preserves explicit bullets and only infers markerless lists with context', () => {
    const parsed = parseJobSpec(`## Description
Your responsibilities include;
Financial management
Delivery governance
Operational excellence

An isolated closing note.

## Requirements
- 8+ years of data engineering
• Executive stakeholder communication
1. Banking transformation experience`);

    const description = parsed.sections.find((section) => section.title === 'Description');
    const requirements = parsed.sections.find((section) => section.title === 'Requirements');
    const { container } = render(
      <>
        <FormattedJobSpecSection section={description} marker="01" />
        <FormattedJobSpecSection section={requirements} marker="02" />
      </>
    );

    const sections = container.querySelectorAll('.role-sec');
    const descriptionSection = sections[0];
    const requirementsSection = sections[1];
    expect(within(descriptionSection).getByText('Financial management').closest('li')).toBeInTheDocument();
    expect(within(descriptionSection).getByText('Delivery governance').closest('li')).toBeInTheDocument();
    expect(within(descriptionSection).getByText('Operational excellence').closest('li')).toBeInTheDocument();
    expect(within(descriptionSection).getByText('An isolated closing note.').closest('p')).toBeInTheDocument();
    expect(within(requirementsSection).getAllByRole('listitem')).toHaveLength(3);
  });

  it('keeps only http(s) source-posting URLs', () => {
    const safe = parseJobSpec('**Application:** https://example.workable.com/jobs/123\n\n## Description\nA role.');
    const unsafe = parseJobSpec('**Application:** javascript:alert(1)\n\n## Description\nA role.');

    expect(safe.meta.applyUrl).toBe('https://example.workable.com/jobs/123');
    expect(unsafe.meta.applyUrl).toBeUndefined();
  });
});
