import React, { useState } from 'react';
import { ArrowRight, Check, CheckCircle, Menu, X, XCircle } from 'lucide-react';
import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  ResponsiveContainer,
} from 'recharts';

import { BRAND } from '../../config/brand';
import { ASSESSMENT_PRICE_AED, formatAed } from '../../lib/currency';
import { dimensionOrder, getDimensionById } from '../../scoring/scoringDimensions';
import { BrandGlyph, Logo } from '../../shared/ui/Branding';
import {
  Button,
  Card,
  PageContainer,
  Panel,
} from '../../shared/ui/TaaliPrimitives';
import { ScoringCardGrid } from '../../shared/ui/ScoringCardGrid';

const LandingNav = ({ onNavigate }) => {
  const [mobileOpen, setMobileOpen] = useState(false);

  const scrollTo = (id) => {
    setMobileOpen(false);
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <nav className="taali-nav sticky top-0 z-40">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-4">
        <Logo onClick={() => onNavigate('landing')} />
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="md:hidden !px-2 !py-2"
          onClick={() => setMobileOpen((open) => !open)}
          aria-label="Toggle navigation"
        >
          {mobileOpen ? <X size={18} /> : <Menu size={18} />}
        </Button>

        <div className="hidden items-center gap-2 md:flex">
          <Button type="button" variant="ghost" size="sm" className="font-mono" onClick={() => scrollTo('pricing')}>
            Pricing
          </Button>
          <Button type="button" variant="secondary" size="sm" className="font-mono" onClick={() => onNavigate('login')}>
            Sign In
          </Button>
          <Button type="button" variant="primary" size="sm" className="font-mono" onClick={() => onNavigate('login')}>
            Start Free Trial
          </Button>
        </div>
      </div>

      {mobileOpen ? (
        <div className="border-t-2 border-[var(--taali-border)] bg-[var(--taali-surface)] px-6 py-3 md:hidden">
          <div className="grid gap-2">
            <Button type="button" variant="ghost" size="sm" className="justify-start font-mono" onClick={() => scrollTo('pricing')}>
              Pricing
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="font-mono"
              onClick={() => {
                setMobileOpen(false);
                onNavigate('login');
              }}
            >
              Sign In
            </Button>
            <Button
              type="button"
              variant="primary"
              size="sm"
              className="font-mono"
              onClick={() => {
                setMobileOpen(false);
                onNavigate('login');
              }}
            >
              Start Free Trial
            </Button>
          </div>
        </div>
      ) : null}
    </nav>
  );
};

const HeroSection = ({ onNavigate }) => (
  <section className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-bg)]">
    <PageContainer className="grid items-center gap-8 lg:grid-cols-[1.2fr_1fr]">
      <div>
        <span className="taali-badge taali-badge-purple mb-4 inline-flex px-3 py-1 font-mono text-xs">
          BUILT FOR AI-NATIVE ENGINEERING TEAMS
        </span>
        <h1 className="text-4xl font-bold leading-tight text-[var(--taali-text)] md:text-6xl">
          Stop hiring for yesterday&apos;s skills.
          <br />
          Start hiring with TAALI.
        </h1>
        <p className="mt-4 max-w-2xl text-base text-[var(--taali-text)] md:text-xl" style={{ opacity: 0.9 }}>
          Assess how engineers actually work today using Cursor, Claude Code, and OpenAI Codex on real tasks, in real workflows.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Button type="button" variant="primary" size="lg" onClick={() => onNavigate('login')}>
            Book a Demo
            <ArrowRight size={16} />
          </Button>
          <Button type="button" variant="secondary" size="lg" onClick={() => onNavigate('login')}>
            Start Free Trial
          </Button>
        </div>
      </div>

      <Panel className="grid grid-cols-2 gap-3 p-4">
        <Card className="bg-[var(--taali-border-muted)]/30 p-3">
          <div className="mb-2 flex items-center gap-2">
            <XCircle size={16} className="text-red-500" />
            <span className="font-mono text-xs font-bold text-[var(--taali-muted)]">LEGACY TEST</span>
          </div>
          <pre className="overflow-auto font-mono text-xs text-[var(--taali-muted)]">{`function reverseList(head) {
  let prev = null;
  let curr = head;
  while (curr) {
    let next = curr.next;
    curr.next = prev;
    prev = curr;
    curr = next;
  }
  return prev;
}`}</pre>
        </Card>

        <Card className="bg-[var(--taali-purple)] p-3 text-white">
          <div className="mb-2 flex items-center gap-2">
            <CheckCircle size={16} className="text-white" />
            <span className="font-mono text-xs font-bold">TAALI FLOW</span>
          </div>
          <pre className="font-mono text-xs text-white/95">{`> Ask Codex for a
  test scaffold

> Use Claude Code to
  trace the parser

> Ship fix with
  green tests`}</pre>
        </Card>

        <Card className="bg-[var(--taali-border-muted)]/30 p-3">
          <p className="font-mono text-xs text-[var(--taali-muted)]">TESTS FOR:</p>
          <p className="mt-1 font-mono text-sm text-[var(--taali-text)]">Algorithm recall</p>
          <p className="font-mono text-sm text-[var(--taali-text)]">Interview theatrics</p>
          <p className="mt-2 font-mono text-sm font-bold text-red-600">NOT delivery quality</p>
        </Card>

        <Card className="bg-gray-900 p-3 text-white">
          <p className="font-mono text-xs text-[var(--taali-purple)]">TESTS FOR:</p>
          <p className="mt-1 font-mono text-sm">Agent collaboration</p>
          <p className="font-mono text-sm">Debugging in context</p>
          <p className="mt-2 font-mono text-sm font-bold text-[var(--taali-purple)]">ACTUAL execution</p>
        </Card>
      </Panel>
    </PageContainer>
  </section>
);

const WhyTaaliSection = () => (
  <section id="why-taali" className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-surface)]">
    <PageContainer>
      <div className="mx-auto max-w-4xl text-center">
        <h2 className="text-4xl font-bold">Why TAALI</h2>
        <p className="mt-3 font-mono text-sm text-[var(--taali-muted)]">Engineering changed. Hiring didn&apos;t.</p>
        <p className="mt-2 font-mono text-sm text-[var(--taali-muted)]">
          Modern engineers do not work in a blank editor. They work in real repos, with modern coding agents, through fast
          iteration and validation. TAALI helps teams hire for that reality.
        </p>
      </div>

      <ScoringCardGrid
        className="mt-8 md:grid-cols-2 lg:grid-cols-4"
        items={[
          {
            key: 'prompting-core-skill',
            title: 'Prompting is a core skill',
            description: 'As agents automate more implementation work, prompt strategy increasingly determines output quality.',
          },
          {
            key: 'conversation-signal',
            title: 'Conversation is signal',
            description: 'Agent conversations reveal collaboration quality, ownership, and communication maturity.',
          },
          {
            key: 'prompting-thinking',
            title: 'Prompting exposes thinking',
            description: 'How candidates frame requests surfaces critical thinking, design judgment, and tradeoff awareness.',
          },
          {
            key: 'efficiency-experience',
            title: 'Efficiency shows experience',
            description: 'Strong candidates get better outcomes with fewer, sharper prompts and tighter iteration loops.',
          },
        ]}
      />
    </PageContainer>
  </section>
);

const HowTaaliSection = () => {
  const dimensions = dimensionOrder.map((id) => {
    const definition = getDimensionById(id);
    return {
      key: id,
      title: definition.label,
      description: definition.shortDescription,
    };
  });

  const personaScoreMap = {
    task_completion: { candidateA: 89, candidateB: 78 },
    prompt_clarity: { candidateA: 92, candidateB: 61 },
    context_provision: { candidateA: 88, candidateB: 59 },
    independence_efficiency: { candidateA: 90, candidateB: 57 },
    response_utilization: { candidateA: 87, candidateB: 62 },
    debugging_design: { candidateA: 84, candidateB: 74 },
    written_communication: { candidateA: 91, candidateB: 63 },
    role_fit: { candidateA: 82, candidateB: 80 },
  };

  const demoComparison = dimensionOrder.map((id) => ({
    dimension: getDimensionById(id).label,
    candidateA: personaScoreMap[id]?.candidateA ?? 0,
    candidateB: personaScoreMap[id]?.candidateB ?? 0,
    fullMark: 100,
  }));

  return (
    <section id="how-taali" className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-bg)]">
      <PageContainer>
        <div className="mx-auto max-w-4xl text-center">
          <h2 className="text-4xl font-bold">How TAALI Evaluates Candidates</h2>
          <p className="mt-3 font-mono text-sm text-[var(--taali-muted)]">Real session behavior to structured scorecard.</p>
        </div>

        <Panel className="mt-8 p-6">
          <h3 className="text-2xl font-bold">What we score</h3>
          <ScoringCardGrid className="mt-4" items={dimensions} />
        </Panel>

        <Panel className="mt-6 p-6">
          <h3 className="text-2xl font-bold">Example candidate persona comparison</h3>
          <p className="mt-2 font-mono text-sm text-[var(--taali-muted)]">
            Same task, very different signal. TAALI shows how candidates reached outcomes and whether performance is repeatable.
          </p>

          <div className="mt-5 grid gap-4 lg:grid-cols-[2fr_1fr]">
            <Card className="h-[340px] p-3">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart data={demoComparison} outerRadius="72%">
                  <PolarGrid stroke="rgba(157, 0, 255, 0.22)" />
                  <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 11, fontFamily: 'var(--taali-font)' }} />
                  <PolarRadiusAxis domain={[0, 100]} tickCount={6} />
                  <Radar name="AI-Native Product Engineer" dataKey="candidateA" stroke="#9D00FF" fill="#9D00FF" fillOpacity={0.16} />
                  <Radar name="Code-Strong Backend Engineer" dataKey="candidateB" stroke="#3f3f56" fill="#3f3f56" fillOpacity={0.08} />
                </RadarChart>
              </ResponsiveContainer>
            </Card>

            <div className="grid gap-3">
              <Card className="p-4">
                <h4 className="font-bold">AI-Native Product Engineer</h4>
                <p className="mt-1 font-mono text-sm text-[var(--taali-text)]">
                  High Task completion, Prompt clarity, Context provision, and Response utilization with clear communication.
                </p>
              </Card>
              <Card className="p-4">
                <h4 className="font-bold">Code-Strong Backend Engineer</h4>
                <p className="mt-1 font-mono text-sm text-[var(--taali-text)]">
                  Strong Debugging and design plus Role fit, but lower Prompt clarity and slower iteration loops.
                </p>
              </Card>
            </div>
          </div>
        </Panel>
      </PageContainer>
    </section>
  );
};

const PricingSection = ({ onNavigate }) => (
  <section id="pricing" className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-surface)]">
    <PageContainer>
      <div className="mx-auto max-w-4xl text-center">
        <h2 className="text-4xl font-bold">Pricing That Scales with Hiring</h2>
        <p className="mt-2 font-mono text-sm text-[var(--taali-muted)]">Clear plans, predictable costs, no hidden add-ons.</p>
      </div>

      <div className="mx-auto mt-8 grid max-w-4xl gap-5 md:grid-cols-2">
        <Panel className="relative p-6">
          <span className="taali-badge taali-badge-purple absolute -top-3 left-5">Recommended</span>
          <h3 className="mt-3 text-2xl font-bold">Pay-Per-Use</h3>
          <p className="mt-1 text-5xl font-bold">{formatAed(ASSESSMENT_PRICE_AED)}</p>
          <p className="font-mono text-sm text-[var(--taali-muted)]">per assessment</p>
          <ul className="mt-4 grid gap-2">
            {[
              'Full coding environment',
              'Coding agents (Claude Code, Codex)',
              'Automated scoring',
              'Candidate reports',
              'Workable sync',
              'Email support',
            ].map((feature) => (
              <li key={feature} className="flex items-center gap-2 font-mono text-sm text-[var(--taali-text)]">
                <Check size={16} className="text-[var(--taali-purple)]" />
                {feature}
              </li>
            ))}
          </ul>
          <Button type="button" variant="primary" size="lg" className="mt-6 w-full" onClick={() => onNavigate('login')}>
            Start Free Trial
          </Button>
        </Panel>

        <Panel className="p-6">
          <h3 className="mt-3 text-2xl font-bold">Monthly</h3>
          <p className="mt-1 text-5xl font-bold">{formatAed(300)}</p>
          <p className="font-mono text-sm text-[var(--taali-muted)]">per month</p>
          <ul className="mt-4 grid gap-2">
            {[
              'Everything in Pay-Per-Use',
              'Unlimited assessments',
              'Custom tasks',
              'Priority support',
              'Analytics dashboard',
              'Team management',
            ].map((feature) => (
              <li key={feature} className="flex items-center gap-2 font-mono text-sm text-[var(--taali-text)]">
                <Check size={16} className="text-[var(--taali-purple)]" />
                {feature}
              </li>
            ))}
          </ul>
          <Button type="button" variant="secondary" size="lg" className="mt-6 w-full" onClick={() => onNavigate('login')}>
            Book Demo
          </Button>
        </Panel>
      </div>
    </PageContainer>
  </section>
);

const Footer = () => (
  <footer className="border-t-2 border-[var(--taali-border)] bg-[#12031f] text-white">
    <div className="mx-auto max-w-7xl px-6 py-14">
      <div className="grid gap-8 md:grid-cols-4">
        <div>
          <div className="mb-3 flex items-center gap-2">
            <BrandGlyph borderClass="border-white" />
            <span className="text-xl font-bold tracking-tight">{BRAND.name}</span>
          </div>
          <p className="font-mono text-sm text-white/70">{BRAND.productTagline}</p>
        </div>

        {[
          {
            title: 'Product',
            items: ['Features', 'Pricing', 'Integrations', 'Changelog'],
          },
          {
            title: 'Resources',
            items: ['Documentation', 'API Reference', 'Blog', 'Support'],
          },
          {
            title: 'Company',
            items: ['About', 'Careers', 'Privacy', 'Terms'],
          },
        ].map((column) => (
          <div key={column.title}>
            <h4 className="font-bold">{column.title}</h4>
            <ul className="mt-3 space-y-1 font-mono text-sm text-white/70">
              {column.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="mt-10 border-t border-white/20 pt-6 font-mono text-xs text-white/55">
        <div>&copy; 2026 {BRAND.name}. All rights reserved.</div>
      </div>
    </div>
  </footer>
);

export const LandingPage = ({ onNavigate }) => (
  <div className="min-h-screen bg-[var(--taali-bg)] text-[var(--taali-text)]">
    <LandingNav onNavigate={onNavigate} />
    <HeroSection onNavigate={onNavigate} />
    <WhyTaaliSection />
    <HowTaaliSection />
    <PricingSection onNavigate={onNavigate} />
    <Footer />
  </div>
);

export default LandingPage;
