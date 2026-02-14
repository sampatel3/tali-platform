import React, { useState } from 'react';
import { Check, CheckCircle, Menu, X, XCircle } from 'lucide-react';
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

const LandingNav = ({ onNavigate }) => {
  const [mobileOpen, setMobileOpen] = useState(false);

  const scrollTo = (id) => {
    setMobileOpen(false);
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <nav className="border-b-2 border-black bg-white">
      <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
        <Logo onClick={() => onNavigate('landing')} />
        <button
          className="md:hidden border-2 border-black p-2 hover:bg-black hover:text-white transition-colors"
          onClick={() => setMobileOpen(!mobileOpen)}
        >
          {mobileOpen ? <X size={20} /> : <Menu size={20} />}
        </button>
        <div className="hidden md:flex items-center gap-6">
          <button className="font-mono text-sm hover:underline" onClick={() => scrollTo('why-taali')}>Why TAALI</button>
          <button className="font-mono text-sm hover:underline" onClick={() => scrollTo('how-taali')}>How TAALI</button>
          <button className="font-mono text-sm hover:underline" onClick={() => scrollTo('pricing')}>Pricing</button>
          <button className="font-mono text-sm hover:underline" onClick={() => alert('Documentation coming soon')}>Docs</button>
          <button
            className="border-2 border-black px-4 py-2 font-mono text-sm hover:bg-black hover:text-white transition-colors"
            onClick={() => onNavigate('login')}
          >
            Sign In
          </button>
          <button
            className="border-2 border-black px-4 py-2 font-mono text-sm text-white hover:bg-black transition-colors"
            style={{ backgroundColor: '#9D00FF' }}
            onClick={() => onNavigate('login')}
          >
            Start Free Trial
          </button>
        </div>
      </div>
      {mobileOpen && (
        <div className="md:hidden border-t-2 border-black bg-white px-6 py-4 space-y-3">
          <button className="block w-full text-left font-mono text-sm py-2 hover:underline" onClick={() => scrollTo('why-taali')}>Why TAALI</button>
          <button className="block w-full text-left font-mono text-sm py-2 hover:underline" onClick={() => scrollTo('how-taali')}>How TAALI</button>
          <button className="block w-full text-left font-mono text-sm py-2 hover:underline" onClick={() => scrollTo('pricing')}>Pricing</button>
          <button className="block w-full text-left font-mono text-sm py-2 hover:underline" onClick={() => alert('Documentation coming soon')}>Docs</button>
          <button
            className="block w-full border-2 border-black px-4 py-2 font-mono text-sm hover:bg-black hover:text-white transition-colors text-center"
            onClick={() => {
              setMobileOpen(false);
              onNavigate('login');
            }}
          >
            Sign In
          </button>
          <button
            className="block w-full border-2 border-black px-4 py-2 font-mono text-sm text-white hover:bg-black transition-colors text-center"
            style={{ backgroundColor: '#9D00FF' }}
            onClick={() => {
              setMobileOpen(false);
              onNavigate('login');
            }}
          >
            Start Free Trial
          </button>
        </div>
      )}
    </nav>
  );
};

const HeroSection = ({ onNavigate }) => (
  <section className="border-b-2 border-black bg-white">
    <div className="max-w-7xl mx-auto px-6 py-20 grid md:grid-cols-2 gap-12 items-center">
      <div>
        <div
          className="inline-block px-4 py-2 text-xs font-mono font-bold text-white border-2 border-black mb-8"
          style={{ backgroundColor: '#9D00FF' }}
        >
          BUILT FOR AI-NATIVE ENGINEERING TEAMS
        </div>
        <h1 className="text-5xl lg:text-6xl font-bold leading-tight mb-6">
          Stop hiring for yesterday&apos;s skills.
          <br />
          {' '}Start hiring with TAALI.
        </h1>
        <p className="text-xl lg:text-2xl font-mono text-gray-700 mb-8 leading-relaxed">
          Assess how engineers actually work today using Cursor, Claude Code, and OpenAI Codex on real tasks, in real workflows.
        </p>
        <div className="flex flex-wrap gap-4">
          <button
            className="border-2 border-black px-8 py-4 font-bold text-lg text-white hover:bg-black transition-colors"
            style={{ backgroundColor: '#9D00FF' }}
            onClick={() => onNavigate('login')}
          >
            Book a Demo
          </button>
          <button
            className="border-2 border-black bg-white px-8 py-4 font-bold text-lg hover:bg-black hover:text-white transition-colors"
            onClick={() => onNavigate('login')}
          >
            Start Free Trial
          </button>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div className="border-2 border-black bg-gray-100 p-4">
          <div className="flex items-center gap-2 mb-3">
            <XCircle size={18} className="text-red-500" />
            <span className="font-mono text-xs font-bold text-gray-500">LEGACY TEST</span>
          </div>
          <pre className="font-mono text-xs text-gray-600 leading-relaxed">{`function reverseList(head) {
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
        </div>
        <div className="border-2 border-black bg-black p-4">
          <div className="flex items-center gap-2 mb-3">
            <CheckCircle size={18} style={{ color: '#9D00FF' }} />
            <span className="font-mono text-xs font-bold" style={{ color: '#9D00FF' }}>{BRAND.name} FLOW</span>
          </div>
          <pre className="font-mono text-xs leading-relaxed" style={{ color: '#9D00FF' }}>{`> Ask Codex for a
  test scaffold

> Use Claude Code to
  trace the parser

> Ship fix with
  green tests`}</pre>
        </div>
        <div className="border-2 border-black bg-gray-100 p-4">
          <div className="font-mono text-xs text-gray-500 mb-2">TESTS FOR:</div>
          <div className="font-mono text-sm text-gray-700">Algorithm recall</div>
          <div className="font-mono text-sm text-gray-700">Interview theatrics</div>
          <div className="font-mono text-sm text-red-500 mt-2 font-bold">NOT delivery quality</div>
        </div>
        <div className="border-2 border-black bg-black p-4">
          <div className="font-mono text-xs mb-2" style={{ color: '#9D00FF' }}>TESTS FOR:</div>
          <div className="font-mono text-sm text-white">Agent collaboration</div>
          <div className="font-mono text-sm text-white">Debugging in context</div>
          <div className="font-mono text-sm font-bold mt-2" style={{ color: '#9D00FF' }}>ACTUAL execution</div>
        </div>
      </div>
    </div>
  </section>
);

const WhyTaaliSection = () => (
  <section id="why-taali" className="border-b-2 border-black bg-white">
    <div className="max-w-7xl mx-auto px-6 py-20">
      <h2 className="text-4xl font-bold text-center mb-4">Why {BRAND.name}</h2>
      <p className="text-center font-mono text-gray-700 mb-3 max-w-4xl mx-auto">Engineering changed. Hiring didn&apos;t.</p>
      <p className="text-center font-mono text-gray-700 mb-3 max-w-4xl mx-auto">
        Modern engineers don&apos;t work in a blank editor. They work in real repos, with modern coding agents, through fast
        iteration and validation. {BRAND.name} helps teams hire for that reality.
      </p>
      <p className="text-center font-mono text-gray-700 mb-3 max-w-4xl mx-auto">
        We believe modern engineering hiring should measure how people think and collaborate with AI, not how well they perform in
        artificial interview formats.
      </p>
      <p className="text-center font-mono text-gray-600 mb-12 max-w-4xl mx-auto">
        Our mission is to help teams hire engineers who can prompt well, reason clearly, and ship reliable outcomes with coding agents.
      </p>

      <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6">
        {[
          ['Prompting is a core skill', 'As agents automate more implementation work, prompt strategy increasingly determines output quality.'],
          ['Conversation is signal', 'Agent conversations reveal collaboration quality, ownership, and communication maturity.'],
          ['Prompting exposes thinking', 'How candidates frame requests surfaces critical thinking, design judgment, and tradeoff awareness.'],
          ['Efficiency shows experience', 'Strong candidates get better outcomes with fewer, sharper prompts and tighter iteration loops.'],
        ].map(([title, description]) => (
          <div key={title} className="border-2 border-black bg-gray-50 p-6">
            <h3 className="text-lg font-bold mb-2">{title}</h3>
            <p className="font-mono text-sm text-gray-700">{description}</p>
          </div>
        ))}
      </div>
    </div>
  </section>
);

const HowTaaliSection = () => {
  const dimensions = dimensionOrder.map((id) => {
    const definition = getDimensionById(id);
    return [definition.label, definition.shortDescription];
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
    <section id="how-taali" className="border-b-2 border-black bg-white">
      <div className="max-w-7xl mx-auto px-6 py-20">
        <h2 className="text-4xl font-bold text-center mb-4">How {BRAND.name} Evaluates Candidates</h2>
        <p className="text-center font-mono text-gray-700 mb-3 max-w-4xl mx-auto">Real session behavior -&gt; structured scorecard</p>
        <p className="text-center font-mono text-gray-700 mb-3 max-w-4xl mx-auto">
          We convert real session behavior into a structured scorecard so hiring teams can evaluate strengths and risks with confidence.
        </p>
        <p className="text-center font-mono text-gray-600 mb-12 max-w-4xl mx-auto">
          Candidates are evaluated across multiple dimensions through a proprietary scoring model calibrated to role and seniority.
        </p>

        <h3 className="text-2xl font-bold mb-4">What we score</h3>
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6 mb-8">
          {dimensions.map(([title, description]) => (
            <div key={title} className="border-2 border-black bg-white p-6">
              <h3 className="text-lg font-bold mb-2">{title}</h3>
              <p className="font-mono text-sm text-gray-700">{description}</p>
            </div>
          ))}
        </div>

        <div className="border-2 border-black bg-gray-50 p-6">
          <h3 className="text-2xl font-bold mb-2">Example candidate persona comparison</h3>
          <p className="font-mono text-sm text-gray-700 mb-2">Same task. Very different signal.</p>
          <p className="font-mono text-sm text-gray-700 mb-5">
            Two candidates can both &quot;complete the task.&quot; {BRAND.name} shows how they got there and whether they can repeat
            it reliably with modern coding agents.
          </p>
          <div className="grid lg:grid-cols-[2fr_1fr] gap-6">
            <div className="border-2 border-black bg-white p-3 h-[340px]">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart data={demoComparison} outerRadius="72%">
                  <PolarGrid />
                  <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 11 }} />
                  <PolarRadiusAxis domain={[0, 100]} tickCount={6} />
                  <Radar name="AI-Native Product Engineer" dataKey="candidateA" stroke="#9D00FF" fill="#9D00FF" fillOpacity={0.14} />
                  <Radar name="Code-Strong Backend Engineer" dataKey="candidateB" stroke="#111827" fill="#111827" fillOpacity={0.06} />
                </RadarChart>
              </ResponsiveContainer>
            </div>
            <div className="space-y-3">
              {[
                [
                  'AI-Native Product Engineer (Prompt-Strong + Outcome-Driven)',
                  'High Task completion, Prompt clarity, Context provision, and Response utilization. Keeps strong Independence & efficiency while communicating decisions clearly.',
                ],
                [
                  'Code-Strong Backend Engineer (Prompt-Weak + Slower Loops)',
                  'Strong Debugging & design and baseline Role fit (CV ↔ Job), but lower Prompt clarity, Context provision, and Independence & efficiency. More retries and slower iteration loops.',
                ],
              ].map(([title, description]) => (
                <div key={title} className="border-2 border-black bg-white p-4">
                  <h4 className="font-bold mb-1">{title}</h4>
                  <p className="font-mono text-sm text-gray-700">{description}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};

const PricingSection = ({ onNavigate }) => (
  <section id="pricing" className="border-b-2 border-black bg-gray-50">
    <div className="max-w-7xl mx-auto px-6 py-20">
      <h2 className="text-4xl font-bold text-center mb-4">Pricing That Scales with Hiring</h2>
      <p className="text-center font-mono text-gray-600 mb-12">Clear plans, predictable costs, no hidden add-ons.</p>
      <div className="grid md:grid-cols-2 gap-8 max-w-4xl mx-auto">
        <div className="relative border-2 bg-white p-8" style={{ borderColor: '#9D00FF' }}>
          <div
            className="absolute -top-4 left-8 px-4 py-1 text-white border-2 border-black font-bold text-sm"
            style={{ backgroundColor: '#9D00FF' }}
          >
            RECOMMENDED
          </div>
          <h3 className="text-2xl font-bold mt-4 mb-2">Pay-Per-Use</h3>
          <div className="text-5xl font-bold mb-1">{formatAed(ASSESSMENT_PRICE_AED)}</div>
          <div className="font-mono text-sm text-gray-500 mb-6">per assessment</div>
          <ul className="space-y-3 mb-8">
            {['Full coding environment', 'Coding agents (Claude Code, Codex)', 'Automated scoring', 'Candidate reports', 'Workable sync', 'Email support'].map((feature) => (
              <li key={feature} className="flex items-center gap-2 font-mono text-sm">
                <Check size={16} style={{ color: '#9D00FF' }} /> {feature}
              </li>
            ))}
          </ul>
          <button
            className="w-full border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors"
            style={{ backgroundColor: '#9D00FF' }}
            onClick={() => onNavigate('login')}
          >
            Start Free Trial
          </button>
        </div>
        <div className="border-2 border-black bg-white p-8">
          <h3 className="text-2xl font-bold mt-4 mb-2">Monthly</h3>
          <div className="text-5xl font-bold mb-1">{formatAed(300)}</div>
          <div className="font-mono text-sm text-gray-500 mb-6">per month</div>
          <ul className="space-y-3 mb-8">
            {['Everything in Pay-Per-Use', 'Unlimited assessments', 'Custom tasks', 'Priority support', 'Analytics dashboard', 'Team management'].map((feature) => (
              <li key={feature} className="flex items-center gap-2 font-mono text-sm">
                <Check size={16} style={{ color: '#9D00FF' }} /> {feature}
              </li>
            ))}
          </ul>
          <button
            className="w-full border-2 border-black bg-white py-3 font-bold hover:bg-black hover:text-white transition-colors"
            onClick={() => onNavigate('login')}
          >
            Book Demo
          </button>
        </div>
      </div>
    </div>
  </section>
);

const Footer = () => (
  <footer className="bg-black text-white border-t-2 border-black">
    <div className="max-w-7xl mx-auto px-6 py-16">
      <div className="grid md:grid-cols-4 gap-8">
        <div>
          <div className="flex items-center gap-2 mb-4">
            <BrandGlyph borderClass="border-white" />
            <span className="text-xl font-bold tracking-tight">{BRAND.name}</span>
          </div>
          <p className="font-mono text-sm text-gray-400">{BRAND.productTagline}</p>
        </div>
        <div>
          <h4 className="font-bold mb-4">Product</h4>
          <ul className="space-y-2 font-mono text-sm text-gray-400">
            <li className="hover:text-white cursor-pointer">Features</li>
            <li className="hover:text-white cursor-pointer">Pricing</li>
            <li className="hover:text-white cursor-pointer">Integrations</li>
            <li className="hover:text-white cursor-pointer">Changelog</li>
          </ul>
        </div>
        <div>
          <h4 className="font-bold mb-4">Resources</h4>
          <ul className="space-y-2 font-mono text-sm text-gray-400">
            <li className="hover:text-white cursor-pointer">Documentation</li>
            <li className="hover:text-white cursor-pointer">API Reference</li>
            <li className="hover:text-white cursor-pointer">Blog</li>
            <li className="hover:text-white cursor-pointer">Support</li>
          </ul>
        </div>
        <div>
          <h4 className="font-bold mb-4">Company</h4>
          <ul className="space-y-2 font-mono text-sm text-gray-400">
            <li className="hover:text-white cursor-pointer">About</li>
            <li className="hover:text-white cursor-pointer">Careers</li>
            <li className="hover:text-white cursor-pointer">Privacy</li>
            <li className="hover:text-white cursor-pointer">Terms</li>
          </ul>
        </div>
      </div>
      <div className="border-t border-gray-800 mt-12 pt-8 flex flex-wrap items-center justify-between">
        <div className="font-mono text-xs text-gray-500">&copy; 2026 {BRAND.name}. All rights reserved.</div>
        <div className="font-mono text-xs text-gray-500">Built with React + Vite + Tailwind CSS</div>
      </div>
    </div>
  </footer>
);

export const LandingPage = ({ onNavigate }) => (
  <div className="min-h-screen bg-white text-black">
    <LandingNav onNavigate={onNavigate} />
    <HeroSection onNavigate={onNavigate} />
    <WhyTaaliSection />
    <HowTaaliSection />
    <PricingSection onNavigate={onNavigate} />
    <Footer />
  </div>
);

export default LandingPage;
