import React from 'react';

import { MarketingNav, TaaliLogo } from '../../shared/layout/TaaliLayout';

const lightGridStyle = {
  backgroundImage:
    'linear-gradient(var(--line-2) 1px, transparent 1px), linear-gradient(90deg, var(--line-2) 1px, transparent 1px)',
  backgroundSize: '48px 48px',
  maskImage: 'radial-gradient(70% 70% at 30% 30%, black, transparent 75%)',
  opacity: 0.7,
};

const darkGridStyle = {
  backgroundImage:
    'linear-gradient(rgba(255,255,255,.04) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.04) 1px, transparent 1px)',
  backgroundSize: '44px 44px',
  maskImage: 'radial-gradient(80% 60% at 30% 40%, black, transparent 80%)',
};

export const AuthCard = ({ kicker, title, subtitle, children, widthClassName = 'max-w-[420px]' }) => (
  <div
    className={`w-full rounded-[var(--radius-xl)] border border-[var(--line)] bg-[var(--bg-2)] p-10 shadow-[var(--shadow-lg)] ${widthClassName}`.trim()}
  >
    {kicker ? <div className="kicker">{kicker}</div> : null}
    {title ? <h2 className="mt-2 font-[var(--font-display)] text-[44px] font-semibold leading-none tracking-[-0.02em]">{title}</h2> : null}
    {subtitle ? <p className="mt-2 mb-7 text-sm text-[var(--mute)]">{subtitle}</p> : null}
    {children}
  </div>
);

export const SignInLayout = ({ onNavigate, children }) => (
  <div className="min-h-screen bg-[var(--bg)]">
    <MarketingNav onNavigate={onNavigate} />
    <div className="grid min-h-[calc(100vh-64px)] grid-cols-1 lg:grid-cols-2">
      <div className="relative overflow-hidden border-r border-[var(--line)] px-8 py-16 lg:px-[72px]">
        <div className="pointer-events-none absolute inset-0" style={lightGridStyle} />
        <div className="relative flex h-full flex-col">
          <span className="eyebrow">
            <span className="eyebrow-tag">taali.</span>
            AI-native technical assessments
          </span>
          <h1 className="h-display mt-6 max-w-[520px] text-[clamp(52px,5.4vw,84px)] leading-[0.98]">
            Welcome back<em>.</em>
            <br />
            Let&apos;s find the
            <br />
            next <em>great</em> hire.
          </h1>
          <p className="max-w-[480px] text-[16.5px] leading-[1.55] text-[var(--mute)]">
            Your workspace holds every assessment, every AI-collaboration score, and every candidate you&apos;re still thinking about. Pick up exactly where you left off.
          </p>

          <div className="mt-auto max-w-[520px] rounded-[var(--radius-lg)] border border-dashed border-[var(--line)] bg-[var(--bg-2)] p-7 shadow-[var(--shadow-sm)]">
            <p className="font-[var(--font-display)] text-[22px] leading-[1.3] text-[var(--ink)]">
              We stopped running whiteboard interviews the week we plugged in Taali. Our <em>first</em> AI-collab-scored cohort became the top quartile of the eng team.
            </p>
            <div className="mt-4 flex items-center gap-3 text-[13px] text-[var(--mute)]">
              <div className="grid h-8 w-8 place-items-center rounded-full bg-[var(--purple-soft)] text-xs font-semibold text-[var(--purple)]">AW</div>
              <div><b className="text-[var(--ink)]">Alex Weston</b> · VP Engineering · FOUNDRY</div>
            </div>
          </div>
        </div>
      </div>
      <div className="grid place-items-center px-6 py-16 lg:px-16">
        {children}
      </div>
    </div>
  </div>
);

export const FlowLayout = ({ children, footerContent = null }) => (
  <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[1.05fr_.95fr]">
    <div className="relative flex flex-col justify-between overflow-hidden bg-[var(--ink)] px-8 py-12 text-[var(--bg)] lg:px-12">
      <div className="pointer-events-none absolute inset-0" style={darkGridStyle} />
      <div className="relative">
        <TaaliLogo onClick={() => {}} wordmarkClassName="!text-[18px]" />
      </div>
      <div className="relative">
        <h1 className="font-[var(--font-display)] text-[56px] font-semibold leading-none tracking-[-0.04em]">
          Hire engineers who work <em>with</em> AI, not around it.
        </h1>
        <p className="mt-4 max-w-[460px] text-[17px] leading-[1.55] text-white/80">
          Taali runs a 60-minute agentic assessment that watches how candidates prompt, accept, reject, and steer Claude, then scores it against the role.
        </p>
      </div>
      <div className="relative flex gap-7">
        <div>
          <div className="text-[28px] font-semibold tracking-[-0.02em]">4.8×</div>
          <div className="mt-1 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-white/60">Faster screens</div>
        </div>
        <div>
          <div className="text-[28px] font-semibold tracking-[-0.02em]">92%</div>
          <div className="mt-1 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-white/60">Panel accuracy</div>
        </div>
        <div>
          <div className="text-[28px] font-semibold tracking-[-0.02em]">240+</div>
          <div className="mt-1 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-white/60">Teams hiring</div>
        </div>
      </div>
    </div>
    <div className="flex flex-col px-6 py-16 lg:px-14">
      <div className="mb-7 inline-flex w-fit gap-0.5 rounded-full border border-[var(--line)] bg-[var(--bg-2)] p-1 shadow-[var(--shadow-sm)]">
        <span className="rounded-full bg-[var(--ink)] px-4 py-1.5 text-[13px] font-medium text-[var(--bg)]">Auth</span>
        <span className="px-4 py-1.5 text-[13px] font-medium text-[var(--mute)]">Flow</span>
      </div>
      {children}
      {footerContent}
    </div>
  </div>
);
