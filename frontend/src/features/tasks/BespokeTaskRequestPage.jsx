import React, { useMemo, useState } from 'react';
import { ArrowLeft, Mail } from 'lucide-react';

const TAALI_EMAIL = 'hello@taali.ai';

export const BespokeTaskRequestPage = ({ onNavigate, NavComponent = null }) => {
  const [role, setRole] = useState('');
  const [seniority, setSeniority] = useState('mid');
  const [skills, setSkills] = useState('');
  const [scenario, setScenario] = useState('');
  const [deadline, setDeadline] = useState('');

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

  const handleSubmit = (event) => {
    event.preventDefault();
    if (!canSubmit) return;
    window.location.assign(mailtoHref);
  };

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="tasks" onNavigate={onNavigate} /> : null}
      <div className="page">
        <div className="bespoke-task-page">
          <button
            type="button"
            className="bespoke-back"
            onClick={() => onNavigate?.('tasks')}
          >
            <ArrowLeft size={14} />
            Back to task library
          </button>

          <div className="bespoke-hero">
            <div className="kicker">TASK LIBRARY · BESPOKE REQUEST</div>
            <h1>Tell us about the task you need.</h1>
            <p>
              Share the role, the skills you want to probe, and the scenario you have in mind.
              Taali&apos;s engineering team will design a bespoke assessment for you, usually within
              a few working days.
            </p>
          </div>

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
              <select
                value={seniority}
                onChange={(event) => setSeniority(event.target.value)}
              >
                <option value="junior">Junior</option>
                <option value="mid">Mid</option>
                <option value="senior">Senior</option>
                <option value="staff">Staff / Principal</option>
              </select>
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
              <button type="submit" className="btn btn-purple" disabled={!canSubmit}>
                <Mail size={14} />
                Send to Taali
              </button>
              <span className="bespoke-actions-hint">
                Opens your email client with the request pre-filled. Replies come from {TAALI_EMAIL}.
              </span>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
};

export default BespokeTaskRequestPage;
