import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Check } from 'lucide-react';

import { assessments } from '../../shared/api/assessmentsClient';
import { MotionLoop } from '../../shared/motion';
import { AssessmentBrandGlyph } from './AssessmentBrandGlyph';

// Mirrors understanding_check.PER_QUESTION_SECONDS. The server stamps when it
// served each question and re-derives the elapsed time itself, so this is the
// candidate's visible clock, not the authority — a drifted or paused browser
// timer cannot buy extra time.
const FALLBACK_SECONDS = 75;

// One question at a time, no going back, and the answer submits the moment the
// timer hits zero. Everything about this surface is deliberately narrow: the
// candidate has just finished the real work, and this is a short comprehension
// pass over what they shipped, not a second exam.
export const UnderstandingCheck = ({
  assessmentId,
  assessmentToken,
  candidateSessionKey,
  lightMode = false,
  onFinished,
}) => {
  const [question, setQuestion] = useState(null);
  const [progress, setProgress] = useState({ answered: 0, total: 0 });
  const [selected, setSelected] = useState(null);
  const [secondsLeft, setSecondsLeft] = useState(FALLBACK_SECONDS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Tab switches during THIS question. Not a verdict on its own — recorded so a
  // recruiter reading a strong score can see how it was earned.
  const tabSwitchesRef = useRef(0);
  const startedAtRef = useRef(Date.now());
  // Guards the submit path against the timer and a click racing each other.
  const submittingRef = useRef(false);
  const finishedRef = useRef(false);

  const finish = useCallback(() => {
    if (finishedRef.current) return;
    finishedRef.current = true;
    onFinished?.();
  }, [onFinished]);

  const applyState = useCallback((data) => {
    setProgress({ answered: data?.answered || 0, total: data?.total || 0 });
    if (!data?.question) {
      setQuestion(null);
      finish();
      return;
    }
    setQuestion(data.question);
    setSelected(null);
    setSecondsLeft(data.question.seconds_allowed || FALLBACK_SECONDS);
    tabSwitchesRef.current = 0;
    startedAtRef.current = Date.now();
  }, [finish]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await assessments.getUnderstandingCheck(
          assessmentId,
          assessmentToken,
          candidateSessionKey,
        );
        if (!cancelled) applyState(response.data);
      } catch {
        // A check that cannot load must never trap the candidate on this
        // screen — their work is already submitted and safe. Fall through to
        // the confirmation screen and let grading proceed without it.
        if (!cancelled) finish();
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [assessmentId, assessmentToken, candidateSessionKey, applyState, finish]);

  const submitAnswer = useCallback(async (selectedIndex) => {
    if (submittingRef.current || !question) return;
    submittingRef.current = true;
    setError(null);
    try {
      const response = await assessments.answerUnderstandingCheck(
        assessmentId,
        {
          question_id: question.id,
          selected_index: selectedIndex,
          elapsed_ms: Math.max(0, Date.now() - startedAtRef.current),
          tab_switches: tabSwitchesRef.current,
        },
        assessmentToken,
        candidateSessionKey,
      );
      applyState(response.data);
    } catch (err) {
      if (err.response?.status === 409) {
        // Window closed underneath us (expired, or answered in another tab).
        finish();
        return;
      }
      setError("Couldn't record that answer. Try again.");
    } finally {
      submittingRef.current = false;
    }
  }, [assessmentId, assessmentToken, candidateSessionKey, question, applyState, finish]);

  // Per-question countdown. Hitting zero submits a skip rather than stalling:
  // an unanswered question is a result, and leaving the candidate stuck on a
  // dead timer would be worse than recording it.
  useEffect(() => {
    if (!question) return undefined;
    const id = window.setInterval(() => {
      setSecondsLeft((prev) => {
        if (prev <= 1) {
          window.clearInterval(id);
          void submitAnswer(null);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => window.clearInterval(id);
  }, [question, submitAnswer]);

  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') tabSwitchesRef.current += 1;
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, []);

  const shellClass = `taali-runtime ${lightMode ? 'taali-runtime-light' : 'taali-runtime-dark'} flex min-h-screen items-center justify-center bg-[var(--taali-runtime-bg)] px-6`;

  if (loading) {
    return (
      <div className={shellClass}>
        <div className="text-center">
          <MotionLoop as="div" kind="pulse" className="mx-auto mb-4 w-fit">
            <AssessmentBrandGlyph sizeClass="w-16 h-16" markSizeClass="w-[2.7rem] h-[2.7rem]" />
          </MotionLoop>
          <p className="font-mono text-sm text-[var(--taali-runtime-muted)]">
            Preparing a few questions about your work...
          </p>
        </div>
      </div>
    );
  }

  if (!question) return null;

  const urgent = secondsLeft <= 15;

  return (
    <div className={shellClass}>
      <div className="mx-auto w-full max-w-[680px] py-[64px]">
        <div className="mb-7 text-center text-[20px] font-extrabold tracking-[-0.01em] text-[var(--taali-runtime-text)]">
          taali<span className="text-[var(--purple)]">.</span>
        </div>

        <div className="mx-auto mb-6 flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <div className="grid h-7 w-7 place-items-center rounded-[9px] bg-[var(--purple)] text-white">
              <Check size={15} />
            </div>
            <span className="text-[13px] text-[var(--taali-runtime-muted)]">
              Work submitted &middot; question {question.index + 1} of {question.total}
            </span>
          </div>
          <span
            className={`font-mono text-[13px] tabular-nums ${
              urgent ? 'text-[var(--purple)]' : 'text-[var(--taali-runtime-muted)]'
            }`}
          >
            {String(Math.floor(secondsLeft / 60)).padStart(2, '0')}:
            {String(secondsLeft % 60).padStart(2, '0')}
          </span>
        </div>

        <div className="rounded-[16px] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] p-6">
          <h1 className="mb-1.5 font-[var(--font-display)] text-[19px] font-bold leading-[1.3] tracking-[-0.015em] text-[var(--taali-runtime-text)]">
            {question.prompt}
          </h1>
          <p className="mb-5 text-[13px] leading-[1.55] text-[var(--taali-runtime-muted)]">
            About the code you just submitted. One question at a time — you
            can&rsquo;t come back to this one.
          </p>

          <div className="flex flex-col gap-2.5">
            {question.options.map((option, index) => {
              const isSelected = selected === index;
              return (
                <button
                  key={option}
                  type="button"
                  onClick={() => setSelected(index)}
                  className={`rounded-[12px] border px-4 py-3 text-left text-[14px] leading-[1.5] transition-colors ${
                    isSelected
                      ? 'border-[var(--purple)] bg-[color-mix(in_srgb,var(--purple)_10%,transparent)] text-[var(--taali-runtime-text)]'
                      : 'border-[var(--taali-runtime-border)] bg-transparent text-[var(--taali-runtime-text)] hover:border-[var(--taali-runtime-muted)]'
                  }`}
                >
                  {option}
                </button>
              );
            })}
          </div>

          {error ? (
            <p className="mt-4 text-[13px] text-[var(--purple)]">{error}</p>
          ) : null}

          <button
            type="button"
            className="taali-btn taali-btn-primary taali-btn-lg mt-6 w-full"
            disabled={selected === null}
            onClick={() => submitAnswer(selected)}
          >
            {question.index + 1 === question.total ? 'Finish' : 'Next question'}
          </button>
        </div>

        <p className="mt-4 text-center text-[12.5px] leading-[1.55] text-[var(--taali-runtime-muted)]">
          Your submitted work is already saved. This is about how well you know
          it &mdash; it doesn&rsquo;t change what you shipped.
        </p>
      </div>
    </div>
  );
};

export default UnderstandingCheck;
