import React from 'react';

const screenClassName = 'overflow-hidden rounded-[28px] border border-[var(--line)] bg-[var(--bg-2)] shadow-[var(--shadow-lg)]';
const monoClassName = 'font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.12em]';

export const SHOWCASE_MOMENTS = [
  {
    kicker: '// MOMENT 01',
    title: 'They scope before they ask.',
    body: 'The candidate opened with "highest-risk launch blockers first, then propose the smallest safe patch sequence" and earned a top-tier prompt quality signal.',
  },
  {
    kicker: '// MOMENT 02',
    title: "They catch Claude's miss.",
    body: 'Claude suggested wrapping the policy cache first. The candidate paused, asked for evidence, and surfaced the incorrect AI output before it reached production logic.',
  },
  {
    kicker: '// MOMENT 03',
    title: 'They own the hard part.',
    body: 'Boilerplate was delegated. The escalation path, degraded mode, and release judgment were written by the candidate without prompting.',
  },
];

const WindowChrome = ({ label = 'taali.com/workspace' }) => (
  <div className="flex items-center justify-between border-b border-[var(--line)] bg-[var(--bg)] px-5 py-3">
    <div className="flex gap-2">
      <span className="h-2.5 w-2.5 rounded-full bg-[var(--red)]" />
      <span className="h-2.5 w-2.5 rounded-full bg-[var(--amber)]" />
      <span className="h-2.5 w-2.5 rounded-full bg-[var(--green)]" />
    </div>
    <div className="font-[var(--font-mono)] text-[12px] text-[var(--mute)]">{label}</div>
    <div className="w-12" />
  </div>
);

export const TaskBriefCard = ({ className = '' }) => (
  <div className={`rounded-[24px] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)] ${className}`.trim()}>
    <div className={`${monoClassName} text-[var(--purple)]`}>01 · THE TASK</div>
    <h3 className="mt-4 font-[var(--font-display)] text-[clamp(30px,3.2vw,44px)] font-semibold tracking-[-0.03em]">
      GenAI Production Readiness Review
    </h3>
    <p className="mt-3 max-w-[980px] text-[15px] leading-8 text-[var(--ink-2)]">
      A candidate senior engineer is asked to stabilize a risky GenAI launch: strengthen safety guardrails,
      improve degraded-mode behavior, and decide whether to ship. They get 30 minutes, a real repo, and Claude as a pair.
    </p>
    <div className="mt-4 flex flex-wrap gap-5 font-[var(--font-mono)] text-[12px] text-[var(--mute)]">
      <span>Duration: 30 min</span>
      <span>Difficulty: Medium</span>
      <span>Stack: Python</span>
      <span>AI: Claude CLI + Chat</span>
    </div>
  </div>
);

export const WorkspaceReplayFrame = ({ className = '' }) => (
  <div className={`${screenClassName} ${className}`.trim()}>
    <div className="border-b border-[color-mix(in_oklab,var(--taali-inverse-text)_10%,transparent)] bg-[var(--ink)] px-5 py-4 text-[var(--bg)]">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3 rounded-full border border-[color-mix(in_oklab,var(--taali-inverse-text)_10%,transparent)] bg-[color-mix(in_oklab,var(--taali-inverse-text)_5%,transparent)] px-3 py-2">
          <span className="grid h-9 w-9 place-items-center rounded-full bg-[var(--purple)] font-[var(--font-mono)] text-[13px] font-semibold text-[var(--taali-inverse-text)]">T</span>
          <div>
            <div className="font-[var(--font-display)] text-[18px] tracking-[-0.02em]">
              GenAI <em className="text-[var(--purple-2)]">Production Readiness</em> Review
            </div>
            <div
              className={`${monoClassName} text-[var(--taali-inverse-text)]`}
              style={{ color: 'color-mix(in oklab, var(--taali-inverse-text) 52%, transparent)' }}
            >
              SAMPLE TASK · ILLUSTRATIVE
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 font-[var(--font-mono)] text-[11px] uppercase tracking-[0.06em] text-[color-mix(in_oklab,var(--taali-inverse-text)_70%,transparent)]">
          <span className="rounded-full border border-[color-mix(in_oklab,var(--taali-inverse-text)_10%,transparent)] bg-[color-mix(in_oklab,var(--taali-inverse-text)_5%,transparent)] px-3 py-1.5">AI: Claude CLI + Chat</span>
          <span className="rounded-full border border-[color-mix(in_oklab,var(--taali-inverse-text)_10%,transparent)] bg-[color-mix(in_oklab,var(--taali-inverse-text)_5%,transparent)] px-3 py-1.5">Permission: Default</span>
          <span className="rounded-full border border-[color-mix(in_oklab,var(--taali-inverse-text)_10%,transparent)] bg-[color-mix(in_oklab,var(--taali-inverse-text)_5%,transparent)] px-3 py-1.5">$ Claude credit: $6.20 left of $12.00</span>
          <span className="rounded-full border border-[color-mix(in_oklab,var(--taali-inverse-text)_10%,transparent)] bg-[color-mix(in_oklab,var(--taali-inverse-text)_5%,transparent)] px-3 py-1.5">26:41</span>
          <span className="rounded-full bg-[var(--taali-inverse-text)] px-3 py-1.5 text-[var(--ink)]">Submit -&gt;</span>
        </div>
      </div>
    </div>

    <div className="border-b border-[var(--line)] bg-[var(--bg)] px-5 py-3">
      <div className="flex flex-wrap gap-2 font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">
        <span className="rounded-full border border-[var(--line)] px-3 py-1.5">Candidate workspace</span>
        <span className="rounded-full bg-[var(--ink)] px-3 py-1.5 text-[var(--bg)]">Repo + editor + AI</span>
        <span className="rounded-full border border-[var(--line)] px-3 py-1.5">Prompt + diff telemetry</span>
        <span className="rounded-full border border-[var(--line)] px-3 py-1.5">Validation runs</span>
        <span className="rounded-full border border-[var(--line)] px-3 py-1.5">Structured evidence</span>
      </div>
    </div>

    <div className="grid gap-0 lg:grid-cols-[220px_minmax(0,1fr)_320px]">
      <aside className="border-r border-[var(--line)] bg-[var(--bg)] px-4 py-4">
        <div className={`${monoClassName} text-[var(--mute)]`}>Context window</div>
        <button type="button" className="mt-4 rounded-[10px] border border-[var(--line)] bg-[var(--bg-2)] px-3 py-2 text-[12px] text-[var(--ink-2)]">
          + New file
        </button>
        <div className="mt-3 text-[12px] leading-6 text-[var(--mute)]">Save syncs edits back into the live terminal workspace.</div>
        <div className="mt-4 space-y-2 font-[var(--font-mono)] text-[12px] leading-6 text-[var(--ink-2)]">
          <div>README.md</div>
          <div>▾ app/</div>
          <div className="pl-4">evals.py</div>
          <div className="pl-4">fallbacks.py</div>
          <div className="rounded-[10px] bg-[var(--purple-soft)] px-2 py-1 pl-4 text-[var(--purple)]">release_guardrails.py</div>
          <div>▾ diagnostics/</div>
          <div className="pl-4">release_findings.md</div>
          <div>▸ prompts/</div>
          <div className="pl-4">support_system.txt</div>
          <div>▾ tests/</div>
          <div className="pl-4">test_release_readiness...</div>
        </div>
      </aside>

      <div className="border-r border-[var(--line)] bg-[var(--bg)] px-4 py-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="font-[var(--font-mono)] text-[12px] text-[var(--ink-2)]">app/release_guardrails.py <span className="text-[var(--mute)]">PYTHON</span></div>
          <div className="flex gap-2">
            <span className="chip purple">Run</span>
            <span className="chip">Save</span>
          </div>
        </div>
        <div className="rounded-[18px] bg-[var(--bg-2)] p-4 font-[var(--font-mono)] text-[12px] leading-6 text-[var(--ink-2)]">
          <div><span className="text-[var(--mute)]">1</span> <span className="text-[var(--purple)]">from</span> app.policy <span className="text-[var(--purple)]">import</span> SAFETY_POLICY</div>
          <div><span className="text-[var(--mute)]">2</span></div>
          <div><span className="text-[var(--mute)]">3</span> <span className="text-[var(--purple)]">def</span> should_allow_response(*, moderation_result, user_intent, confidence):</div>
          <div><span className="text-[var(--mute)]">4</span> &nbsp;&nbsp;&nbsp;&nbsp;<span className="text-[var(--mute)]">&quot;&quot;&quot;Return whether the assistant is allowed to answer directly.&quot;&quot;&quot;</span></div>
          <div><span className="text-[var(--mute)]">5</span> &nbsp;&nbsp;&nbsp;&nbsp;<span className="text-[var(--purple)]">if</span> moderation_result <span className="text-[var(--purple)]">is</span> None:</div>
          <div className="rounded-[8px] bg-[color-mix(in_oklab,var(--purple)_8%,transparent)]"><span className="text-[var(--mute)]">6</span> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span className="text-[var(--mute)]"># BUG: moderation outages should not default to allow</span></div>
          <div className="rounded-[8px] bg-[color-mix(in_oklab,var(--purple)_8%,transparent)]"><span className="text-[var(--mute)]">&nbsp;</span> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span className="text-[var(--mute)]">for customer-facing launch traffic</span></div>
          <div><span className="text-[var(--mute)]">7</span> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span className="text-[var(--purple)]">return</span> True</div>
          <div><span className="text-[var(--mute)]">8</span> &nbsp;&nbsp;&nbsp;&nbsp;<span className="text-[var(--purple)]">if</span> moderation_result.get(<span className="text-[var(--purple)]">&quot;blocked&quot;</span>):</div>
          <div><span className="text-[var(--mute)]">9</span> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span className="text-[var(--purple)]">return</span> False</div>
          <div><span className="text-[var(--mute)]">10</span> &nbsp;&nbsp;&nbsp;&nbsp;<span className="text-[var(--purple)]">if</span> user_intent <span className="text-[var(--purple)]">in</span> SAFETY_POLICY[<span className="text-[var(--purple)]">&quot;always_escalate&quot;</span>]:</div>
          <div><span className="text-[var(--mute)]">11</span> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span className="text-[var(--purple)]">return</span> False</div>
          <div><span className="text-[var(--mute)]">12</span> &nbsp;&nbsp;&nbsp;&nbsp;<span className="text-[var(--purple)]">return</span> confidence &gt;= 0.42</div>
        </div>
      </div>

      <aside className="bg-[var(--bg)] px-4 py-4">
        <div className="flex items-center justify-between">
          <div>
            <div className={`${monoClassName} text-[var(--mute)]`}>Claude</div>
            <div className="mt-1 text-[13px] font-medium text-[var(--ink-2)]">Show terminal</div>
          </div>
          <div className="chip purple">Chat</div>
        </div>

        <div className="mt-4 space-y-3 text-[12.5px] leading-6">
          <div className="rounded-[14px] bg-[var(--purple)] p-3 text-[var(--taali-inverse-text)]">
            Prioritize the highest-risk launch blockers first, then propose the smallest safe patch sequence for the GenAI release review.
          </div>
          <div className="rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] p-3 text-[var(--ink-2)]">
            <div className="mb-2 font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Claude</div>
            <div className="font-medium text-[var(--ink)]">Highest-risk blockers:</div>
            <ul className="mt-2 space-y-1 text-[var(--ink-2)]">
              <li>• Moderation outages currently default to <code>allow=True</code>, which is unsafe for a public launch.</li>
              <li>• Degraded mode can still answer directly on policy-sensitive requests instead of escalating.</li>
              <li>• The eval gate logs critical failures but still marks the release as approved.</li>
            </ul>
          </div>
          <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">
            TURN 04 · CLAUDE-SONNET · SCROLL TO EXPLORE
          </div>
        </div>
      </aside>
    </div>

    <div className="grid gap-4 border-t border-[var(--line)] bg-[var(--bg)] px-5 py-4 md:grid-cols-3">
      {[
        ['Prompt quality', 'Whether the prompt was scoped and sequenced - explicit downstream action vs. "do the thing." Linked back to the prompt text in the timeline.'],
        ['Error recovery', 'Whether the candidate verified, rejected, or accepted incorrect AI suggestions. Captured live with the diff and reasoning attached.'],
        ['Independence', 'Where the candidate delegated to AI versus where they wrote it themselves. Per-block attribution across the whole session.'],
      ].map(([title, body]) => (
        <div key={title} className="rounded-[16px] border border-[var(--line)] bg-[var(--bg-2)] p-4">
          <div className={`${monoClassName} text-[var(--purple)]`}>{title}</div>
          <p className="mt-3 text-[13px] leading-6 text-[var(--ink-2)]">{body}</p>
        </div>
      ))}
    </div>
  </div>
);

export const MomentCards = ({ className = '' }) => (
  <div className={`grid gap-4 md:grid-cols-3 ${className}`.trim()}>
    {SHOWCASE_MOMENTS.map((item) => (
      <div key={item.kicker} className="rounded-[24px] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
        <div className={`${monoClassName} text-[var(--purple)]`}>{item.kicker}</div>
        <h4 className="mt-4 font-[var(--font-display)] text-[34px] leading-[1.02] tracking-[-0.03em]">{item.title}</h4>
        <p className="mt-4 text-[14px] leading-7 text-[var(--ink-2)]">{item.body}</p>
      </div>
    ))}
  </div>
);

export const ShowcaseCtaBand = ({
  onPrimaryAction,
  onSecondaryAction,
  primaryLabel = 'Book a call →',
  secondaryLabel = 'Sign in to Taali',
}) => (
  <div className="rounded-[30px] bg-[var(--ink)] px-8 py-9 text-[var(--bg)] shadow-[var(--shadow-lg)] md:px-12">
    <div className="grid gap-6 lg:grid-cols-[1fr_360px] lg:items-end">
      <div>
        <h3 className="font-[var(--font-display)] text-[clamp(38px,4.6vw,60px)] font-semibold leading-[0.95] tracking-[-0.04em]">
          Ready to see this
          <br />
          with your <span className="text-[var(--purple-2)]">team&apos;s task</span>?
        </h3>
        <p
          className="mt-4 max-w-[640px] text-[15px] leading-7 text-[var(--taali-inverse-text)]"
          style={{ color: 'color-mix(in oklab, var(--taali-inverse-text) 72%, transparent)' }}
        >
          A Taali specialist will bring a live runtime, walk you through the scoring model, and calibrate it
          against recent hires before anything goes to production.
        </p>
      </div>
      <div className="flex flex-col gap-3">
        <button type="button" className="btn btn-primary btn-lg justify-center" onClick={onPrimaryAction}>
          {primaryLabel}
        </button>
        <button
          type="button"
          className="btn btn-outline btn-lg justify-center text-[var(--taali-inverse-text)]"
          style={{ borderColor: 'color-mix(in oklab, var(--taali-inverse-text) 20%, transparent)' }}
          onClick={onSecondaryAction}
        >
          {secondaryLabel}
        </button>
      </div>
    </div>
  </div>
);

export const WelcomePreviewCard = ({ className = '' }) => (
  <div className={`${screenClassName} ${className}`.trim()}>
    <div className="flex items-center justify-between border-b border-[var(--line)] bg-[var(--bg)] px-5 py-4">
      <div className="logo-word text-[24px]">taali<em>.</em></div>
      <div className={`${monoClassName} text-[var(--mute)]`}>Candidate assessment · secure session</div>
    </div>
    <div className="grid gap-5 px-5 py-5 lg:grid-cols-[1.15fr_.85fr]">
      <div className="rounded-[24px] border border-[var(--line)] bg-[var(--bg)] p-5">
        <div className={`${monoClassName} text-[var(--purple)]`}>Invited by Alex Weston · Deeplight AI</div>
        <h3 className="mt-4 font-[var(--font-display)] text-[clamp(34px,3.8vw,52px)] font-semibold leading-[0.96] tracking-[-0.04em]">
          Hi Priya - ready to <span className="text-[var(--purple)]">show your work</span>?
        </h3>
        <p className="mt-4 text-[14px] leading-7 text-[var(--ink-2)]">
          This is a real engineering task, not a puzzle. You&apos;ll work with Claude for up to 60 minutes to diagnose and ship a safety-gate fix. We care how you work with the AI, not just what you ship.
        </p>
        <div className="mt-5 grid gap-4 border-y border-[var(--line)] py-4 md:grid-cols-3">
          {[
            ['Duration', '60 min'],
            ['Tools', 'Claude · IDE · Docs'],
            ['Submit by', 'Sun, 07 Apr · 11:59pm'],
          ].map(([label, value]) => (
            <div key={label}>
              <div className={`${monoClassName} text-[var(--mute)]`}>{label}</div>
              <div className="mt-1 text-[14px] font-medium text-[var(--ink-2)]">{value}</div>
            </div>
          ))}
        </div>
        <div className="mt-5 space-y-3">
          {[
            'A real prompt, not a riddle.',
            'Work the way you normally do.',
            'Pause anytime. One session, one sitting.',
            'We ask for your honest feedback at the end.',
          ].map((item) => (
            <div key={item} className="flex items-start gap-3 border-b border-[var(--line-2)] pb-3 last:border-b-0 last:pb-0">
              <span className="mt-1 h-5 w-5 rounded-full bg-[var(--purple-soft)]" />
              <div className="text-[13px] leading-6 text-[var(--ink-2)]">{item}</div>
            </div>
          ))}
        </div>
        <div className="mt-6 flex flex-col gap-3">
          <button type="button" className="btn btn-primary btn-lg justify-center">Start assessment →</button>
          <button type="button" className="btn btn-outline btn-lg justify-center">Preview the environment (no timer)</button>
        </div>
      </div>

      <div className="space-y-4">
        <div className="rounded-[22px] bg-[var(--ink)] p-5 text-[var(--bg)]">
          <div className={`${monoClassName} text-[var(--purple-2)]`}>Applying for</div>
          <div className="mt-3 text-[30px] font-semibold tracking-[-0.03em]">AI Full Stack Engineer</div>
          <p
            className="mt-3 text-[13px] leading-6 text-[var(--taali-inverse-text)]"
            style={{ color: 'color-mix(in oklab, var(--taali-inverse-text) 70%, transparent)' }}
          >
            Deeplight AI · San Francisco / Remote-OK · Senior (L5)
          </p>
          <span
            className="mt-4 inline-flex rounded-full px-3 py-1 font-[var(--font-mono)] text-[11px]"
            style={{ backgroundColor: 'color-mix(in oklab, var(--taali-inverse-text) 10%, transparent)' }}
          >
            Assessment 1 of 1
          </span>
        </div>
        <div className="rounded-[22px] border border-[var(--line)] bg-[var(--bg)] p-5">
          <div className="text-[22px] font-semibold tracking-[-0.02em]">System <span className="text-[var(--purple)]">check</span></div>
          <div className="mt-4 space-y-3 text-[13px]">
            {[
              ['Browser', 'Chrome 126'],
              ['Connection', 'Stable · 84 Mbps'],
              ['Screen', '1440 × 900+'],
              ['Claude access', 'Ready'],
            ].map(([label, value]) => (
              <div key={label} className="flex items-center justify-between border-b border-[var(--line-2)] pb-3 last:border-b-0 last:pb-0">
                <span className="text-[var(--mute)]">{label}</span>
                <span className="text-[var(--green)]">{value}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="rounded-[22px] border border-[var(--line)] bg-[var(--bg)] p-5">
          <div className="text-[22px] font-semibold tracking-[-0.02em]">Your <span className="text-[var(--purple)]">rights</span></div>
          <div className="mt-4 rounded-[18px] bg-[var(--purple-soft)] p-4 text-[12.5px] leading-6 text-[var(--ink-2)]">
            We record what you prompted, what Claude said, and what you accepted or edited. We do not record your camera.
          </div>
        </div>
      </div>
    </div>
  </div>
);

export const StandingReportPreviewCard = ({ className = '' }) => (
  <div className={`${screenClassName} ${className}`.trim()}>
    <div className="flex items-center justify-between border-b border-[var(--line)] bg-[var(--bg)] px-5 py-4">
      <div className="logo-word text-[24px]">taali<em>.</em></div>
      <div className="app-tabs !gap-2">
        {['Jobs', 'Candidates', 'Tasks', 'Reporting', 'Settings'].map((item) => (
          <span key={item} className={`app-tab ${item === 'Candidates' ? 'active' : ''}`.trim()}>{item}</span>
        ))}
      </div>
    </div>

    <div className="px-5 py-5">
      <div className="rounded-[28px] bg-[var(--ink)] px-6 py-6 text-[var(--bg)]">
        <div className="mb-4 flex items-center gap-3">
          <div className={`${monoClassName} text-[var(--purple-2)]`}>Standing report · application #APP-2041</div>
          <span className="chip green">Strong hire</span>
        </div>
        <h3 className="font-[var(--font-display)] text-[clamp(36px,3.7vw,56px)] font-semibold leading-[0.96] tracking-[-0.04em]">
          Priya Anand - where she <span className="text-[var(--purple-2)]">stands</span> in the pipeline.
        </h3>
        <p
          className="mt-4 max-w-[760px] text-[15px] leading-7 text-[var(--taali-inverse-text)]"
          style={{ color: 'color-mix(in oklab, var(--taali-inverse-text) 72%, transparent)' }}
        >
          A role-anchored, shareable summary. Evidence-first: every claim links back to a timestamped moment in her assessment session.
        </p>
        <div
          className="mt-6 grid gap-4 border-t pt-5 md:grid-cols-4"
          style={{ borderColor: 'color-mix(in oklab, var(--taali-inverse-text) 10%, transparent)' }}
        >
          {[
            ['Composite', '87 / 100', 'top 6% cohort'],
            ['Role fit', '94%', 'AI Full Stack Eng'],
            ['AI-collaboration', 'A+ · 92', 'prompt 9.1 · recovery 8.9'],
            ['Percentile', '98th', 'vs 240 candidates'],
          ].map(([label, value, detail]) => (
            <div key={label}>
              <div className={`${monoClassName} text-[var(--taali-inverse-text)] opacity-50`}>{label}</div>
              <div className="mt-2 text-[30px] font-semibold tracking-[-0.03em] text-[var(--lime)]">{value}</div>
              <div
                className="mt-1 text-[12px] text-[var(--taali-inverse-text)]"
                style={{ color: 'color-mix(in oklab, var(--taali-inverse-text) 55%, transparent)' }}
              >
                {detail}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1.25fr_.95fr]">
        <div className="space-y-4">
          <div className="rounded-[22px] border border-[var(--line)] bg-[var(--bg)] p-5">
            <div className={`${monoClassName} text-[var(--purple)]`}>Verdict</div>
            <div className="mt-3 text-[32px] font-semibold tracking-[-0.03em]">Advance to panel. <span className="text-[var(--purple)]">With conviction.</span></div>
            <p className="mt-3 text-[13.5px] leading-6 text-[var(--ink-2)]">
              Priya works with Claude the way a senior engineer works with a sharp junior - specific, skeptical, and in charge of the decisions that matter.
            </p>
          </div>

          <div className="rounded-[22px] border border-[var(--line)] bg-[var(--bg)] p-5">
            <div className="text-[28px] font-semibold tracking-[-0.03em]">Top <span className="text-[var(--purple)]">strengths</span></div>
            <div className="mt-4 space-y-4 text-[13px] leading-6 text-[var(--ink-2)]">
              {[
                ['01', 'Connects LLM failure to customer-facing blast radius', '9.4 / 10'],
                ['02', 'Rejects premature suggestions', '9.1 / 10'],
                ['03', 'Orders patches for rollback safety', '8.9 / 10'],
                ['04', 'Hand-writes the code that matters', '8.8 / 10'],
              ].map(([index, text, score]) => (
                <div key={index} className="grid grid-cols-[40px_minmax(0,1fr)_auto] gap-4 border-b border-[var(--line-2)] pb-4 last:border-b-0 last:pb-0">
                  <div className="font-[var(--font-display)] text-[28px] text-[var(--purple)]">{index}</div>
                  <div>{text}</div>
                  <div className="font-[var(--font-mono)] text-[12px] text-[var(--green)]">{score}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="space-y-4">
          <div className="rounded-[22px] border border-[var(--line)] bg-[var(--bg)] p-5">
            <div className="text-[26px] font-semibold tracking-[-0.03em]">Vs. <span className="text-[var(--purple)]">peers</span></div>
            <div className="mt-4 space-y-3 text-[13px] text-[var(--ink-2)]">
              {[
                ['1 · Priya Anand (you)', '87', '98th'],
                ['2 · Nia Kovac', '81', '94th'],
                ['3 · Thomas Hale', '76', '88th'],
                ['4 · Sofia Renna', '74', '82nd'],
              ].map(([name, score, percentile]) => (
                <div key={name} className="flex items-center justify-between rounded-[12px] bg-[var(--bg-2)] px-4 py-3">
                  <span>{name}</span>
                  <span className="font-[var(--font-mono)]">{score} · {percentile}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-[22px] border border-[var(--line)] bg-[var(--bg)] p-5">
            <div className="text-[26px] font-semibold tracking-[-0.03em]">Scored <span className="text-[var(--purple)]">dimensions</span></div>
            <div className="mt-4 space-y-3">
              {[
                ['Prompt quality', '91', 'var(--green)'],
                ['Error recovery', '86', 'var(--green)'],
                ['Independence', '89', 'var(--green)'],
                ['Context utilization', '72', 'var(--amber)'],
                ['Design thinking', '94', 'var(--green)'],
                ['Time to first prompt', '3:42', 'var(--green)'],
              ].map(([label, value, color]) => (
                <div key={label}>
                  <div className="mb-1 flex items-center justify-between text-[13px]">
                    <span>{label}</span>
                    <span className="font-[var(--font-mono)]">{value}</span>
                  </div>
                  <div className="h-2 rounded-full bg-[var(--bg-2)]">
                    <div className="h-full rounded-full" style={{ width: label === 'Time to first prompt' ? '88%' : `${Number(value) || 0}%`, background: color }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
);

export const SettingsPreviewCard = ({ className = '' }) => (
  <div className={`${screenClassName} ${className}`.trim()}>
    <div className="flex items-center justify-between border-b border-[var(--line)] bg-[var(--bg)] px-5 py-4">
      <div className="logo-word text-[24px]">taali<em>.</em></div>
      <div className="app-tabs !gap-2">
        {['Jobs', 'Candidates', 'Tasks', 'Reporting', 'Settings'].map((item) => (
          <span key={item} className={`app-tab ${item === 'Settings' ? 'active' : ''}`.trim()}>{item}</span>
        ))}
      </div>
    </div>
    <div className="grid gap-4 px-5 py-5 lg:grid-cols-[210px_1fr]">
      <aside className="rounded-[22px] border border-[var(--line)] bg-[var(--bg)] p-4">
        <div className={`${monoClassName} text-[var(--mute)]`}>Workspace</div>
        <div className="mt-3 space-y-2 text-[13px] text-[var(--ink-2)]">
          {[
            'Organization',
            'Scoring policy',
            'AI tooling',
            'Members',
            'Roles & access',
            'ATS sync',
            'SSO / SAML',
            'Billing',
            'Notifications',
          ].map((item, index) => (
            <div key={item} className={`rounded-[10px] px-3 py-2 ${index === 0 ? 'bg-[var(--ink)] text-[var(--bg)]' : ''}`.trim()}>
              {item}
            </div>
          ))}
        </div>
      </aside>

      <div className="space-y-4">
        <div className="rounded-[22px] border border-[var(--line)] bg-[var(--bg)] p-5">
          <div className="text-[30px] font-semibold tracking-[-0.03em]">Organization<span className="text-[var(--purple)]">.</span></div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            {[
              ['Workspace name', 'DEEPLIGHT_AI'],
              ['Domain', 'deeplight.ai'],
              ['Candidate-facing brand', 'Deeplight · Engineering'],
              ['Locale', 'English (US)'],
            ].map(([label, value]) => (
              <div key={label}>
                <div className={`${monoClassName} text-[var(--mute)]`}>{label}</div>
                <div className="mt-2 rounded-[12px] border border-[var(--line)] bg-[var(--bg-2)] px-4 py-3 text-[14px] text-[var(--ink-2)]">{value}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-[22px] border border-[var(--line)] bg-[var(--bg)] p-5">
          <div className="text-[30px] font-semibold tracking-[-0.03em]">Scoring <span className="text-[var(--purple)]">policy</span></div>
          <p className="mt-2 text-[13px] text-[var(--mute)]">Turn dimensions on or off for this workspace.</p>
          <div className="mt-4 space-y-3">
            {[
              ['Prompt quality', true],
              ['Error recovery', true],
              ['Independence', true],
              ['Context utilization', true],
              ['Design thinking', true],
              ['Time-to-first-signal', false],
            ].map(([label, enabled]) => (
              <div key={label} className="flex items-center justify-between rounded-[14px] border border-[var(--line)] px-4 py-3">
                <span className="text-[13px] text-[var(--ink-2)]">{label}</span>
                <span className={`h-6 w-10 rounded-full ${enabled ? 'bg-[var(--purple)]' : 'bg-[var(--bg-3)]'}`.trim()}>
                  <span className={`mt-1 block h-4 w-4 rounded-full bg-[var(--taali-inverse-text)] transition ${enabled ? 'ml-5' : 'ml-1'}`.trim()} />
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-[22px] border border-[var(--line)] bg-[var(--bg)] p-5">
          <div className="text-[30px] font-semibold tracking-[-0.03em]">AI <span className="text-[var(--purple)]">tooling</span></div>
          <div className="mt-4 space-y-3 text-[13px] text-[var(--ink-2)]">
            {[
              ['Claude CLI + Chat', 'Enabled'],
              ['Cursor / Copilot inline', 'Off'],
              ['No-AI baseline', 'Enabled'],
            ].map(([label, state]) => (
              <div key={label} className="flex items-center justify-between rounded-[14px] border border-[var(--line)] px-4 py-3">
                <span>{label}</span>
                <span className={`chip ${state === 'Enabled' ? 'purple' : ''}`.trim()}>{state}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  </div>
);
