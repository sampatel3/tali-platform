import React, { Suspense, lazy, useEffect, useState } from 'react';
import {
  ArrowRight,
  Check,
  Menu,
  Play,
  Sparkles,
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
import { readDarkModePreference, subscribeThemePreference } from '../../lib/themePreference';
import { Logo } from '../../shared/ui/Branding';
import { GlobalThemeToggle } from '../../shared/ui/GlobalThemeToggle';
import {
  Badge,
  Button,
  Card,
  PageContainer,
  Panel,
} from '../../shared/ui/TaaliPrimitives';
import { ScoringCardGrid } from '../../shared/ui/ScoringCardGrid';
import { dimensionOrder, getDimensionById } from '../../scoring/scoringDimensions';

const AssessmentRuntimePreviewView = lazy(() =>
  import('../assessment_runtime/AssessmentRuntimePreviewView').then((module) => ({ default: module.AssessmentRuntimePreviewView }))
);
const CandidateResultsPreviewView = lazy(() =>
  import('../candidates/CandidateResultsPreviewView').then((module) => ({ default: module.CandidateResultsPreviewView }))
);

const TRUST_ITEMS = [
  'Built for modern technical hiring, not outdated coding tests',
  'Assess AI-native skillsets through prompt framing, context, validation, and recovery',
  'Make defensible shortlist decisions with role fit, TAALI score, and interview probes',
  'Share a client-ready report employers can use in real decision meetings',
];

const AUDIENCE_ITEMS = [
  {
    title: 'Recruiting agencies',
    description: 'Send stronger shortlists with proof. TAALI helps agencies show why a candidate is worth moving forward, not just that they passed a task.',
  },
  {
    title: 'In-house talent teams',
    description: 'Hire engineers for modern product, platform, data, and AI roles where success depends on using AI tools well under real delivery pressure.',
  },
  {
    title: 'Hiring managers and technical leaders',
    description: 'See how candidates scope the work, guide agents, validate outputs, and make tradeoffs before you invest in deeper interview loops.',
  },
  {
    title: 'Teams building AI-native products',
    description: 'Benchmark the operating style you actually need: prompt clarity, context quality, execution judgment, communication, and role fit.',
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
        <div className="border-t border-[var(--taali-border-soft)] bg-[var(--taali-surface)] px-6 py-4 backdrop-blur-md md:hidden">
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

const SurfacePreviewFallback = ({ heightClass = 'h-[32rem]' }) => (
  <Panel className={`overflow-hidden bg-[linear-gradient(145deg,var(--taali-surface),var(--taali-surface-subtle))] p-0 ${heightClass}`}>
    <div className="flex h-full flex-col justify-between p-5">
      <div className="space-y-3">
        <div className="h-4 w-40 rounded-full bg-[var(--taali-border-subtle)]" />
        <div className="h-10 w-3/4 rounded-[var(--taali-radius-card)] bg-[var(--taali-border-subtle)]" />
        <div className="grid gap-2 md:grid-cols-2">
          <div className="h-28 rounded-[var(--taali-radius-card)] bg-[var(--taali-surface-subtle)]" />
          <div className="h-28 rounded-[var(--taali-radius-card)] bg-[var(--taali-surface-subtle)]" />
        </div>
      </div>
      <div className="grid gap-2 md:grid-cols-3">
        <div className="h-20 rounded-[var(--taali-radius-card)] bg-[var(--taali-surface-subtle)]" />
        <div className="h-20 rounded-[var(--taali-radius-card)] bg-[var(--taali-surface-subtle)]" />
        <div className="h-20 rounded-[var(--taali-radius-card)] bg-[var(--taali-surface-subtle)]" />
      </div>
    </div>
  </Panel>
);

const AmbientProductShowcase = ({
  children,
  heightClass = 'h-[min(68vh,38rem)]',
  scale = 1,
}) => (
  <div className={`pointer-events-none overflow-hidden rounded-[2rem] border border-[var(--taali-border-soft)] bg-[linear-gradient(180deg,var(--taali-surface-warm),var(--taali-surface-subtle))] p-4 shadow-[var(--taali-shadow-strong)] ${heightClass} md:p-6`}>
    <div className="h-full overflow-hidden rounded-[1.5rem] border border-[var(--taali-border-soft)] bg-[var(--taali-surface-elevated)] shadow-[var(--taali-shadow-soft)]">
      <div
        className="h-full"
        style={{
          transform: scale === 1 ? undefined : `scale(${scale})`,
          transformOrigin: 'top left',
          width: scale === 1 ? '100%' : `${100 / scale}%`,
        }}
      >
        {children}
      </div>
    </div>
  </div>
);

const HeroSection = ({ onNavigate }) => (
  <section className="relative overflow-hidden">
    <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(157,0,255,0.14),transparent_28%),radial-gradient(circle_at_bottom_left,rgba(255,196,89,0.12),transparent_24%)]" />
    <PageContainer className="relative pb-8 pt-5 lg:pb-10 lg:pt-6" width="wide">
      <div className="max-w-[58rem]">
        <Badge variant="purple" className="mb-4">Hire AI-native engineers with evidence</Badge>
        <h1 className="taali-display max-w-[52rem] text-[3rem] font-semibold leading-[0.97] text-[var(--taali-text)] md:text-[4.55rem]">
          Assess the skills modern engineering teams actually need.
          <br />
          Hire AI-native talent with TAALI.
        </h1>
        <p className="mt-4 max-w-[46rem] text-[1rem] leading-7 text-[var(--taali-muted)] md:text-[1.08rem]">
          TAALI is built for recruiters, agencies, hiring managers, and technical leaders evaluating engineers who work with Cursor, Codex, Claude Code, and modern AI workflows. It turns AI-assisted technical work into hiring signal you can rank, defend, and share.
        </p>

        <div className="mt-6 flex flex-wrap items-center gap-2.5">
          <Button type="button" variant="primary" size="md" onClick={() => onNavigate('demo')}>
            See the product
            <ArrowRight size={16} />
          </Button>
          <Button type="button" variant="secondary" size="md" onClick={() => onNavigate('login')}>
            Start hiring
          </Button>
          <Button type="button" variant="ghost" size="md" onClick={() => scrollToId('framework')}>
            <Play size={16} />
            View scoring framework
          </Button>
        </div>
      </div>
    </PageContainer>
  </section>
);

const AudienceSection = () => (
  <section className="pb-8 lg:pb-10">
    <PageContainer className="space-y-5" width="wide">
      <div className="max-w-[62rem]">
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--taali-purple)]">WHO IT&apos;S FOR</div>
        <h2 className="taali-display mt-3 text-4xl font-semibold text-[var(--taali-text)] md:text-5xl">
          Made for teams hiring engineers who already work with AI.
        </h2>
        <p className="mt-4 text-base leading-8 text-[var(--taali-muted)] md:text-lg">
          Once you understand the rubric, the fit is straightforward: TAALI is for teams hiring engineers whose real job already depends on AI-assisted execution, judgment, and delivery quality.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {AUDIENCE_ITEMS.map((item) => (
          <Card key={item.title} className="p-5">
            <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-purple)]">{item.title}</div>
            <p className="mt-3 text-sm leading-7 text-[var(--taali-text)]">{item.description}</p>
          </Card>
        ))}
      </div>
    </PageContainer>
  </section>
);

const AssessmentExperienceSection = ({ darkMode }) => (
  <section id="product" className="pb-8 lg:pb-12">
    <PageContainer className="space-y-5" width="wide">
      <div className="max-w-[62rem]">
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--taali-purple)]">ASSESSMENT RUNTIME</div>
        <h2 className="taali-display mt-3 text-4xl font-semibold text-[var(--taali-text)] md:text-5xl">
          Real Tasks. Real Signal.
        </h2>
        <p className="mt-4 text-base leading-8 text-[var(--taali-muted)] md:text-lg">
          TAALI drops candidates into role-traceable engineering work with real repo context, meaningful failure shape, and full telemetry. You see how they frame prompts, use AI, validate decisions, and recover under delivery pressure, so you can assess AI-native execution instead of guessing from a take-home score.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <Badge variant="muted">Role-traceable scenario</Badge>
        <Badge variant="muted">Prompt + diff telemetry</Badge>
        <Badge variant="muted">Deliberate failure shape</Badge>
        <Badge variant="muted">Evidence without replay</Badge>
      </div>

      <AmbientProductShowcase heightClass="h-[min(72vh,44rem)]">
        <Suspense fallback={<SurfacePreviewFallback heightClass="h-[min(68vh,42rem)]" />}>
          <AssessmentRuntimePreviewView
            heightClass="h-[min(72vh,44rem)]"
            defaultCollapsedSections={{ contextWindow: true }}
            lightMode={darkMode}
          />
        </Suspense>
      </AmbientProductShowcase>
    </PageContainer>
  </section>
);

const CandidateSummarySection = ({ darkMode }) => (
  <section className="py-8 lg:py-12">
    <PageContainer className="space-y-5" width="wide">
      <div className="max-w-[62rem]">
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--taali-purple)]">RECRUITER REVIEW</div>
        <h2 className="taali-display mt-3 text-4xl font-semibold text-[var(--taali-text)] md:text-5xl">
          Faster Recruitment Decisions.
        </h2>
        <p className="mt-4 text-base leading-8 text-[var(--taali-muted)] md:text-lg">
          TAALI turns raw assessment activity into a benchmarked recruiter readout with recommendation, risk, and evidence in one place. Hiring managers get clear probe points, agencies get stronger shortlist confidence, and employers get proof they can trust without replaying the session.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <Badge variant="muted">Hire AI-native engineers faster</Badge>
        <Badge variant="muted">Benchmark + percentile context</Badge>
        <Badge variant="muted">Employer-ready proof</Badge>
        <Badge variant="muted">Interview probes that matter</Badge>
      </div>

      <AmbientProductShowcase heightClass="h-[min(56vh,31rem)]">
        <Suspense fallback={<SurfacePreviewFallback heightClass="h-[min(82vh,56rem)]" />}>
          <CandidateResultsPreviewView
            className="h-full"
            maxHeightClass="max-h-[26rem]"
            scaleClassName="scale-[0.76]"
            scaledWidth="131.6%"
            lightMode={darkMode}
          />
        </Suspense>
      </AmbientProductShowcase>
    </PageContainer>
  </section>
);

const TrustStrip = () => (
  <section className="border-y border-[var(--taali-border-soft)] bg-[var(--taali-surface)] backdrop-blur-sm">
    <PageContainer className="py-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {TRUST_ITEMS.map((item) => (
          <div key={item} className="flex items-start gap-3 rounded-[var(--taali-radius-card)] bg-[var(--taali-surface)] px-4 py-3">
            <Sparkles size={16} className="mt-0.5 shrink-0 text-[var(--taali-purple)]" />
            <p className="text-sm leading-6 text-[var(--taali-text)]">{item}</p>
          </div>
        ))}
      </div>
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
          A rubric built to measure AI-native engineering judgment.
        </h2>
          <p className="mt-4 text-base leading-8 text-[var(--taali-muted)] md:text-lg">
            TAALI scores task completion, prompt strategy, context, debugging, communication, and role fit, then turns those signals into a single recruiter-facing review of AI-native engineering skill.
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
                  <Radar name="Backend engineer" dataKey="candidateB" stroke="var(--taali-muted)" fill="var(--taali-muted)" fillOpacity={0.08} />
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
        <Panel className="relative overflow-hidden bg-[linear-gradient(145deg,var(--taali-surface),var(--taali-surface-subtle))] p-6">
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
              <div key={feature} className="flex items-start gap-3 rounded-[var(--taali-radius-card)] bg-[var(--taali-surface)] px-4 py-3">
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
  <footer className="border-t border-[var(--taali-border-soft)] bg-[linear-gradient(180deg,var(--taali-surface),var(--taali-surface-subtle))] text-[var(--taali-text)]">
    <div className="mx-auto flex max-w-7xl flex-wrap items-end justify-between gap-6 px-6 py-10">
      <div>
        <div>
          <Logo />
          <div className="pl-[3.25rem]">
            <p className="mt-1 text-sm text-[var(--taali-muted)]">{BRAND.productTagline}</p>
          </div>
        </div>
      </div>

      <div className="text-sm text-[var(--taali-muted)]">
        Questions?{' '}
        <a href={`mailto:hello@${BRAND.domain}`} className="text-[var(--taali-text)] underline underline-offset-4">
          hello@{BRAND.domain}
        </a>
      </div>
    </div>
  </footer>
);

export const LandingPage = ({ onNavigate }) => {
  const [darkMode, setDarkMode] = useState(() => readDarkModePreference());

  useEffect(() => (
    subscribeThemePreference((next) => {
      setDarkMode(Boolean(next));
    })
  ), []);

  return (
    <div className="min-h-screen bg-[var(--taali-bg)] text-[var(--taali-text)]">
      <LandingNav onNavigate={onNavigate} />
      <HeroSection onNavigate={onNavigate} />
      <TrustStrip />
      <AssessmentExperienceSection darkMode={darkMode} />
      <CandidateSummarySection darkMode={darkMode} />
      <FrameworkSection />
      <AudienceSection />
      <PricingSection onNavigate={onNavigate} />
      <Footer />
    </div>
  );
};

export default LandingPage;
