import React, { useMemo, useState } from 'react';
import { ArrowLeft, Check, Mail } from 'lucide-react';

import { PageHero } from '../../shared/layout/PageHero';
import { Select } from '../../shared/ui/TaaliPrimitives';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import { getErrorMessage } from '../../shared/getErrorMessage';
import api from '../../shared/api/httpClient';

const TAALI_EMAIL = 'hello@taali.ai';

export const BespokeTaskRequestPage = ({ onNavigate, NavComponent = null }) => {
  const { showToast } = useToast();
  const { user } = useAuth();
  const [role, setRole] = useState('');
  const [seniority, setSeniority] = useState('mid');
  const [skills, setSkills] = useState('');
  const [scenario, setScenario] = useState('');
  const [deadline, setDeadline] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const canSubmit = role.trim().length > 0 && scenario.trim().length > 0;

  const mailtoHref = useMemo(() => {
    const subject = `Bespoke task request — ${role.trim() || 'role TBC'}`;
    const lines = [
      `Role / title: ${role.trim() || '(not specified)'}`,
      `Seniority: ${seniority}`,
      `Skills to assess: ${skills.trim() || '(not specified)'}`,
      `Deadline: ${deadline.trim() || '(none)'}`,
      '',
      'Scenario / context:',
      scenario.trim() || '(not specified)',
    ];
    const body = lines.join('\n');
    return `mailto:${TAALI_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  }, [role, seniority, skills, scenario, deadline]);

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!canSubmit || submitting) return;
    setSubmitting(true);
    try {
      // POST through the backend (forwarded to hello@ via Resend) so the
      // request doesn't depend on the machine having a mail client set up.
      await api.post('/public/bespoke-task', {
        role: role.trim(),
        seniority,
        skills: skills.trim(),
        scenario: scenario.trim(),
        deadline: deadline.trim(),
        // So Taali has a reply address — the success copy promises a reply.
        requester_email: user?.email || '',
      });
      setSubmitted(true);
    } catch (err) {
      showToast(getErrorMessage(err, "Couldn't send your request. Email us directly instead."), 'error');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="tasks" onNavigate={onNavigate} /> : null}
      <div className="page">
        <div className="bespoke-task-page">
          <button
            type="button"
            className="taali-text-btn bespoke-back"
            onClick={() => onNavigate?.('tasks')}
          >
            <ArrowLeft size={14} />
            Back to task library
          </button>

          <PageHero
            kicker="REQUEST A BESPOKE TASK"
            title={<>Don't see your <em>role</em>?</>}
            subtitle="Tell us what you're hiring for. Our engineers build a hands-on assessment in 3–5 working days. You get a draft to approve before any candidate sees it."
          />

          {submitted ? (
            <div className="bespoke-form" role="status">
              <div className="bespoke-field" style={{ alignItems: 'center', textAlign: 'center', gap: 12 }}>
                <Check size={28} style={{ color: 'var(--purple)' }} />
                <h2 style={{ margin: 0 }}>Request sent.</h2>
                <p className="bespoke-actions-hint" style={{ maxWidth: '40ch' }}>
                  Thanks — we&apos;ll be in touch from {TAALI_EMAIL} within a couple of working days to confirm scope.
                </p>
                <button type="button" className="btn btn-outline btn-sm" onClick={() => onNavigate?.('tasks')}>
                  Back to task library
                </button>
              </div>
            </div>
          ) : (
          <form className="bespoke-form" onSubmit={handleSubmit}>
            <label className="bespoke-field">
              <span className="bespoke-label">Role / title <em>required</em></span>
              <input
                type="text"
                value={role}
                onChange={(event) => setRole(event.target.value)}
                placeholder="e.g. Senior Backend Engineer"
                required
              />
            </label>

            <label className="bespoke-field">
              <span className="bespoke-label">Seniority</span>
              <Select
                value={seniority}
                onChange={(event) => setSeniority(event.target.value)}
              >
                <option value="junior">Junior</option>
                <option value="mid">Mid</option>
                <option value="senior">Senior</option>
                <option value="staff">Staff / Principal</option>
              </Select>
            </label>

            <label className="bespoke-field">
              <span className="bespoke-label">Skills to assess</span>
              <input
                type="text"
                value={skills}
                onChange={(event) => setSkills(event.target.value)}
                placeholder="e.g. Python, FastAPI, Postgres, system design"
              />
            </label>

            <label className="bespoke-field">
              <span className="bespoke-label">Scenario / context <em>required</em></span>
              <textarea
                rows={6}
                value={scenario}
                onChange={(event) => setScenario(event.target.value)}
                placeholder="What problem should the candidate work through? Any real systems, data, or constraints we should mirror?"
                required
              />
            </label>

            <label className="bespoke-field">
              <span className="bespoke-label">Deadline (optional)</span>
              <input
                type="text"
                value={deadline}
                onChange={(event) => setDeadline(event.target.value)}
                placeholder="e.g. need it ready by 15 May"
              />
            </label>

            <div className="bespoke-actions">
              <button type="submit" className="btn btn-purple" disabled={!canSubmit || submitting}>
                <Mail size={14} />
                {submitting ? 'Sending…' : 'Send to Taali'}
              </button>
              <span className="bespoke-actions-hint">
                We&apos;ll email you back from {TAALI_EMAIL}. Prefer email?{' '}
                <a href={mailtoHref} style={{ color: 'var(--purple)' }}>Email us directly</a>.
              </span>
            </div>
          </form>
          )}
        </div>
      </div>
    </div>
  );
};

export default BespokeTaskRequestPage;
