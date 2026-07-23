import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  NO_AV_RECORDING_SENTENCE,
  PROCTORING_FLAG,
  SESSION_TRANSCRIPT_ONLY_FLAG,
  WORKSPACE_SIGNAL_CAVEAT,
  WORKSPACE_SIGNAL_EVENTS,
  WORKSPACE_SIGNAL_GROUPS,
  WORKSPACE_SIGNAL_SENTENCE,
  WORKSPACE_SIGNAL_SUMMARY,
  WORKSPACE_SIGNALS_FLAG,
  WORK_RECORD_SENTENCE,
  recordingFlagLabel,
} from './sessionDisclosure';

// Repo paths, resolved from this file (frontend/src/shared/assessment/).
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');
const read = (rel) => fs.readFileSync(path.join(repoRoot, rel), 'utf8');

const integrityRoutes = read('backend/app/domains/assessments_runtime/candidate_integrity_routes.py');
const workspaceSecuritySource = read('frontend/src/features/assessment_runtime/AssessmentWorkspaceSecurity.jsx');
const runtimeSource = read('frontend/src/features/assessment_runtime/AssessmentPageContent.jsx');

// The server allow-list is the authoritative gate: nothing reaches a candidate's
// timeline unless it appears in one of these frozensets.
const pythonFrozenset = (name) => {
  const block = integrityRoutes.split(`${name} = frozenset(`)[1];
  expect(block, `${name} not found in candidate_integrity_routes.py`).toBeTruthy();
  return new Set(
    Array.from(block.split(')')[0].matchAll(/"([a-z0-9_]+)"/g), (match) => match[1]),
  );
};

const serverAdvisoryEvents = pythonFrozenset('_ADVISORY_INTEGRITY_EVENT_TYPES');
const serverEngagementEvents = pythonFrozenset('_DEDUPED_RUNTIME_EVENT_TYPES');

const sorted = (values) => Array.from(values).sort();

describe('assessment session disclosure', () => {
  it('discloses exactly the advisory signals the server will record', () => {
    // If this fails, a workspace signal was added or removed on one side only.
    // Fix the disclosure copy — do not relax the assertion.
    expect(sorted(WORKSPACE_SIGNAL_EVENTS)).toEqual(sorted(serverAdvisoryEvents));
  });

  it('keeps every disclosed signal wired to a real emit site in the runtime', () => {
    const sources = `${workspaceSecuritySource}\n${runtimeSource}`;
    for (const eventType of WORKSPACE_SIGNAL_EVENTS) {
      expect(sources, `${eventType} is disclosed but never emitted`).toContain(`'${eventType}'`);
    }
  });

  it('classifies the engagement beacons as work-record, not advisory signals', () => {
    // A new beacon must be consciously classified: either it is part of the work
    // record (extend WORK_RECORD_ITEMS) or it is an advisory signal (extend
    // WORKSPACE_SIGNAL_GROUPS). Adding one silently should break this test.
    expect(sorted(serverEngagementEvents)).toEqual(['file_opened', 'runtime_loaded']);
  });

  it('lists each signal exactly once, in a named group', () => {
    expect(new Set(WORKSPACE_SIGNAL_EVENTS).size).toBe(WORKSPACE_SIGNAL_EVENTS.length);
    for (const group of WORKSPACE_SIGNAL_GROUPS) {
      expect(group.label.trim()).not.toBe('');
      expect(group.events.length).toBeGreaterThan(0);
      expect(WORKSPACE_SIGNAL_SUMMARY).toContain(group.label);
    }
  });

  it('covers the tab-focus metrics candidates would not otherwise expect', () => {
    // The specific gap this disclosure closes: with proctoring off the workspace
    // still logs when the assessment tab loses focus — the signal that catches
    // screenshot-and-ask-an-external-model, which a closed workspace cannot.
    expect(WORKSPACE_SIGNAL_EVENTS).toContain('visibility_hidden');
    expect(WORKSPACE_SIGNAL_EVENTS).toContain('fullscreen_exit');
    expect(WORKSPACE_SIGNAL_SENTENCE).toMatch(/when the tab loses focus/);
  });

  it('keeps the workspace banner framing these as advisory', () => {
    expect(workspaceSecuritySource).toContain('activity signals are advisory');
  });

  it('never claims transcript-only while the workspace layer is recording', () => {
    expect(recordingFlagLabel({ workspaceProtectionEnabled: true })).toBe(WORKSPACE_SIGNALS_FLAG);
    expect(recordingFlagLabel({ proctoringEnabled: true, workspaceProtectionEnabled: true }))
      .toBe(PROCTORING_FLAG);
    expect(recordingFlagLabel({ workspaceProtectionEnabled: false }))
      .toBe(SESSION_TRANSCRIPT_ONLY_FLAG);
    expect(recordingFlagLabel()).toBe(SESSION_TRANSCRIPT_ONLY_FLAG);
  });

  it('keeps the no-AV promise the deck and legal pages also make', () => {
    expect(NO_AV_RECORDING_SENTENCE).toMatch(/do not record your screen, camera, or microphone/);
    const deck = read('frontend/public/_deck/index.html');
    expect(deck).toContain('No webcam, no screen or microphone recording, no lockdown browser.');
    expect(read('frontend/src/features/legal/PrivacyPage.jsx'))
      .toContain('WORKSPACE_SIGNAL_SUMMARY');
  });

  it('surfaces both disclosure layers on the candidate-facing surfaces', () => {
    for (const rel of [
      'frontend/src/features/assessment_runtime/AssessmentPageContent.jsx',
      'frontend/src/features/assessment_runtime/CandidateWelcomePage.jsx',
    ]) {
      const source = read(rel);
      expect(source, `${rel} must show the work record`).toContain('WORK_RECORD_SENTENCE');
      expect(source, `${rel} must show the workspace signals`).toContain('WORKSPACE_SIGNAL_SENTENCE');
    }
    expect(WORK_RECORD_SENTENCE).toMatch(/prompts/);
  });

  it('never claims recording is limited to the transcript on a candidate surface', () => {
    // "The session transcript is reviewed — not your screen, mic, or camera" is
    // true and stays: nothing scores or grades on the activity metrics. What is
    // banned is a claim that the transcript is the ONLY thing recorded, which is
    // what the old footer flag asserted while metrics were being logged.
    const falseClaim = /transcript only|only the (session )?transcript|nothing else is (recorded|logged)/i;
    for (const rel of [
      'frontend/src/features/assessment_runtime/AssessmentPageContent.jsx',
      'frontend/src/features/assessment_runtime/CandidateWelcomePage.jsx',
    ]) {
      const hardcoded = read(rel).match(falseClaim);
      expect(hardcoded, `${rel} hardcodes a transcript-only claim: ${hardcoded?.[0]}`).toBeNull();
    }
  });

  it('states what the metrics are for and that they carry no content', () => {
    expect(WORKSPACE_SIGNAL_SENTENCE).toMatch(/keep the assessment fair/);
    expect(WORKSPACE_SIGNAL_CAVEAT).toMatch(/not the content of what you type or copy/);
    // The server stores a surface name and a bounded count, never the text.
    expect(integrityRoutes).toContain('payload["source"] = data.source');
    expect(integrityRoutes).toContain('payload["length"] = data.length');
    expect(integrityRoutes).not.toMatch(/payload\["(text|content|clipboard)"\]/);
  });
});
