import React, { useState, useEffect } from 'react';
import {
  Code,
  Clock,
  BarChart3,
  DollarSign,
  CheckCircle,
  XCircle,
  Star,
  Clipboard,
  ChevronRight,
  ArrowLeft,
  Check,
  AlertTriangle,
  Zap,
  Users,
  Shield,
  Brain,
  Terminal,
  MessageSquare,
  Play,
  Settings,
  LogOut,
  Mail,
  Building,
  CreditCard,
  FileText,
  Activity,
  Eye,
  Bot,
  Timer,
  Menu,
  X,
  Loader2,
  Pencil,
  Trash2,
} from 'lucide-react';
import { RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip } from 'recharts';
import { useAuth } from './context/AuthContext';
import { auth, assessments as assessmentsApi, organizations as orgsApi, tasks as tasksApi, analytics as analyticsApi, candidates as candidatesApi } from './lib/api';
import AssessmentPage from './components/assessment/AssessmentPage';
import { CandidateDetailPage } from './pages/CandidateDetailPage';
import { DashboardPage } from './pages/DashboardPage';
import { CandidatesPage } from './pages/CandidatesPage';
import { BRAND } from './config/brand';
import { TasksPage } from './pages/TasksPage';
import { SettingsPage } from './pages/SettingsPage';
import { ASSESSMENT_PRICE_AED, formatAed } from './lib/currency';

// ============================================================
// DATA
// ============================================================

const weeklyData = [
  { week: 'Week 1', rate: 72, count: 8 },
  { week: 'Week 2', rate: 78, count: 10 },
  { week: 'Week 3', rate: 82, count: 12 },
  { week: 'Week 4', rate: 85, count: 9 },
  { week: 'Week 5', rate: 87.5, count: 13 },
];

// ============================================================
// SHARED COMPONENTS
// ============================================================

const Logo = ({ onClick }) => (
  <div className="flex items-center gap-2 cursor-pointer" onClick={onClick}>
    <div className="w-10 h-10 border-2 border-black flex items-center justify-center" style={{ backgroundColor: '#9D00FF' }}>
      <Code size={20} className="text-white" />
    </div>
    <span className="text-xl font-bold tracking-tight">{BRAND.name}</span>
  </div>
);

const StatsCard = ({ icon: Icon, label, value, change }) => (
  <div
    className="border-2 border-black bg-white p-6 hover:shadow-lg transition-shadow cursor-pointer"
    onClick={() => {}}
  >
    <Icon size={32} className="mb-4" />
    <div className="font-mono text-sm text-gray-600 mb-2">{label}</div>
    <div className="text-3xl font-bold mb-1">{value}</div>
    <div className="font-mono text-xs text-gray-500">{change}</div>
  </div>
);

const StatusBadge = ({ status }) => {
  if (status === 'completed') {
    return (
      <span
        className="inline-flex items-center gap-1 px-3 py-1 text-xs font-mono font-bold border-2"
        style={{ borderColor: '#9D00FF', backgroundColor: '#f3e8ff', color: '#9D00FF' }}
      >
        <Check size={12} /> Completed
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-3 py-1 text-xs font-mono font-bold border-2 border-black bg-yellow-300 text-black">
      <Timer size={12} /> In Progress
    </span>
  );
};

// ============================================================
// LANDING PAGE
// ============================================================

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
        {/* Mobile hamburger */}
        <button
          className="md:hidden border-2 border-black p-2 hover:bg-black hover:text-white transition-colors"
          onClick={() => setMobileOpen(!mobileOpen)}
        >
          {mobileOpen ? <X size={20} /> : <Menu size={20} />}
        </button>
        {/* Desktop nav */}
        <div className="hidden md:flex items-center gap-6">
          <button className="font-mono text-sm hover:underline" onClick={() => scrollTo('problem')}>Features</button>
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
      {/* Mobile menu */}
      {mobileOpen && (
        <div className="md:hidden border-t-2 border-black bg-white px-6 py-4 space-y-3">
          <button className="block w-full text-left font-mono text-sm py-2 hover:underline" onClick={() => scrollTo('problem')}>Features</button>
          <button className="block w-full text-left font-mono text-sm py-2 hover:underline" onClick={() => scrollTo('pricing')}>Pricing</button>
          <button className="block w-full text-left font-mono text-sm py-2 hover:underline" onClick={() => alert('Documentation coming soon')}>Docs</button>
          <button
            className="block w-full border-2 border-black px-4 py-2 font-mono text-sm hover:bg-black hover:text-white transition-colors text-center"
            onClick={() => { setMobileOpen(false); onNavigate('login'); }}
          >
            Sign In
          </button>
          <button
            className="block w-full border-2 border-black px-4 py-2 font-mono text-sm text-white hover:bg-black transition-colors text-center"
            style={{ backgroundColor: '#9D00FF' }}
            onClick={() => { setMobileOpen(false); onNavigate('login'); }}
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
      {/* Left Column */}
      <div>
        <div
          className="inline-block px-4 py-2 text-xs font-mono font-bold text-white border-2 border-black mb-8"
          style={{ backgroundColor: '#9D00FF' }}
        >
          POWERED BY E2B &bull; CLAUDE &bull; WORKABLE
        </div>
        <h1 className="text-5xl lg:text-6xl font-bold leading-tight mb-6">
          Stop Screening for Yesterday&apos;s Skills
        </h1>
        <p className="text-xl lg:text-2xl font-mono text-gray-700 mb-8 leading-relaxed">
          Test how engineers actually work—with AI tools like Cursor and Claude—not outdated algorithm puzzles.
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
      {/* Right Column — Old Way vs TALI Way */}
      <div className="grid grid-cols-2 gap-4">
        <div className="border-2 border-black bg-gray-100 p-4">
          <div className="flex items-center gap-2 mb-3">
            <XCircle size={18} className="text-red-500" />
            <span className="font-mono text-xs font-bold text-gray-500">OLD WAY</span>
          </div>
          <pre className="font-mono text-xs text-gray-600 leading-relaxed">
{`function reverseList(head) {
  let prev = null;
  let curr = head;
  while (curr) {
    let next = curr.next;
    curr.next = prev;
    prev = curr;
    curr = next;
  }
  return prev;
}`}
          </pre>
        </div>
        <div className="border-2 border-black bg-black p-4">
          <div className="flex items-center gap-2 mb-3">
            <CheckCircle size={18} style={{ color: '#9D00FF' }} />
            <span className="font-mono text-xs font-bold" style={{ color: '#9D00FF' }}>{BRAND.name} WAY</span>
          </div>
          <pre className="font-mono text-xs leading-relaxed" style={{ color: '#9D00FF' }}>
{`> Fix the delimiter bug
  in the CSV parser

> How should I handle
  empty rows?

> Write a test for
  parse_row()`}
          </pre>
        </div>
        <div className="border-2 border-black bg-gray-100 p-4">
          <div className="font-mono text-xs text-gray-500 mb-2">TESTS FOR:</div>
          <div className="font-mono text-sm text-gray-700">Memorized algorithms</div>
          <div className="font-mono text-sm text-gray-700">Whiteboard tricks</div>
          <div className="font-mono text-sm text-red-500 mt-2 font-bold">NOT real work</div>
        </div>
        <div className="border-2 border-black bg-black p-4">
          <div className="font-mono text-xs mb-2" style={{ color: '#9D00FF' }}>TESTS FOR:</div>
          <div className="font-mono text-sm text-white">AI collaboration</div>
          <div className="font-mono text-sm text-white">Real debugging</div>
          <div className="font-mono text-sm font-bold mt-2" style={{ color: '#9D00FF' }}>ACTUAL work</div>
        </div>
      </div>
    </div>
  </section>
);

const ProblemSection = () => (
  <section id="problem" className="border-b-2 border-black bg-white">
    <div className="max-w-7xl mx-auto px-6 py-20">
      <h2 className="text-4xl font-bold text-center mb-4">The Problem</h2>
      <p className="text-center font-mono text-gray-600 mb-12 max-w-2xl mx-auto">
        Current technical screening is broken. Here&apos;s why.
      </p>
      <div className="grid md:grid-cols-3 gap-6">
        <div className="border-2 border-black bg-white p-8 hover:shadow-lg transition-shadow">
          <Clock size={32} className="mb-4" />
          <h3 className="text-xl font-bold mb-4">Wastes Time</h3>
          <ul className="space-y-3">
            <li className="font-mono text-sm text-gray-700">3-hour technical rounds for each candidate</li>
            <li className="font-mono text-sm text-gray-700">Engineering time pulled from shipping features</li>
          </ul>
        </div>
        <div className="border-2 border-black bg-white p-8 hover:shadow-lg transition-shadow">
          <BarChart3 size={32} className="mb-4" />
          <h3 className="text-xl font-bold mb-4">Tests Wrong Skills</h3>
          <ul className="space-y-3">
            <li className="font-mono text-sm text-gray-700">Algorithm puzzles ≠ Real engineering work</li>
            <li className="font-mono text-sm text-gray-700">Engineers use AI tools daily—your tests don&apos;t</li>
          </ul>
        </div>
        <div className="border-2 border-black bg-white p-8 hover:shadow-lg transition-shadow">
          <DollarSign size={32} className="mb-4" />
          <h3 className="text-xl font-bold mb-4">Expensive</h3>
          <ul className="space-y-3">
            <li className="font-mono text-sm text-gray-700">Senior engineers @ AED 450/hour doing manual screening</li>
            <li className="font-mono text-sm text-gray-700">HackerRank/Codility: AED 90–225 per assessment</li>
          </ul>
        </div>
      </div>
    </div>
  </section>
);

const WhatWeTestSection = () => (
  <section id="what-we-test" className="border-b-2 border-black bg-white">
    <div className="max-w-7xl mx-auto px-6 py-20">
      <h2 className="text-4xl font-bold text-center mb-4">What we test (30+ signals)</h2>
      <p className="text-center font-mono text-gray-600 mb-12 max-w-3xl mx-auto">
        {BRAND.name} evaluates how candidates think and deliver in realistic, AI-assisted workflows—not just whether they can recite syntax.
      </p>
      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
        {[
          ['Prompt clarity', 'Do they ask precise, context-rich questions that move work forward?'],
          ['Debugging behavior', 'Can they isolate root causes, run targeted checks, and verify fixes?'],
          ['Autonomy', 'Do they make progress independently instead of waiting for step-by-step rescue?'],
          ['Communication quality', 'Can they explain tradeoffs, assumptions, and next steps clearly?'],
          ['Code quality', 'Is the resulting code maintainable, testable, and production-minded?'],
          ['Fraud signals', 'Do timeline and interaction patterns indicate authentic candidate work?'],
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

const PricingSection = ({ onNavigate }) => (
  <section id="pricing" className="border-b-2 border-black bg-gray-50">
    <div className="max-w-7xl mx-auto px-6 py-20">
      <h2 className="text-4xl font-bold text-center mb-4">Simple Pricing</h2>
      <p className="text-center font-mono text-gray-600 mb-12">No surprises. No hidden fees.</p>
      <div className="grid md:grid-cols-2 gap-8 max-w-4xl mx-auto">
        {/* Recommended */}
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
            {['Full AI-augmented environment', 'Claude integration', 'Automated scoring', 'Candidate reports', 'Workable sync', 'Email support'].map((f) => (
              <li key={f} className="flex items-center gap-2 font-mono text-sm">
                <Check size={16} style={{ color: '#9D00FF' }} /> {f}
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
        {/* Monthly */}
        <div className="border-2 border-black bg-white p-8">
          <h3 className="text-2xl font-bold mt-4 mb-2">Monthly</h3>
          <div className="text-5xl font-bold mb-1">{formatAed(300)}</div>
          <div className="font-mono text-sm text-gray-500 mb-6">per month</div>
          <ul className="space-y-3 mb-8">
            {['Everything in Pay-Per-Use', 'Unlimited assessments', 'Custom tasks', 'Priority support', 'Analytics dashboard', 'Team management'].map((f) => (
              <li key={f} className="flex items-center gap-2 font-mono text-sm">
                <Check size={16} style={{ color: '#9D00FF' }} /> {f}
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
            <div className="w-10 h-10 border-2 border-white flex items-center justify-center" style={{ backgroundColor: '#9D00FF' }}>
              <Code size={20} className="text-white" />
            </div>
            <span className="text-xl font-bold tracking-tight">{BRAND.name}</span>
          </div>
          <p className="font-mono text-sm text-gray-400">
            {BRAND.productTagline}
          </p>
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

const LandingPage = ({ onNavigate }) => (
  <div className="min-h-screen bg-white">
    <LandingNav onNavigate={onNavigate} />
    <HeroSection onNavigate={onNavigate} />
    <ProblemSection />
    <WhatWeTestSection />
    <PricingSection onNavigate={onNavigate} />
    <Footer />
  </div>
);

// ============================================================
// LOGIN PAGE
// ============================================================

const LoginPage = ({ onNavigate }) => {
  const { login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [needsVerification, setNeedsVerification] = useState(false);
  const [resending, setResending] = useState(false);
  const [resent, setResent] = useState(false);

  const handleLogin = async () => {
    setError('');
    setNeedsVerification(false);
    setLoading(true);
    try {
      await login(email, password);
      onNavigate('dashboard');
    } catch (err) {
      const status = err.response?.status;
      const msg = err.response?.data?.detail || err.message || 'Login failed';
      if (status === 403 && typeof msg === 'string' && msg.toLowerCase().includes('verify')) {
        setNeedsVerification(true);
      }
      setError(typeof msg === 'string' ? msg : 'Invalid credentials');
    } finally {
      setLoading(false);
    }
  };

  const handleResendVerification = async () => {
    if (!email) return;
    setResending(true);
    try {
      await auth.resendVerification(email);
      setResent(true);
      setTimeout(() => setResent(false), 5000);
    } catch {
      // endpoint always returns 200
    } finally {
      setResending(false);
    }
  };

  return (
    <div className="min-h-screen bg-white flex flex-col">
      <nav className="border-b-2 border-black bg-white">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <Logo onClick={() => onNavigate('landing')} />
        </div>
      </nav>
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          {error && (
            <div className="border-2 border-red-500 bg-red-50 p-4 mb-6">
              <div className="flex items-center gap-2">
                <AlertTriangle size={18} className="text-red-500 flex-shrink-0" />
                <span className="font-mono text-sm text-red-700">{error}</span>
              </div>
              {needsVerification && (
                <button
                  className="mt-3 w-full border border-red-300 py-2 font-mono text-sm font-bold text-red-700 hover:bg-red-100 transition-colors flex items-center justify-center gap-2"
                  onClick={handleResendVerification}
                  disabled={resending}
                >
                  {resending ? <><Loader2 size={14} className="animate-spin" /> Sending...</> : resent ? <><CheckCircle size={14} /> Verification email sent!</> : <><Mail size={14} /> Resend verification email</>}
                </button>
              )}
            </div>
          )}
          <div className="border-2 border-black p-8">
            <h2 className="text-3xl font-bold mb-2">Sign In</h2>
            <p className="font-mono text-sm text-gray-600 mb-8">Access your {BRAND.name} dashboard</p>
            <div className="space-y-4">
              <div>
                <label className="block font-mono text-sm mb-1">Email</label>
                <input
                  type="email"
                  className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
                  placeholder="you@company.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div>
                <label className="block font-mono text-sm mb-1">Password</label>
                <input
                  type="password"
                  className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
                />
              </div>
              <button
                className="w-full border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors mt-4 flex items-center justify-center gap-2"
                style={{ backgroundColor: '#9D00FF' }}
                onClick={handleLogin}
                disabled={loading}
              >
                {loading ? <><Loader2 size={18} className="animate-spin" /> Signing in...</> : 'Sign In'}
              </button>
            </div>
            <div className="mt-6 text-center space-y-2">
              <button
                type="button"
                className="font-mono text-sm hover:underline"
                style={{ color: '#9D00FF' }}
                onClick={() => onNavigate('forgot-password')}
              >
                Forgot password?
              </button>
              <div>
                <span className="font-mono text-sm text-gray-500">No account? </span>
                <button
                  className="font-mono text-sm font-bold hover:underline"
                  style={{ color: '#9D00FF' }}
                  onClick={() => onNavigate('register')}
                >
                  Register
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

// ============================================================
// REGISTER PAGE
// ============================================================

const RegisterPage = ({ onNavigate }) => {
  const { register } = useAuth();
  const [form, setForm] = useState({ email: '', password: '', full_name: '', organization_name: '' });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [resending, setResending] = useState(false);
  const [resent, setResent] = useState(false);

  const updateField = (field) => (e) => setForm((prev) => ({ ...prev, [field]: e.target.value }));

  const handleRegister = async () => {
    setError('');
    if (!form.email || !form.password || !form.full_name) {
      setError('Email, password, and full name are required');
      return;
    }
    if (form.password.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }
    setLoading(true);
    try {
      await register(form);
      setSuccess(true);
    } catch (err) {
      const detail = err.response?.data?.detail;
      const status = err.response?.status;
      let msg = 'Registration failed';

      // Map API error codes to friendly messages
      const errorMessages = {
        REGISTER_USER_ALREADY_EXISTS: 'An account with this email already exists. Sign in instead or use a different email.',
        INVALID_PASSWORD: 'Password should be at least 8 characters',
      };
      if (typeof detail === 'string' && errorMessages[detail]) {
        msg = errorMessages[detail];
      } else if (typeof detail === 'string') {
        msg = detail;
      } else if (Array.isArray(detail) && detail.length > 0) {
        const parts = detail.map((e) => {
          const m = e.msg ?? e.message;
          if (typeof m === 'string') return m;
          if (e.type === 'string_too_short' && e.ctx?.min_length === 8 && e.loc?.includes?.('password')) {
            return 'Password must be at least 8 characters';
          }
          return m ? String(m) : JSON.stringify(e);
        });
        msg = parts.join('. ');
      } else if (status === 404 || status === 0) {
        msg = 'Cannot reach server. The app may be misconfigured — please try again later.';
      } else if (err.message && !err.message.includes('Network Error')) {
        msg = err.message;
      } else if (err.code === 'ERR_NETWORK' || err.message === 'Network Error') {
        msg = 'Cannot connect to server. Check your connection and try again.';
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleResend = async () => {
    setResending(true);
    try {
      await auth.resendVerification(form.email);
      setResent(true);
      setTimeout(() => setResent(false), 5000);
    } catch {
      // silent — endpoint always returns 200
    } finally {
      setResending(false);
    }
  };

  return (
    <div className="min-h-screen bg-white flex flex-col">
      <nav className="border-b-2 border-black bg-white">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <Logo onClick={() => onNavigate('landing')} />
        </div>
      </nav>
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          {success ? (
            <div className="border-2 border-black p-8 text-center">
              <Mail size={48} className="mx-auto mb-4" style={{ color: '#9D00FF' }} />
              <h2 className="text-2xl font-bold mb-2">Check your email</h2>
              <p className="font-mono text-sm text-gray-600 mb-2">
                We sent a verification link to
              </p>
              <p className="font-mono text-sm font-bold mb-6">{form.email}</p>
              <p className="font-mono text-xs text-gray-500 mb-6">
                Click the link in the email to activate your account. The link expires in 24 hours.
              </p>
              <button
                className="w-full border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors mb-3"
                style={{ backgroundColor: '#9D00FF' }}
                onClick={() => onNavigate('login')}
              >
                Go to Sign In
              </button>
              <button
                className="w-full border-2 border-black py-3 font-bold hover:bg-gray-50 transition-colors flex items-center justify-center gap-2"
                onClick={handleResend}
                disabled={resending}
              >
                {resending ? <><Loader2 size={16} className="animate-spin" /> Sending...</> : resent ? <><CheckCircle size={16} style={{ color: '#9D00FF' }} /> Sent!</> : 'Resend verification email'}
              </button>
            </div>
          ) : (
            <div className="border-2 border-black p-8">
              <h2 className="text-3xl font-bold mb-2">Create Account</h2>
              <p className="font-mono text-sm text-gray-600 mb-8">Start using {BRAND.name} for your team</p>
              {error && (
                <div className="border-2 border-red-500 bg-red-50 p-4 mb-6 flex items-center gap-2">
                  <AlertTriangle size={18} className="text-red-500 flex-shrink-0" />
                  <span className="font-mono text-sm text-red-700">{error}</span>
                </div>
              )}
              <div className="space-y-4">
                <div>
                  <label className="block font-mono text-sm mb-1">Full Name *</label>
                  <input
                    type="text"
                    className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
                    placeholder="Jane Smith"
                    value={form.full_name}
                    onChange={updateField('full_name')}
                  />
                </div>
                <div>
                  <label className="block font-mono text-sm mb-1">Email *</label>
                  <input
                    type="email"
                    className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
                    placeholder="you@company.com"
                    value={form.email}
                    onChange={updateField('email')}
                  />
                </div>
                <div>
                  <label className="block font-mono text-sm mb-1">Password *</label>
                  <input
                    type="password"
                    className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
                    placeholder="••••••••"
                    value={form.password}
                    onChange={updateField('password')}
                    onKeyDown={(e) => e.key === 'Enter' && handleRegister()}
                  />
                  <p className="font-mono text-xs text-gray-500 mt-1">Minimum 8 characters</p>
                </div>
                <div>
                  <label className="block font-mono text-sm mb-1">Organization Name</label>
                  <input
                    type="text"
                    className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
                    placeholder="Acme Corp"
                    value={form.organization_name}
                    onChange={updateField('organization_name')}
                  />
                </div>
                <button
                  className="w-full border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors mt-4 flex items-center justify-center gap-2"
                  style={{ backgroundColor: '#9D00FF' }}
                  onClick={handleRegister}
                  disabled={loading}
                >
                  {loading ? <><Loader2 size={18} className="animate-spin" /> Creating account...</> : 'Create Account'}
                </button>
              </div>
              <div className="mt-6 text-center">
                <span className="font-mono text-sm text-gray-500">Already have an account? </span>
                <button
                  className="font-mono text-sm font-bold hover:underline"
                  style={{ color: '#9D00FF' }}
                  onClick={() => onNavigate('login')}
                >
                  Sign In
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// ============================================================
// FORGOT PASSWORD PAGE
// ============================================================

const ForgotPasswordPage = ({ onNavigate }) => {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (!email.trim()) {
      setError('Enter your email address');
      return;
    }
    setLoading(true);
    try {
      const { auth } = await import('./lib/api');
      await auth.forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Request failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-white flex flex-col">
      <nav className="border-b-2 border-black bg-white">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <Logo onClick={() => onNavigate('landing')} />
        </div>
      </nav>
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          {sent ? (
            <div className="border-2 border-black p-8 text-center">
              <CheckCircle size={48} className="mx-auto mb-4" style={{ color: '#9D00FF' }} />
              <h2 className="text-2xl font-bold mb-2">Check your email</h2>
              <p className="font-mono text-sm text-gray-600 mb-6">
                If an account exists for that email, we sent a link to reset your password.
              </p>
              <button
                className="w-full border-2 border-black py-3 font-bold text-white transition-colors"
                style={{ backgroundColor: '#9D00FF' }}
                onClick={() => onNavigate('login')}
              >
                Back to Sign In
              </button>
            </div>
          ) : (
            <div className="border-2 border-black p-8">
              <h2 className="text-3xl font-bold mb-2">Forgot password?</h2>
              <p className="font-mono text-sm text-gray-600 mb-6">Enter your email and we&apos;ll send a reset link.</p>
              {error && (
                <div className="border-2 border-red-500 bg-red-50 p-3 mb-4 flex items-center gap-2">
                  <AlertTriangle size={18} className="text-red-500 flex-shrink-0" />
                  <span className="font-mono text-sm text-red-700">{error}</span>
                </div>
              )}
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="block font-mono text-sm mb-1">Email</label>
                  <input
                    type="email"
                    className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
                    placeholder="you@company.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </div>
                <button
                  type="submit"
                  className="w-full border-2 border-black py-3 font-bold text-white transition-colors flex items-center justify-center gap-2"
                  style={{ backgroundColor: '#9D00FF' }}
                  disabled={loading}
                >
                  {loading ? <><Loader2 size={18} className="animate-spin" /> Sending...</> : 'Send reset link'}
                </button>
              </form>
              <div className="mt-6 text-center">
                <button
                  type="button"
                  className="font-mono text-sm hover:underline"
                  style={{ color: '#9D00FF' }}
                  onClick={() => onNavigate('login')}
                >
                  Back to Sign In
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// ============================================================
// RESET PASSWORD PAGE
// ============================================================

const ResetPasswordPage = ({ onNavigate, token }) => {
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (password.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }
    if (password !== confirm) {
      setError('Passwords do not match');
      return;
    }
    if (!token) {
      setError('Invalid reset link. Request a new one.');
      return;
    }
    setLoading(true);
    try {
      const { auth } = await import('./lib/api');
      await auth.resetPassword(token, password);
      setSuccess(true);
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Reset failed');
    } finally {
      setLoading(false);
    }
  };

  if (!token) {
    return (
      <div className="min-h-screen bg-white flex flex-col">
        <nav className="border-b-2 border-black bg-white">
          <div className="max-w-7xl mx-auto px-6 py-4">
            <Logo onClick={() => onNavigate('landing')} />
          </div>
        </nav>
        <div className="flex-1 flex items-center justify-center p-6">
          <div className="border-2 border-black p-8 text-center max-w-md">
            <AlertTriangle size={48} className="mx-auto mb-4 text-amber-500" />
            <h2 className="text-2xl font-bold mb-2">Invalid link</h2>
            <p className="font-mono text-sm text-gray-600 mb-6">This reset link is missing or invalid. Request a new one from the login page.</p>
            <button className="w-full border-2 border-black py-3 font-bold text-white" style={{ backgroundColor: '#9D00FF' }} onClick={() => onNavigate('forgot-password')}>Request new link</button>
          </div>
        </div>
      </div>
    );
  }

  if (success) {
    return (
      <div className="min-h-screen bg-white flex flex-col">
        <nav className="border-b-2 border-black bg-white">
          <div className="max-w-7xl mx-auto px-6 py-4">
            <Logo onClick={() => onNavigate('landing')} />
          </div>
        </nav>
        <div className="flex-1 flex items-center justify-center p-6">
          <div className="border-2 border-black p-8 text-center max-w-md">
            <CheckCircle size={48} className="mx-auto mb-4" style={{ color: '#9D00FF' }} />
            <h2 className="text-2xl font-bold mb-2">Password reset</h2>
            <p className="font-mono text-sm text-gray-600 mb-6">You can now sign in with your new password.</p>
            <button className="w-full border-2 border-black py-3 font-bold text-white" style={{ backgroundColor: '#9D00FF' }} onClick={() => onNavigate('login')}>Sign In</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white flex flex-col">
      <nav className="border-b-2 border-black bg-white">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <Logo onClick={() => onNavigate('landing')} />
        </div>
      </nav>
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          <div className="border-2 border-black p-8">
            <h2 className="text-3xl font-bold mb-2">Set new password</h2>
            <p className="font-mono text-sm text-gray-600 mb-6">Enter your new password below.</p>
            {error && (
              <div className="border-2 border-red-500 bg-red-50 p-3 mb-4 flex items-center gap-2">
                <AlertTriangle size={18} className="text-red-500 flex-shrink-0" />
                <span className="font-mono text-sm text-red-700">{error}</span>
              </div>
            )}
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block font-mono text-sm mb-1">New password</label>
                <input
                  type="password"
                  className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              <div>
                <label className="block font-mono text-sm mb-1">Confirm password</label>
                <input
                  type="password"
                  className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
                  placeholder="••••••••"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                />
              </div>
              <button
                type="submit"
                className="w-full border-2 border-black py-3 font-bold text-white transition-colors flex items-center justify-center gap-2"
                style={{ backgroundColor: '#9D00FF' }}
                disabled={loading}
              >
                {loading ? <><Loader2 size={18} className="animate-spin" /> Resetting...</> : 'Reset password'}
              </button>
            </form>
            <div className="mt-6 text-center">
              <button type="button" className="font-mono text-sm hover:underline" style={{ color: '#9D00FF' }} onClick={() => onNavigate('login')}>Back to Sign In</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

// ============================================================
// VERIFY EMAIL PAGE
// ============================================================

const VerifyEmailPage = ({ onNavigate, token }) => {
  const [status, setStatus] = useState('loading'); // loading | success | error
  const [message, setMessage] = useState('');

  useEffect(() => {
    if (!token) {
      setStatus('error');
      setMessage('Invalid verification link.');
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await auth.verifyEmail(token);
        if (!cancelled) {
          setStatus('success');
          setMessage(res.data?.detail || 'Email verified successfully.');
        }
      } catch (err) {
        if (!cancelled) {
          setStatus('error');
          setMessage(err.response?.data?.detail || 'Verification failed. The link may have expired.');
        }
      }
    })();
    return () => { cancelled = true; };
  }, [token]);

  return (
    <div className="min-h-screen bg-white flex flex-col">
      <nav className="border-b-2 border-black bg-white">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <Logo onClick={() => onNavigate('landing')} />
        </div>
      </nav>
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="border-2 border-black p-8 text-center max-w-md w-full">
          {status === 'loading' && (
            <>
              <Loader2 size={48} className="mx-auto mb-4 animate-spin" style={{ color: '#9D00FF' }} />
              <h2 className="text-2xl font-bold mb-2">Verifying your email...</h2>
              <p className="font-mono text-sm text-gray-600">Please wait a moment.</p>
            </>
          )}
          {status === 'success' && (
            <>
              <CheckCircle size={48} className="mx-auto mb-4" style={{ color: '#9D00FF' }} />
              <h2 className="text-2xl font-bold mb-2">Email verified!</h2>
              <p className="font-mono text-sm text-gray-600 mb-6">{message}</p>
              <button
                className="w-full border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors"
                style={{ backgroundColor: '#9D00FF' }}
                onClick={() => onNavigate('login')}
              >
                Sign In
              </button>
            </>
          )}
          {status === 'error' && (
            <>
              <AlertTriangle size={48} className="mx-auto mb-4 text-amber-500" />
              <h2 className="text-2xl font-bold mb-2">Verification failed</h2>
              <p className="font-mono text-sm text-gray-600 mb-6">{message}</p>
              <button
                className="w-full border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors"
                style={{ backgroundColor: '#9D00FF' }}
                onClick={() => onNavigate('login')}
              >
                Go to Sign In
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

// ============================================================
// DASHBOARD NAV
// ============================================================

const DashboardNav = ({ currentPage, onNavigate }) => {
  const { user, logout } = useAuth();
  const orgName = user?.organization?.name || '--';
  const initials = orgName.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();

  const handleLogout = () => {
    logout();
    onNavigate('landing');
  };

  return (
    <nav className="border-b-2 border-black bg-white">
      <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-8">
          <Logo onClick={() => onNavigate('dashboard')} />
          <div className="hidden md:flex items-center gap-1">
            {[
              { id: 'dashboard', label: 'Dashboard' },
              { id: 'candidates', label: 'Candidates' },
              { id: 'tasks', label: 'Tasks' },
              { id: 'analytics', label: 'Analytics' },
              { id: 'settings', label: 'Settings' },
            ].map((item) => (
              <button
                key={item.id}
                className={`px-4 py-2 font-mono text-sm border-2 transition-colors ${
                  currentPage === item.id
                    ? 'text-white border-black'
                    : 'border-transparent hover:border-black'
                }`}
                style={currentPage === item.id ? { backgroundColor: '#9D00FF' } : {}}
                onClick={() => onNavigate(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-4">
          <span className="font-mono text-sm hidden sm:inline">{orgName}</span>
          <div
            className="w-9 h-9 border-2 border-black flex items-center justify-center text-white font-bold text-sm"
            style={{ backgroundColor: '#9D00FF' }}
          >
            {initials}
          </div>
          <button
            className="border-2 border-black p-2 hover:bg-black hover:text-white transition-colors"
            onClick={handleLogout}
            title="Sign out"
          >
            <LogOut size={16} />
          </button>
        </div>
      </div>
    </nav>
  );
};

// ============================================================
// DASHBOARD PAGE
// ============================================================

const NewAssessmentModal = ({ onClose, onCreated, candidate: prefillCandidate }) => {
  const [tasksList, setTasksList] = useState([]);
  const [form, setForm] = useState({
    candidate_email: prefillCandidate?.email || '',
    candidate_name: prefillCandidate?.full_name || '',
    task_id: '',
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [createdAssessment, setCreatedAssessment] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    tasksApi.list().then((res) => setTasksList(res.data || [])).catch(() => {});
  }, []);

  const getCandidateLink = (token) => {
    const base = window.location.origin;
    return `${base}/assess/${token}`;
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(getCandidateLink(createdAssessment.token));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const getEmailTemplate = (token, candidateName, candidateEmail, taskName, durationMinutes) => {
    const link = getCandidateLink(token);
    const name = candidateName || candidateEmail || 'there';
    const firstName = name.split(' ')[0];
    const duration = durationMinutes || 30;
    const task = taskName || 'Technical Assessment';
    return `Subject: Your Technical Assessment — ${task}

Hi ${firstName},

Thank you for your interest. As part of our process, we'd like you to complete a short technical assessment.

Assessment: ${task}
Time allowed: ${duration} minutes
Your unique link: ${link}

A few things to note:
- The assessment is self-contained — no setup required, just a browser
- The timer starts when you click Begin
- You can use the built-in AI assistant during the task
- Make sure you're in a quiet place with a stable internet connection before starting

Please complete the assessment at your earliest convenience.

If you have any questions, just reply to this email.

Good luck!`;
  };

  const [copiedEmail, setCopiedEmail] = useState(false);
  const handleCopyEmail = () => {
    if (!createdAssessment) return;
    const template = getEmailTemplate(
      createdAssessment.token,
      form.candidate_name || prefillCandidate?.full_name,
      form.candidate_email || prefillCandidate?.email,
      createdAssessment.task_name,
      createdAssessment.duration_minutes,
    );
    navigator.clipboard.writeText(template);
    setCopiedEmail(true);
    setTimeout(() => setCopiedEmail(false), 2000);
  };

  const handleCreate = async () => {
    setError('');
    if (!form.candidate_email || !form.task_id) {
      setError('Candidate email and task are required');
      return;
    }
    setLoading(true);
    try {
      const res = await assessmentsApi.create({
        candidate_email: form.candidate_email,
        candidate_name: form.candidate_name || undefined,
        task_id: form.task_id,
      });
      setCreatedAssessment(res.data);
      onCreated(res.data);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to create assessment');
    } finally {
      setLoading(false);
    }
  };

  // After creation: show email template + link
  if (createdAssessment) {
    const link = getCandidateLink(createdAssessment.token);
    const candidateName = form.candidate_name || prefillCandidate?.full_name;
    const candidateEmail = form.candidate_email || prefillCandidate?.email;
    const emailTemplate = getEmailTemplate(
      createdAssessment.token,
      candidateName,
      candidateEmail,
      createdAssessment.task_name,
      createdAssessment.duration_minutes,
    );
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
        <div className="bg-white border-2 border-black p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center gap-3 mb-4">
            <div className="inline-flex items-center justify-center w-10 h-10 border-2 border-black flex-shrink-0" style={{ backgroundColor: '#9D00FF' }}>
              <CheckCircle size={20} className="text-white" />
            </div>
            <div>
              <h2 className="text-xl font-bold">Assessment Created</h2>
              <p className="font-mono text-xs text-gray-500">Send the email below to {candidateName || candidateEmail}</p>
            </div>
          </div>

          {/* Email template */}
          <div className="border-2 border-black mb-4">
            <div className="flex items-center justify-between px-3 py-2 border-b-2 border-black bg-gray-50">
              <span className="font-mono text-xs font-bold text-gray-600">EMAIL TEMPLATE</span>
              <span className="font-mono text-xs text-gray-400">Copy and paste into your email client</span>
            </div>
            <pre className="p-3 font-mono text-xs text-gray-800 whitespace-pre-wrap leading-relaxed">{emailTemplate}</pre>
          </div>

          <button
            className="w-full border-2 border-black py-3 font-bold text-white flex items-center justify-center gap-2 mb-2"
            style={{ backgroundColor: copiedEmail ? '#22c55e' : '#9D00FF' }}
            onClick={handleCopyEmail}
          >
            {copiedEmail ? <><Check size={16} /> Email Copied!</> : <><Clipboard size={16} /> Copy Email</>}
          </button>

          {/* Link as secondary action */}
          <div className="border border-gray-200 p-2 mb-3">
            <div className="font-mono text-xs text-gray-400 mb-1">or copy just the link</div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs text-gray-700 truncate flex-1">{link}</span>
              <button
                className="border border-black px-2 py-1 font-mono text-xs flex-shrink-0 flex items-center gap-1"
                style={{ backgroundColor: copied ? '#22c55e' : 'white', color: copied ? 'white' : 'black' }}
                onClick={handleCopy}
              >
                {copied ? <Check size={12} /> : <Clipboard size={12} />}
                {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
          </div>

          <button className="w-full border-2 border-black py-2 font-mono text-sm font-bold hover:bg-black hover:text-white" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="bg-white border-2 border-black p-8 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-2xl font-bold">New Assessment</h2>
          <button className="border-2 border-black p-1 hover:bg-black hover:text-white transition-colors" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        {error && (
          <div className="border-2 border-red-500 bg-red-50 p-3 mb-4 font-mono text-sm text-red-700">{error}</div>
        )}
        <div className="space-y-4">
          {prefillCandidate ? (
            <div className="border-2 border-black p-3 bg-gray-50">
              <div className="font-mono text-xs text-gray-500 mb-1">CANDIDATE</div>
              <div className="font-bold">{prefillCandidate.full_name || prefillCandidate.email}</div>
              {prefillCandidate.full_name && <div className="font-mono text-sm text-gray-600">{prefillCandidate.email}</div>}
            </div>
          ) : (
            <>
              <div>
                <label className="block font-mono text-sm mb-1">Candidate Name</label>
                <input
                  type="text"
                  className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
                  placeholder="Jane Smith"
                  value={form.candidate_name}
                  onChange={(e) => setForm((p) => ({ ...p, candidate_name: e.target.value }))}
                />
              </div>
              <div>
                <label className="block font-mono text-sm mb-1">Candidate Email *</label>
                <input
                  type="email"
                  className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
                  placeholder="candidate@example.com"
                  value={form.candidate_email}
                  onChange={(e) => setForm((p) => ({ ...p, candidate_email: e.target.value }))}
                />
              </div>
            </>
          )}
          <div>
            <label className="block font-mono text-sm mb-1">Task *</label>
            <select
              className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white"
              value={form.task_id}
              onChange={(e) => setForm((p) => ({ ...p, task_id: e.target.value }))}
            >
              <option value="">Select a task...</option>
              {tasksList.map((t) => (
                <option key={t.id} value={t.id}>{t.name || t.title}</option>
              ))}
              {tasksList.length === 0 && <option disabled>No tasks available — create one in the Tasks page</option>}
            </select>
          </div>
          <button
            className="w-full border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors flex items-center justify-center gap-2"
            style={{ backgroundColor: '#9D00FF' }}
            onClick={handleCreate}
            disabled={loading}
          >
            {loading ? <><Loader2 size={18} className="animate-spin" /> Creating...</> : 'Create Assessment'}
          </button>
        </div>
      </div>
    </div>
  );
};

// ============================================================
// CANDIDATES PAGE
// ============================================================

// ============================================================
// CANDIDATE DETAIL PAGE
// ============================================================

export { CandidateDetailPage };

// ============================================================
// TASKS PAGE
// ============================================================

// ============================================================
// ANALYTICS PAGE
// ============================================================

const AnalyticsPage = ({ onNavigate }) => {
  const [data, setData] = useState({
    weekly_completion: [],
    total_assessments: 0,
    completed_count: 0,
    completion_rate: 0,
    top_score: null,
    avg_score: null,
    avg_time_minutes: null,
  });
  const [loading, setLoading] = useState(true);
  const maxRate = 100;

  useEffect(() => {
    let cancelled = false;
    analyticsApi.get()
      .then((res) => { if (!cancelled) setData(res.data); })
      .catch(() => { if (!cancelled) setData((d) => d); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const weekly = data.weekly_completion?.length ? data.weekly_completion : [
    { week: 'Week 1', rate: 0, count: 0 },
    { week: 'Week 2', rate: 0, count: 0 },
    { week: 'Week 3', rate: 0, count: 0 },
    { week: 'Week 4', rate: 0, count: 0 },
    { week: 'Week 5', rate: 0, count: 0 },
  ];

  return (
    <div>
      <DashboardNav currentPage="analytics" onNavigate={onNavigate} />
      <div className="max-w-7xl mx-auto px-6 py-8">
        <h1 className="text-3xl font-bold mb-2">Analytics</h1>
        <p className="font-mono text-sm text-gray-600 mb-8">Assessment performance over time</p>

        {loading ? (
          <div className="flex items-center justify-center py-16 gap-3">
            <Loader2 size={24} className="animate-spin" style={{ color: '#9D00FF' }} />
            <span className="font-mono text-sm text-gray-500">Loading analytics...</span>
          </div>
        ) : (
          <>
            {/* Completion Rate Chart */}
            <div className="border-2 border-black p-8 mb-8">
              <h2 className="font-bold text-xl mb-6">Completion Rate</h2>
              <div className="flex items-end gap-4 h-64">
                {weekly.map((w, i) => (
                  <div key={i} className="flex-1 flex flex-col items-center justify-end h-full">
                    <div className="font-mono text-xs mb-2 font-bold">{w.rate}%</div>
                    <div
                      className="w-full border-2 border-black transition-all"
                      style={{
                        height: `${(w.rate / maxRate) * 100}%`,
                        backgroundColor: i === weekly.length - 1 ? '#9D00FF' : '#e5e7eb',
                      }}
                    />
                    <div className="font-mono text-xs mt-2 text-gray-600">{w.week}</div>
                  </div>
                ))}
              </div>
              <div className="flex items-center gap-6 mt-6 font-mono text-xs">
                <div className="flex items-center gap-2">
                  <div className="w-4 h-4 border-2 border-black" style={{ backgroundColor: '#9D00FF' }} />
                  <span>Your rate: {data.completion_rate ?? 0}%</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-4 h-4 border-2 border-black bg-gray-200" />
                  <span>Industry avg: 65%</span>
                </div>
              </div>
            </div>

            {/* Summary Stats */}
            <div className="grid md:grid-cols-3 gap-6">
              <div className="border-2 border-black p-6">
                <div className="font-mono text-sm text-gray-600 mb-2">Total Assessments</div>
                <div className="text-4xl font-bold">{data.total_assessments ?? 0}</div>
                <div className="font-mono text-xs text-gray-500 mt-1">All time</div>
              </div>
              <div className="border-2 border-black p-6">
                <div className="font-mono text-sm text-gray-600 mb-2">Top Score</div>
                <div className="text-4xl font-bold" style={{ color: '#9D00FF' }}>
                  {data.top_score != null ? `${data.top_score}/10` : '—'}
                </div>
                <div className="font-mono text-xs text-gray-500 mt-1">Best candidate score</div>
              </div>
              <div className="border-2 border-black p-6">
                <div className="font-mono text-sm text-gray-600 mb-2">Avg Time to Complete</div>
                <div className="text-4xl font-bold">
                  {data.avg_time_minutes != null ? `${data.avg_time_minutes}m` : '—'}
                </div>
                <div className="font-mono text-xs text-gray-500 mt-1">Completed assessments</div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

// ============================================================
// WORKABLE CONNECT BUTTON & CALLBACK
// ============================================================

const ConnectWorkableButton = () => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const handleClick = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await orgsApi.getWorkableAuthorizeUrl();
      if (res.data?.url) window.location.href = res.data.url;
      else setError('Could not get authorization URL');
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to connect');
    } finally {
      setLoading(false);
    }
  };
  return (
    <div>
      <button
        type="button"
        onClick={handleClick}
        disabled={loading}
        className="flex items-center gap-2 px-4 py-2 font-mono text-sm font-bold border-2 border-black bg-black text-white hover:bg-gray-800 disabled:opacity-60"
      >
        {loading ? <Loader2 size={18} className="animate-spin" /> : null}
        {loading ? 'Redirecting…' : 'Connect Workable'}
      </button>
      {error && <p className="font-mono text-sm text-red-600 mt-2">{error}</p>}
    </div>
  );
};

const WorkableCallbackPage = ({ code, onNavigate }) => {
  const [status, setStatus] = useState('connecting'); // 'connecting' | 'success' | 'error'
  const [message, setMessage] = useState('');
  useEffect(() => {
    if (!code) {
      setStatus('error');
      setMessage('Missing authorization code');
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        await orgsApi.connectWorkable(code);
        if (!cancelled) {
          setStatus('success');
          onNavigate('settings', { replace: true });
        }
      } catch (err) {
        if (!cancelled) {
          setStatus('error');
          setMessage(err?.response?.data?.detail || err.message || 'Connection failed');
        }
      }
    })();
    return () => { cancelled = true; };
  }, [code, onNavigate]);
  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="border-2 border-black p-8 max-w-md text-center">
        {status === 'connecting' && (
          <>
            <Loader2 size={32} className="animate-spin mx-auto mb-4" style={{ color: '#9D00FF' }} />
            <p className="font-mono text-sm">Connecting Workable…</p>
          </>
        )}
        {status === 'success' && (
          <>
            <CheckCircle size={32} className="mx-auto mb-4 text-green-600" />
            <p className="font-mono text-sm">Workable connected. Taking you to Settings…</p>
          </>
        )}
        {status === 'error' && (
          <>
            <AlertTriangle size={32} className="mx-auto mb-4 text-red-600" />
            <p className="font-mono text-sm text-red-600 mb-4">{message}</p>
            <button
              type="button"
              onClick={() => onNavigate('settings')}
              className="px-4 py-2 font-mono text-sm font-bold border-2 border-black hover:bg-gray-100"
            >
              Back to Settings
            </button>
          </>
        )}
      </div>
    </div>
  );
};

// ============================================================
// SETTINGS PAGE
// ============================================================

// ============================================================
// CANDIDATE PORTAL — WELCOME PAGE
// ============================================================

const CandidateWelcomePage = ({ token, assessmentId, onNavigate, onStarted }) => {
  const [loadingStart, setLoadingStart] = useState(false);
  const [startError, setStartError] = useState('');

  const handleStart = async () => {
    if (!token) {
      setStartError('Assessment token is missing from the link.');
      return;
    }
    setLoadingStart(true);
    setStartError('');
    try {
      const res = await assessmentsApi.start(token);
      const data = res.data;
      // Pass start response to parent so AssessmentPage receives it (no double-start)
      if (onStarted) onStarted(data);
      // Navigate to assessment page
      onNavigate('assessment');
    } catch (err) {
      const msg = err.response?.data?.detail || 'Failed to start assessment';
      setStartError(msg);
    } finally {
      setLoadingStart(false);
    }
  };

  return (
    <div className="min-h-screen bg-white">
      <nav className="border-b-2 border-black bg-white">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center gap-4">
          <Logo onClick={() => {}} />
          <span className="font-mono text-sm text-gray-500">|</span>
          <span className="font-mono text-sm">Technical Assessment</span>
        </div>
      </nav>
      <div className="max-w-3xl mx-auto px-6 py-16">
        <div className="text-center mb-8">
          <div
            className="inline-block px-4 py-2 text-xs font-mono font-bold text-white border-2 border-black mb-4"
            style={{ backgroundColor: '#9D00FF' }}
          >
            TALI Assessment
          </div>
          <h1 className="text-4xl font-bold mb-2">Technical Assessment</h1>
          <p className="font-mono text-gray-600">You&apos;ve been invited to complete a coding challenge</p>
        </div>

        {/* Welcome Message */}
        <div className="border-2 border-black p-8 mb-8">
          <p className="text-lg mb-4">Welcome,</p>
          <p className="font-mono text-sm text-gray-700 mb-4 leading-relaxed">
            You&apos;ve been invited to complete a technical assessment. This is a real coding environment where you can write, run, and test code with AI assistance.
          </p>
          <p className="font-mono text-sm text-gray-700 mb-4">You&apos;ll have access to:</p>
          <ul className="space-y-2 mb-6">
            {['Full Python environment (sandboxed)', 'Claude AI assistant for help', 'All the tools you\'d use on the job'].map((item) => (
              <li key={item} className="flex items-center gap-2 font-mono text-sm">
                <Check size={16} style={{ color: '#9D00FF' }} /> {item}
              </li>
            ))}
          </ul>
          <p className="font-mono text-sm text-gray-700 italic">
            This isn&apos;t a trick. We want to see how you actually work.
          </p>
          <p className="font-mono text-sm text-gray-500 mt-4">Ready when you are.</p>
        </div>

        {/* Info Cards */}
        <div className="grid md:grid-cols-3 gap-4 mb-8">
          <div className="border-2 border-black p-6">
            <Terminal size={24} className="mb-3" />
            <h3 className="font-bold mb-2">What You&apos;ll Do</h3>
            <ul className="font-mono text-xs text-gray-600 space-y-1">
              <li>Complete a coding challenge</li>
              <li>Use AI tools as you normally would</li>
              <li>Write and run your solution</li>
            </ul>
          </div>
          <div className="border-2 border-black p-6">
            <Brain size={24} className="mb-3" />
            <h3 className="font-bold mb-2">What We&apos;re Testing</h3>
            <ul className="font-mono text-xs text-gray-600 space-y-1">
              <li>Problem-solving approach</li>
              <li>AI collaboration skills</li>
              <li>Code quality & testing</li>
            </ul>
          </div>
          <div className="border-2 border-black p-6">
            <Shield size={24} className="mb-3" />
            <h3 className="font-bold mb-2">What You&apos;ll Need</h3>
            <ul className="font-mono text-xs text-gray-600 space-y-1">
              <li>Desktop browser (Chrome/Firefox)</li>
              <li>Uninterrupted time</li>
              <li>Stable internet connection</li>
              <li>Read the task context before coding</li>
            </ul>
          </div>
        </div>

        {startError && (
          <div className="border-2 border-red-500 bg-red-50 p-4 mb-4 font-mono text-sm text-red-700">
            {startError}
          </div>
        )}

        <button
          className="w-full border-2 border-black py-4 font-bold text-lg text-white hover:bg-black transition-colors flex items-center justify-center gap-2"
          style={{ backgroundColor: '#9D00FF' }}
          onClick={handleStart}
          disabled={loadingStart}
        >
          {loadingStart ? (
            <><Loader2 size={20} className="animate-spin" /> Starting Assessment...</>
          ) : (
            <>Start Assessment <ChevronRight size={20} /></>
          )}
        </button>
      </div>
    </div>
  );
};

// ============================================================
// MAIN APP
// ============================================================

function App() {
  const { isAuthenticated, loading: authLoading } = useAuth();
  const [selectedCandidate, setSelectedCandidate] = useState(null);
  /** Start response from candidate welcome — passed to AssessmentPage so it does not call start() again */
  const [startedAssessmentData, setStartedAssessmentData] = useState(null);

  const parseRouteFromLocation = () => {
    if (typeof window === 'undefined') {
      return {
        currentPage: 'landing',
        assessmentToken: null,
        assessmentIdFromLink: null,
        resetPasswordToken: '',
        verifyEmailToken: '',
      };
    }

    const pathname = window.location.pathname || '/';
    const searchParams = new URLSearchParams(window.location.search || '');
    const staticRoutes = {
      '/': 'landing',
      '/login': 'login',
      '/register': 'register',
      '/forgot-password': 'forgot-password',
      '/dashboard': 'dashboard',
      '/candidates': 'candidates',
      '/candidate-detail': 'candidate-detail',
      '/tasks': 'tasks',
      '/analytics': 'analytics',
      '/settings': 'settings',
    };

    if (pathname === '/settings/workable/callback') {
      return {
        currentPage: 'workable-callback',
        assessmentToken: null,
        assessmentIdFromLink: null,
        resetPasswordToken: '',
        verifyEmailToken: '',
      };
    }

    const assessPath = pathname.match(/^\/assess\/(.+)$/);
    if (assessPath) {
      return {
        currentPage: 'candidate-welcome',
        assessmentToken: decodeURIComponent(assessPath[1]),
        assessmentIdFromLink: null,
        resetPasswordToken: '',
        verifyEmailToken: '',
      };
    }

    const assessWithIdPath = pathname.match(/^\/assessment\/(\d+)$/);
    if (assessWithIdPath && searchParams.get('token')) {
      return {
        currentPage: 'candidate-welcome',
        assessmentToken: searchParams.get('token'),
        assessmentIdFromLink: Number(assessWithIdPath[1]),
        resetPasswordToken: '',
        verifyEmailToken: '',
      };
    }

    if (pathname === '/assessment/live') {
      return {
        currentPage: 'assessment',
        assessmentToken: searchParams.get('token'),
        assessmentIdFromLink: null,
        resetPasswordToken: '',
        verifyEmailToken: '',
      };
    }

    if (pathname === '/reset-password') {
      return {
        currentPage: 'reset-password',
        assessmentToken: null,
        assessmentIdFromLink: null,
        resetPasswordToken: searchParams.get('token') || '',
        verifyEmailToken: '',
      };
    }

    if (pathname === '/verify-email') {
      return {
        currentPage: 'verify-email',
        assessmentToken: null,
        assessmentIdFromLink: null,
        resetPasswordToken: '',
        verifyEmailToken: searchParams.get('token') || '',
      };
    }

    const hash = window.location.hash || '';
    // Backward compatibility: accept legacy hash routes and normalize to path routes.
    // Handle these before static "/" route matching so "/#/reset-password" works.
    const hashAssess = hash.match(/^#\/assess\/(.+)$/);
    if (hashAssess) {
      const token = decodeURIComponent(hashAssess[1]);
      window.history.replaceState(null, '', `/assess/${encodeURIComponent(token)}`);
      return {
        currentPage: 'candidate-welcome',
        assessmentToken: token,
        assessmentIdFromLink: null,
        resetPasswordToken: '',
        verifyEmailToken: '',
      };
    }
    const hashAssessment = hash.match(/^#\/assessment\/(\d+)\?token=(.+)$/);
    if (hashAssessment) {
      const id = Number(hashAssessment[1]);
      const token = decodeURIComponent(hashAssessment[2]);
      window.history.replaceState(null, '', `/assessment/${id}?token=${encodeURIComponent(token)}`);
      return {
        currentPage: 'candidate-welcome',
        assessmentToken: token,
        assessmentIdFromLink: id,
        resetPasswordToken: '',
        verifyEmailToken: '',
      };
    }
    if (hash.startsWith('#/reset-password')) {
      const query = hash.split('?')[1] || '';
      const token = new URLSearchParams(query).get('token') || '';
      window.history.replaceState(null, '', `/reset-password${token ? `?token=${encodeURIComponent(token)}` : ''}`);
      return {
        currentPage: 'reset-password',
        assessmentToken: null,
        assessmentIdFromLink: null,
        resetPasswordToken: token,
        verifyEmailToken: '',
      };
    }
    if (hash.startsWith('#/verify-email')) {
      const query = hash.split('?')[1] || '';
      const token = new URLSearchParams(query).get('token') || '';
      window.history.replaceState(null, '', `/verify-email${token ? `?token=${encodeURIComponent(token)}` : ''}`);
      return {
        currentPage: 'verify-email',
        assessmentToken: null,
        assessmentIdFromLink: null,
        resetPasswordToken: '',
        verifyEmailToken: token,
      };
    }
    const hashStatic = {
      '#/dashboard': '/dashboard',
      '#/settings': '/settings',
      '#/login': '/login',
      '#/register': '/register',
      '#/forgot-password': '/forgot-password',
    };
    if (hashStatic[hash]) {
      const target = hashStatic[hash];
      window.history.replaceState(null, '', target);
      return {
        currentPage: staticRoutes[target],
        assessmentToken: null,
        assessmentIdFromLink: null,
        resetPasswordToken: '',
        verifyEmailToken: '',
      };
    }

    if (staticRoutes[pathname]) {
      return {
        currentPage: staticRoutes[pathname],
        assessmentToken: null,
        assessmentIdFromLink: null,
        resetPasswordToken: '',
        verifyEmailToken: '',
      };
    }

    return {
      currentPage: 'landing',
      assessmentToken: null,
      assessmentIdFromLink: null,
      resetPasswordToken: '',
      verifyEmailToken: '',
    };
  };

  const initialRoute = parseRouteFromLocation();
  const [currentPage, setCurrentPage] = useState(initialRoute.currentPage);
  const [assessmentToken, setAssessmentToken] = useState(initialRoute.assessmentToken);
  const [assessmentIdFromLink, setAssessmentIdFromLink] = useState(initialRoute.assessmentIdFromLink);
  const [resetPasswordToken, setResetPasswordToken] = useState(initialRoute.resetPasswordToken);
  const [verifyEmailToken, setVerifyEmailToken] = useState(initialRoute.verifyEmailToken);

  const isWorkableCallback = typeof window !== 'undefined' && window.location.pathname === '/settings/workable/callback';
  const workableCallbackCode = isWorkableCallback ? new URLSearchParams(window.location.search).get('code') : null;

  useEffect(() => {
    const syncRouteState = () => {
      const route = parseRouteFromLocation();
      setCurrentPage(route.currentPage);
      setAssessmentToken(route.assessmentToken);
      setAssessmentIdFromLink(route.assessmentIdFromLink);
      setResetPasswordToken(route.resetPasswordToken);
      setVerifyEmailToken(route.verifyEmailToken);
    };

    window.addEventListener('popstate', syncRouteState);
    window.addEventListener('hashchange', syncRouteState);
    return () => {
      window.removeEventListener('popstate', syncRouteState);
      window.removeEventListener('hashchange', syncRouteState);
    };
  }, []);

  // Auto-redirect: if already authenticated and on landing/login/forgot-password, go to dashboard
  useEffect(() => {
    if (isAuthenticated && ['landing', 'login', 'forgot-password'].includes(currentPage)) {
      setCurrentPage('dashboard');
      if (typeof window !== 'undefined' && window.location.pathname !== '/dashboard') {
        window.history.replaceState(null, '', '/dashboard');
      }
    }
  }, [isAuthenticated, currentPage]);

  // When user logs out, redirect to landing (except on reset-password and workable-callback which may be in progress)
  useEffect(() => {
    if (!authLoading && !isAuthenticated && ['dashboard', 'candidates', 'analytics', 'settings', 'tasks', 'candidate-detail'].includes(currentPage)) {
      setCurrentPage('landing');
      if (typeof window !== 'undefined' && window.location.pathname !== '/') {
        window.history.replaceState(null, '', '/');
      }
    }
  }, [isAuthenticated, authLoading, currentPage]);

  const pathForPage = (page, options = {}) => {
    switch (page) {
      case 'landing':
        return '/';
      case 'login':
        return '/login';
      case 'register':
        return '/register';
      case 'forgot-password':
        return '/forgot-password';
      case 'reset-password':
        return `/reset-password${options.resetPasswordToken ? `?token=${encodeURIComponent(options.resetPasswordToken)}` : ''}`;
      case 'verify-email':
        return `/verify-email${options.verifyEmailToken ? `?token=${encodeURIComponent(options.verifyEmailToken)}` : ''}`;
      case 'dashboard':
        return '/dashboard';
      case 'candidates':
        return '/candidates';
      case 'candidate-detail':
        return '/candidate-detail';
      case 'tasks':
        return '/tasks';
      case 'analytics':
        return '/analytics';
      case 'settings':
        return '/settings';
      case 'candidate-welcome':
        if (options.assessmentIdFromLink && options.assessmentToken) {
          return `/assessment/${options.assessmentIdFromLink}?token=${encodeURIComponent(options.assessmentToken)}`;
        }
        if (options.assessmentToken) {
          return `/assess/${encodeURIComponent(options.assessmentToken)}`;
        }
        return '/';
      case 'assessment':
        return `/assessment/live${options.assessmentToken ? `?token=${encodeURIComponent(options.assessmentToken)}` : ''}`;
      case 'workable-callback':
        return '/settings/workable/callback';
      default:
        return null;
    }
  };

  const navigateToPage = (page, options = {}) => {
    if (Object.prototype.hasOwnProperty.call(options, 'assessmentToken')) setAssessmentToken(options.assessmentToken);
    if (Object.prototype.hasOwnProperty.call(options, 'assessmentIdFromLink')) setAssessmentIdFromLink(options.assessmentIdFromLink);
    if (Object.prototype.hasOwnProperty.call(options, 'resetPasswordToken')) setResetPasswordToken(options.resetPasswordToken);
    if (Object.prototype.hasOwnProperty.call(options, 'verifyEmailToken')) setVerifyEmailToken(options.verifyEmailToken);
    setCurrentPage(page);

    const nextPath = pathForPage(page, {
      assessmentToken: Object.prototype.hasOwnProperty.call(options, 'assessmentToken') ? options.assessmentToken : assessmentToken,
      assessmentIdFromLink: Object.prototype.hasOwnProperty.call(options, 'assessmentIdFromLink') ? options.assessmentIdFromLink : assessmentIdFromLink,
      resetPasswordToken: Object.prototype.hasOwnProperty.call(options, 'resetPasswordToken') ? options.resetPasswordToken : resetPasswordToken,
      verifyEmailToken: Object.prototype.hasOwnProperty.call(options, 'verifyEmailToken') ? options.verifyEmailToken : verifyEmailToken,
    });
    if (typeof window !== 'undefined' && nextPath) {
      const currentPath = `${window.location.pathname}${window.location.search}`;
      if (currentPath !== nextPath) {
        const historyMethod = options.replace ? 'replaceState' : 'pushState';
        window.history[historyMethod](null, '', nextPath);
      }
    }
    window.scrollTo(0, 0);
  };

  const handleCandidateStarted = (startData) => {
    setStartedAssessmentData(startData);
  };

  const navigateToCandidate = (candidate) => {
    setSelectedCandidate(candidate);
    navigateToPage('candidate-detail');
  };

  // Show nothing while auth is validating token
  if (authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 size={32} className="animate-spin" style={{ color: '#9D00FF' }} />
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      {currentPage === 'landing' && <LandingPage onNavigate={navigateToPage} />}
      {currentPage === 'login' && <LoginPage onNavigate={navigateToPage} />}
      {currentPage === 'register' && <RegisterPage onNavigate={navigateToPage} />}
      {currentPage === 'forgot-password' && <ForgotPasswordPage onNavigate={navigateToPage} />}
      {currentPage === 'reset-password' && <ResetPasswordPage onNavigate={navigateToPage} token={resetPasswordToken} />}
      {currentPage === 'verify-email' && <VerifyEmailPage onNavigate={navigateToPage} token={verifyEmailToken} />}
      {currentPage === 'dashboard' && (
        <DashboardPage
          onNavigate={navigateToPage}
          onViewCandidate={navigateToCandidate}
          NavComponent={DashboardNav}
          StatsCardComponent={StatsCard}
          StatusBadgeComponent={StatusBadge}
        />
      )}
      {currentPage === 'candidates' && (
        <CandidatesPage
          onNavigate={navigateToPage}
          onViewCandidate={navigateToCandidate}
          NavComponent={DashboardNav}
          NewAssessmentModalComponent={NewAssessmentModal}
        />
      )}
      {currentPage === 'candidate-detail' && (
        <CandidateDetailPage
          candidate={selectedCandidate}
          onNavigate={navigateToPage}
          onDeleted={() => setSelectedCandidate(null)}
          onNoteAdded={(timeline) =>
            setSelectedCandidate((prev) => (prev ? { ...prev, timeline } : prev))
          }
          NavComponent={DashboardNav}
        />
      )}
      {currentPage === 'tasks' && <TasksPage onNavigate={navigateToPage} NavComponent={DashboardNav} />}
      {currentPage === 'analytics' && <AnalyticsPage onNavigate={navigateToPage} />}
      {currentPage === 'settings' && (
        <SettingsPage
          onNavigate={navigateToPage}
          NavComponent={DashboardNav}
          ConnectWorkableButton={ConnectWorkableButton}
        />
      )}
      {currentPage === 'workable-callback' && (
        <WorkableCallbackPage code={workableCallbackCode} onNavigate={navigateToPage} />
      )}
      {currentPage === 'candidate-welcome' && (
        <CandidateWelcomePage
          token={assessmentToken}
          assessmentId={assessmentIdFromLink}
          onNavigate={navigateToPage}
          onStarted={handleCandidateStarted}
        />
      )}
      {currentPage === 'assessment' && (
        <AssessmentPage
          token={assessmentToken}
          startData={startedAssessmentData}
        />
      )}
    </div>
  );
}

export default App;
