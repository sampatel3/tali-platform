import React, { useState } from 'react';
import {
  ArrowRight,
  Check,
  Menu,
  Play,
  ShieldCheck,
  Sparkles,
  Workflow,
  X,
} from 'lucide-react';
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from 'recharts';

import { BRAND } from '../../config/brand';
import { aedToUsd } from '../../lib/currency';
import { dimensionOrder, getDimensionById } from '../../scoring/scoringDimensions';
import { BrandGlyph, Logo } from '../../shared/ui/Branding';
import { GlobalThemeToggle } from '../../shared/ui/GlobalThemeToggle';
import {
  Badge,
  Button,
  Card,
  PageContainer,
  Panel,
} from '../../shared/ui/TaaliPrimitives';
import { ScoringCardGrid } from '../../shared/ui/ScoringCardGrid';

const TRUST_ITEMS = [
  'Built for Cursor, Codex, and Claude Code workflows',
  'Structured rubric across execution, judgment, and communication',
  'ATS-ready candidate summaries with recruiter-facing evidence',
  'Real repository context instead of toy algorithm prompts',
];

const FEATURE_BANDS = [
  {
    id: 'product',
    eyebrow: 'REAL TASKS, REAL SIGNAL',
    title: 'Assess the way modern engineers actually work.',
    copy: 'Candidates operate in a realistic environment, collaborate with coding agents, and leave behind evidence you can review without replaying the session yourself.',
    bullets: [
      'Agent conversations reveal how clearly candidates frame problems and use feedback.',
      'Git, tests, timing, and rubric evidence combine into one recruiter-ready decision surface.',
      'Role-fit analysis stays attached to the assessment so sidebars, reports, and details agree.',
    ],
  },
  {
    id: 'review',
    eyebrow: 'FASTER RECRUITER REVIEW',
    title: 'Move from raw activity to a decision in minutes.',
    copy: 'TAALI turns messy session data into a compact review flow: overview, breakdown, evidence, integrity signals, and follow-up questions.',
    bullets: [
      'Strongest and weakest dimensions are surfaced automatically.',
      'Requirement-level evidence explains what was met, partial, or missing.',
      'Retakes and superseded attempts stay traceable without cluttering the main experience.',
    ],
  },
];

const PROOF_POINTS = [
  {
    title: 'Clear hiring signal',
    description: 'Separate strong execution from candidates who mainly paste or over-rely on agents without direction.',
  },
  {
    title: 'Consistent review surfaces',
    description: 'The same evidence model powers the recruiter sidebar, candidate detail page, and assessment summary.',
  },
  {
    title: 'Modern hiring workflow',
    description: 'More ambient, calmer surfaces and stronger visual hierarchy keep the product technical without feeling dated.',
  },
];

const demoComparison = dimensionOrder.map((id) => {
  const scores = {
    task_completion: [8.8, 7.2],
    prompt_clarity: [9.1, 5.9],
    context_provision: [8.7, 6.1],
    independence_efficiency: [8.9, 5.8],
    response_utilization: [8.6, 6.4],
    debugging_design: [8.1, 7.7],
    written_communication: [9.0, 6.0],
    role_fit: [8.3, 8.0],
  }[id] || [0, 0];

  return {
    dimension: getDimensionById(id).label,
    candidateA: scores[0],
    candidateB: scores[1],
    fullMark: 10,
  };
});

const scrollToId = (id) => {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
};

const LandingNav = ({ onNavigate }) => {
  const [mobileOpen, setMobileOpen] = useState(false);

  const handleScroll = (id) => {
    setMobileOpen(false);
    scrollToId(id);
  };

  return (
    <nav className="taali-nav sticky top-0 z-40">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-4">
        <Logo onClick={() => onNavigate('landing')} />

        <div className="hidden items-center gap-2 md:flex">
          <Button type="button" variant="ghost" size="sm" onClick={() => handleScroll('product')}>
            Product
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={() => handleScroll('framework')}>
            Framework
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={() => handleScroll('pricing')}>
            Pricing
          </Button>
        </div>

        <div className="flex items-center gap-2">
          <Button type="button" variant="secondary" size="sm" className="hidden md:inline-flex" onClick={() => onNavigate('login')}>
            Sign In
          </Button>
          <Button type="button" variant="primary" size="sm" className="hidden md:inline-flex" onClick={() => onNavigate('demo')}>
            Demo
          </Button>
          <GlobalThemeToggle className="shrink-0" />
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
        </div>
      </div>

      {mobileOpen ? (
        <div className="border-t border-[var(--taali-border-soft)] bg-[rgba(255,255,255,0.92)] px-6 py-4 backdrop-blur-md md:hidden">
          <div className="grid gap-2">
            <Button type="button" variant="ghost" size="sm" className="justify-start" onClick={() => handleScroll('product')}>
              Product
            </Button>
            <Button type="button" variant="ghost" size="sm" className="justify-start" onClick={() => handleScroll('framework')}>
              Framework
            </Button>
            <Button type="button" variant="ghost" size="sm" className="justify-start" onClick={() => handleScroll('pricing')}>
              Pricing
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={() => onNavigate('login')}>
              Sign In
            </Button>
            <Button type="button" variant="primary" size="sm" onClick={() => onNavigate('demo')}>
              Demo
            </Button>
          </div>
        </div>
      ) : null}
    </nav>
  );
};

const HeroMetric = ({ label, value }) => (
  <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[rgba(255,255,255,0.72)] px-4 py-3 shadow-[var(--taali-shadow-soft)]">
    <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">{label}</div>
    <div className="mt-2 taali-display text-2xl font-semibold text-[var(--taali-text)]">{value}</div>
  </div>
);

const ProductFrame = () => (
  <Panel className="overflow-hidden bg-[linear-gradient(145deg,rgba(255,255,255,0.98),rgba(242,236,255,0.86))] p-0">
    <div className="border-b border-[var(--taali-border-soft)] px-5 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">Candidate assessment review</div>
          <div className="mt-2 taali-display text-2xl font-semibold text-[var(--taali-text)]">Engineering Manager - PR Review</div>
        </div>
        <Badge variant="purple">Assessment + CV</Badge>
      </div>
    </div>

    <div className="grid gap-4 px-5 py-5 xl:grid-cols-[1.2fr_0.8fr]">
      <div className="space-y-4">
        <Card className="bg-[rgba(255,255,255,0.74)] p-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">TAALI score</div>
              <div className="mt-2 taali-display text-5xl font-semibold text-[var(--taali-text)]">87.7</div>
              <p className="mt-3 max-w-sm text-sm leading-6 text-[var(--taali-muted)]">
                TAALI blends assessment execution with role-fit evidence so recruiters get one decision score and the reasoning behind it.
              </p>
            </div>
            <div className="rounded-[var(--taali-radius-card)] bg-[linear-gradient(180deg,#161127,#0f1220)] px-4 py-3 text-right text-white shadow-[var(--taali-shadow-soft)]">
              <div className="text-[11px] font-semibold uppercase tracking-[0.1em] text-white/60">Recommendation</div>
              <div className="mt-2 text-lg font-semibold">Strong Hire</div>
            </div>
          </div>
        </Card>

        <div className="grid gap-3 md:grid-cols-3">
          <HeroMetric label="Assessment score" value="85.4" />
          <HeroMetric label="CV fit" value="83.3" />
          <HeroMetric label="Requirements fit" value="80.0" />
        </div>

        <Card className="bg-[rgba(255,255,255,0.76)] p-4">
          <div className="mb-3 flex items-center justify-between gap-2">
            <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">Why this score</div>
            <Badge variant="success">Evidence-backed</Badge>
          </div>
          <div className="grid gap-3">
            {[
              'Strong role-fit evidence across Applied AI, MLOps, and data platform delivery.',
              'Leadership history maps well to the scope of the role and team expectations.',
              'Generative AI experience is present but still lighter than the must-have requirement.',
            ].map((item) => (
              <div key={item} className="flex items-start gap-3 rounded-[var(--taali-radius-card)] bg-[var(--taali-surface-subtle)] px-3 py-3">
                <span className="mt-1 h-2 w-2 rounded-full bg-[var(--taali-purple)]" />
                <p className="text-sm leading-6 text-[var(--taali-text)]">{item}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="space-y-4">
        <Card className="bg-[linear-gradient(180deg,#161127,#0f1220)] p-4 text-white">
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-white/60">
            <Workflow size={14} />
            Recruiter review flow
          </div>
          <div className="mt-4 space-y-3">
            {[
              ['Overview', 'Decision score, recommendation, strongest and weakest dimensions'],
              ['Evidence', 'Requirement coverage, rationale bullets, and requirement-level impact'],
              ['Integrity', 'Fraud signals and original uncapped score when adjustments apply'],
            ].map(([title, text]) => (
              <div key={title} className="rounded-[var(--taali-radius-card)] border border-white/10 bg-white/5 px-3 py-3">
                <div className="font-semibold">{title}</div>
                <p className="mt-1 text-sm leading-6 text-white/72">{text}</p>
              </div>
            ))}
          </div>
        </Card>

        <Card className="bg-[rgba(255,247,235,0.82)] p-4">
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">
            <ShieldCheck size={14} />
            Integrity and retakes
          </div>
          <p className="mt-3 text-sm leading-6 text-[var(--taali-text)]">
            Superseded attempts remain visible in history, but the completed assessment stays the single source of truth for recruiter-facing review.
          </p>
        </Card>
      </div>
    </div>
  </Panel>
);

const HeroSection = ({ onNavigate }) => (
  <section className="relative overflow-hidden">
    <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(157,0,255,0.14),transparent_28%),radial-gradient(circle_at_bottom_left,rgba(255,196,89,0.12),transparent_24%)]" />
    <PageContainer className="relative grid gap-10 pb-10 pt-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:items-center lg:pb-16 lg:pt-12">
      <div>
        <Badge variant="purple" className="mb-5">AI-native technical assessments</Badge>
        <h1 className="taali-display max-w-3xl text-5xl font-semibold leading-[0.98] text-[var(--taali-text)] md:text-7xl">
          Hire for modern engineering judgment, not interview theater.
        </h1>
        <p className="mt-6 max-w-2xl text-lg leading-8 text-[var(--taali-muted)] md:text-xl">
          TAALI evaluates how candidates work with coding agents, real repo context, tests, and constraints, then turns that activity into a recruiter-ready review surface.
        </p>

        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Button type="button" variant="primary" size="lg" onClick={() => onNavigate('demo')}>
            See the product
            <ArrowRight size={16} />
          </Button>
          <Button type="button" variant="secondary" size="lg" onClick={() => onNavigate('login')}>
            Start hiring
          </Button>
          <Button type="button" variant="ghost" size="lg" onClick={() => scrollToId('framework')}>
            <Play size={16} />
            View scoring framework
          </Button>
        </div>

        <div className="mt-8 grid gap-3 sm:grid-cols-3">
          <HeroMetric label="Review time" value="< 5 min" />
          <HeroMetric label="Score format" value="One source" />
          <HeroMetric label="Retakes" value="Prompt-based" />
        </div>
      </div>

      <div className="relative">
        <ProductFrame />
      </div>
    </PageContainer>
  </section>
);

const TrustStrip = () => (
  <section className="border-y border-[var(--taali-border-soft)] bg-[rgba(255,255,255,0.62)] backdrop-blur-sm">
    <PageContainer className="py-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {TRUST_ITEMS.map((item) => (
          <div key={item} className="flex items-start gap-3 rounded-[var(--taali-radius-card)] bg-[rgba(255,255,255,0.72)] px-4 py-3">
            <Sparkles size={16} className="mt-0.5 shrink-0 text-[var(--taali-purple)]" />
            <p className="text-sm leading-6 text-[var(--taali-text)]">{item}</p>
          </div>
        ))}
      </div>
    </PageContainer>
  </section>
);

const FeatureBand = ({ band, reverse = false }) => (
  <section id={band.id} className="py-8 lg:py-12">
    <PageContainer className={`grid gap-6 lg:grid-cols-2 lg:items-center ${reverse ? 'lg:[&>*:first-child]:order-2' : ''}`}>
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--taali-purple)]">{band.eyebrow}</div>
        <h2 className="taali-display mt-3 text-4xl font-semibold text-[var(--taali-text)] md:text-5xl">{band.title}</h2>
        <p className="mt-4 max-w-2xl text-base leading-8 text-[var(--taali-muted)] md:text-lg">{band.copy}</p>
        <div className="mt-6 grid gap-3">
          {band.bullets.map((item) => (
            <div key={item} className="flex items-start gap-3 rounded-[var(--taali-radius-card)] bg-[rgba(255,255,255,0.72)] px-4 py-3 shadow-[var(--taali-shadow-soft)]">
              <Check size={16} className="mt-1 shrink-0 text-[var(--taali-purple)]" />
              <p className="text-sm leading-6 text-[var(--taali-text)]">{item}</p>
            </div>
          ))}
        </div>
      </div>

      <Panel className="overflow-hidden bg-[linear-gradient(145deg,rgba(255,255,255,0.96),rgba(245,240,255,0.82))] p-5">
        <div className="grid gap-4">
          {band.id === 'product' ? (
            <>
              <Card className="bg-[rgba(255,255,255,0.78)] p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">Session evidence</div>
                    <div className="mt-2 taali-display text-3xl font-semibold text-[var(--taali-text)]">Prompt trace + test deltas</div>
                  </div>
                  <Badge variant="success">Live signal</Badge>
                </div>
              </Card>
              <div className="grid gap-3 md:grid-cols-3">
                <Card className="p-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">Prompt clarity</div>
                  <div className="mt-2 text-2xl font-semibold text-[var(--taali-text)]">9.1/10</div>
                </Card>
                <Card className="p-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">Context provision</div>
                  <div className="mt-2 text-2xl font-semibold text-[var(--taali-text)]">8.7/10</div>
                </Card>
                <Card className="p-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">Response utilization</div>
                  <div className="mt-2 text-2xl font-semibold text-[var(--taali-text)]">8.6/10</div>
                </Card>
              </div>
            </>
          ) : (
            <>
              <Card className="bg-[linear-gradient(180deg,#161127,#0f1220)] p-4 text-white">
                <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-white/60">Recruiter summary</div>
                <p className="mt-3 text-sm leading-7 text-white/80">
                  Candidate demonstrates strong judgment in PR triage, good use of coding agents for verification, and credible role fit for data-heavy leadership work. Probe depth of hands-on generative AI delivery before final decision.
                </p>
              </Card>
              <div className="grid gap-3 md:grid-cols-2">
                <Card className="p-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">Requirement evidence</div>
                  <p className="mt-2 text-sm leading-6 text-[var(--taali-text)]">
                    Requirement-level cards show priority, evidence, and impact so the hiring manager can quickly validate the recommendation.
                  </p>
                </Card>
                <Card className="p-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">Assessment history</div>
                  <p className="mt-2 text-sm leading-6 text-[var(--taali-text)]">
                    Current, voided, and superseded attempts remain visible without polluting the main review surface.
                  </p>
                </Card>
              </div>
            </>
          )}
        </div>
      </Panel>
    </PageContainer>
  </section>
);

const FrameworkSection = () => {
  const dimensions = dimensionOrder.map((id) => ({
    key: id,
    title: getDimensionById(id).label,
    description: getDimensionById(id).shortDescription,
  }));

  return (
    <section id="framework" className="py-8 lg:py-12">
      <PageContainer className="space-y-6">
        <div className="max-w-3xl">
          <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--taali-purple)]">SCORING FRAMEWORK</div>
          <h2 className="taali-display mt-3 text-4xl font-semibold text-[var(--taali-text)] md:text-5xl">
            A rubric built for AI-native delivery, not toy questions.
          </h2>
          <p className="mt-4 text-base leading-8 text-[var(--taali-muted)] md:text-lg">
            TAALI scores task completion, prompt strategy, context, debugging, communication, and role fit, then turns those signals into a single recruiter-facing review.
          </p>
        </div>

        <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
          <Panel className="p-5">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">Dimension profile</div>
                <div className="mt-2 taali-display text-2xl font-semibold text-[var(--taali-text)]">Two candidates. Same task. Different signal.</div>
              </div>
              <Badge variant="muted">0.0 to 10.0 rubric</Badge>
            </div>

            <div className="h-[340px]">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart data={demoComparison} outerRadius="74%">
                  <PolarGrid stroke="var(--taali-purple-soft)" />
                  <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 11, fill: 'var(--taali-muted)', fontFamily: 'var(--taali-font)' }} />
                  <PolarRadiusAxis domain={[0, 10]} tick={{ fontSize: 10, fill: 'var(--taali-muted)' }} />
                  <Radar name="AI-native product engineer" dataKey="candidateA" stroke="var(--taali-purple)" fill="var(--taali-purple)" fillOpacity={0.18} />
                  <Radar name="Backend engineer" dataKey="candidateB" stroke="#64748b" fill="#64748b" fillOpacity={0.08} />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          </Panel>

          <div className="grid gap-4">
            <Card className="p-4">
              <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">Candidate A</div>
              <div className="mt-2 taali-display text-2xl font-semibold text-[var(--taali-text)]">Agent-native product builder</div>
              <p className="mt-3 text-sm leading-6 text-[var(--taali-text)]">
                Strong context framing, fast feedback loops, and consistent use of agent output to move the solution forward.
              </p>
            </Card>
            <Card className="p-4">
              <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">Candidate B</div>
              <div className="mt-2 taali-display text-2xl font-semibold text-[var(--taali-text)]">Code-strong backend operator</div>
              <p className="mt-3 text-sm leading-6 text-[var(--taali-text)]">
                Good debugging depth and role fit, but weaker prompt clarity, slower iteration, and less effective agent collaboration.
              </p>
            </Card>
          </div>
        </div>

        <ScoringCardGrid items={dimensions} className="md:grid-cols-2 xl:grid-cols-4" cardClassName="!p-5" />
      </PageContainer>
    </section>
  );
};

const ProofSection = () => (
  <section className="py-8 lg:py-12">
    <PageContainer className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
      <Panel className="bg-[linear-gradient(180deg,#161127,#0f1220)] p-6 text-white">
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-white/60">RECRUITER EXPERIENCE</div>
        <h2 className="taali-display mt-3 text-4xl font-semibold text-white">One calm review surface instead of five disconnected ones.</h2>
        <p className="mt-4 text-base leading-8 text-white/74">
          TAALI is designed so the dashboard, candidate detail page, sidebar, and landing experience all feel like the same product: technical, clear, and decisive.
        </p>
      </Panel>

      <div className="grid gap-4 md:grid-cols-3">
        {PROOF_POINTS.map((item) => (
          <Card key={item.title} className="p-5">
            <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-purple)]">{item.title}</div>
            <p className="mt-3 text-sm leading-7 text-[var(--taali-text)]">{item.description}</p>
          </Card>
        ))}
      </div>
    </PageContainer>
  </section>
);

const PricingSection = ({ onNavigate }) => (
  <section id="pricing" className="py-8 lg:py-12">
    <PageContainer className="space-y-6">
      <div className="max-w-3xl">
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--taali-purple)]">PRICING</div>
        <h2 className="taali-display mt-3 text-4xl font-semibold text-[var(--taali-text)] md:text-5xl">
          Simple pricing for high-signal technical hiring.
        </h2>
        <p className="mt-4 text-base leading-8 text-[var(--taali-muted)] md:text-lg">
          Start pay-as-you-go, or move to a team plan when you want deeper workflow support, custom tasks, and ATS integration work.
        </p>
      </div>

      <div className="grid gap-5 lg:grid-cols-[1.05fr_0.95fr]">
        <Panel className="relative overflow-hidden bg-[linear-gradient(145deg,rgba(255,255,255,0.98),rgba(242,236,255,0.86))] p-6">
          <Badge variant="purple" className="mb-4">Most flexible</Badge>
          <div className="taali-display text-3xl font-semibold text-[var(--taali-text)]">Pay as you go</div>
          <div className="mt-3 taali-display text-6xl font-semibold text-[var(--taali-text)]">AED 59</div>
          <p className="mt-1 text-sm text-[var(--taali-muted)]">Per assessment, approximately ${aedToUsd(59)} USD. Invoiced in AED.</p>
          <div className="mt-6 grid gap-3 md:grid-cols-2">
            {[
              'Real coding environment with coding-agent support',
              'Automated scoring plus dimension-level evidence',
              'Recruiter-ready candidate summary and report',
              'Email support and fast setup',
            ].map((feature) => (
              <div key={feature} className="flex items-start gap-3 rounded-[var(--taali-radius-card)] bg-[rgba(255,255,255,0.76)] px-4 py-3">
                <Check size={16} className="mt-1 shrink-0 text-[var(--taali-purple)]" />
                <p className="text-sm leading-6 text-[var(--taali-text)]">{feature}</p>
              </div>
            ))}
          </div>
          <div className="mt-6 flex flex-wrap gap-3">
            <Button type="button" variant="primary" size="lg" onClick={() => onNavigate('login')}>
              Buy credits
            </Button>
            <Button type="button" variant="secondary" size="lg" onClick={() => onNavigate('demo')}>
              Talk to sales
            </Button>
          </div>
        </Panel>

        <Panel className="p-6">
          <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--taali-purple)]">TEAMS AND ENTERPRISE</div>
          <div className="mt-3 taali-display text-3xl font-semibold text-[var(--taali-text)]">Need a tailored workflow?</div>
          <p className="mt-4 text-base leading-8 text-[var(--taali-muted)]">
            We support agencies, growing product teams, and enterprise hiring groups that need custom tasks, ATS integrations, invoicing, or volume pricing.
          </p>
          <div className="mt-6 grid gap-3">
            {[
              'Custom assessment templates mapped to your roles',
              'Structured recruiter summary pages and reporting support',
              'Guidance on rollout, rubric calibration, and hiring process fit',
            ].map((item) => (
              <Card key={item} className="p-4">
                <p className="text-sm leading-6 text-[var(--taali-text)]">{item}</p>
              </Card>
            ))}
          </div>
        </Panel>
      </div>
    </PageContainer>
  </section>
);

const Footer = () => (
  <footer className="border-t border-[var(--taali-border-soft)] bg-[linear-gradient(180deg,#111827,#0b1220)] text-[var(--taali-inverse-text)]">
    <div className="mx-auto flex max-w-7xl flex-wrap items-end justify-between gap-6 px-6 py-10">
      <div>
        <div className="flex items-center gap-3">
          <BrandGlyph borderClass="border-white/10" />
          <div>
            <div className="taali-display text-2xl font-semibold text-white">{BRAND.name}</div>
            <p className="mt-1 text-sm text-white/60">{BRAND.productTagline}</p>
          </div>
        </div>
      </div>

      <div className="text-sm text-white/60">
        Questions?{' '}
        <a href={`mailto:hello@${BRAND.domain}`} className="text-white underline underline-offset-4">
          hello@{BRAND.domain}
        </a>
      </div>
    </div>
  </footer>
);

export const LandingPage = ({ onNavigate }) => (
  <div className="min-h-screen bg-[var(--taali-bg)] text-[var(--taali-text)]">
    <LandingNav onNavigate={onNavigate} />
    <HeroSection onNavigate={onNavigate} />
    <TrustStrip />
    {FEATURE_BANDS.map((band, index) => (
      <FeatureBand key={band.id} band={band} reverse={index % 2 === 1} />
    ))}
    <FrameworkSection />
    <ProofSection />
    <PricingSection onNavigate={onNavigate} />
    <Footer />
  </div>
);

export default LandingPage;
