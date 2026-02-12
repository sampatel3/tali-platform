import React, { useState, useEffect, useCallback } from 'react';
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
import { auth, assessments as assessmentsApi, organizations as orgsApi, tasks as tasksApi, analytics as analyticsApi, billing as billingApi, team as teamApi, candidates as candidatesApi } from './lib/api';
import AssessmentPage from './components/assessment/AssessmentPage';

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
    <span className="text-xl font-bold tracking-tight">TALI</span>
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
            <span className="font-mono text-xs font-bold" style={{ color: '#9D00FF' }}>TALI WAY</span>
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
            <li className="font-mono text-sm text-gray-700">Senior engineers @ £100/hour doing manual screening</li>
            <li className="font-mono text-sm text-gray-700">HackerRank/Codility: £20–50 per assessment</li>
          </ul>
        </div>
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
          <div className="text-5xl font-bold mb-1">£25</div>
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
          <div className="text-5xl font-bold mb-1">£300</div>
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
            <span className="text-xl font-bold tracking-tight">TALI</span>
          </div>
          <p className="font-mono text-sm text-gray-400">
            AI-augmented technical assessments for modern engineering teams.
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
        <div className="font-mono text-xs text-gray-500">&copy; 2026 TALI. All rights reserved.</div>
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
            <p className="font-mono text-sm text-gray-600 mb-8">Access your TALI dashboard</p>
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
              <p className="font-mono text-sm text-gray-600 mb-8">Start using TALI for your team</p>
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
    return `${base}/#/assess/${token}`;
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(getCandidateLink(createdAssessment.token));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
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

  // After creation: show the link
  if (createdAssessment) {
    const link = getCandidateLink(createdAssessment.token);
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
        <div className="bg-white border-2 border-black p-8 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
          <div className="text-center mb-6">
            <div
              className="inline-flex items-center justify-center w-16 h-16 border-2 border-black mb-4"
              style={{ backgroundColor: '#9D00FF' }}
            >
              <CheckCircle size={32} className="text-white" />
            </div>
            <h2 className="text-2xl font-bold">Assessment Created</h2>
            <p className="font-mono text-sm text-gray-600 mt-2">
              Share this link with <strong>{form.candidate_name || form.candidate_email}</strong>
            </p>
          </div>
          <div className="border-2 border-black p-4 mb-4 bg-gray-50">
            <label className="block font-mono text-xs text-gray-500 mb-2">CANDIDATE ASSESSMENT LINK</label>
            <div className="font-mono text-xs break-all text-gray-800">{link}</div>
          </div>
          <button
            className="w-full border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors flex items-center justify-center gap-2 mb-3"
            style={{ backgroundColor: copied ? '#22c55e' : '#9D00FF' }}
            onClick={handleCopy}
          >
            {copied ? <><Check size={18} /> Copied!</> : <><Clipboard size={18} /> Copy Link</>}
          </button>
          <button
            className="w-full border-2 border-black py-3 font-bold hover:bg-gray-100 transition-colors"
            onClick={onClose}
          >
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

const PAGE_SIZE = 10;

const DashboardPage = ({ onNavigate, onViewCandidate }) => {
  const { user } = useAuth();
  const [assessmentsList, setAssessmentsList] = useState([]);
  const [totalAssessmentsCount, setTotalAssessmentsCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [showNewModal, setShowNewModal] = useState(false);
  const [loadingViewId, setLoadingViewId] = useState(null);
  const [statusFilter, setStatusFilter] = useState('');
  const [taskFilter, setTaskFilter] = useState('');
  const [tasksForFilter, setTasksForFilter] = useState([]);
  const [page, setPage] = useState(0);
  const [compareIds, setCompareIds] = useState([]);

  useEffect(() => {
    let cancelled = false;
    tasksApi.list().then((res) => { if (!cancelled) setTasksForFilter(res.data || []); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const params = { limit: PAGE_SIZE, offset: page * PAGE_SIZE };
    if (statusFilter) params.status = statusFilter;
    if (taskFilter) params.task_id = taskFilter;
    assessmentsApi.list(params)
      .then((res) => {
        if (cancelled) return;
        const data = res.data || {};
        setAssessmentsList(Array.isArray(data) ? data : (data.items || []));
        setTotalAssessmentsCount(typeof data.total === 'number' ? data.total : (data.items || []).length);
      })
      .catch((err) => {
        console.warn('Failed to fetch assessments:', err.message);
        if (!cancelled) setAssessmentsList([]);
        if (!cancelled) setTotalAssessmentsCount(0);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [page, statusFilter, taskFilter]);

  const getAssessmentLink = (token) =>
    `${typeof window !== 'undefined' ? window.location.origin : ''}${typeof window !== 'undefined' ? (window.location.pathname || '/') : ''}#/assess/${token || ''}`;

  // Map API assessments to table-friendly shape, falling back to mock data
  const displayCandidates = assessmentsList.length > 0
    ? assessmentsList.map((a) => ({
        id: a.id,
        name: (a.candidate_name || a.candidate?.full_name || a.candidate_email || '').trim() || 'Unknown',
        email: a.candidate_email || a.candidate?.email || '',
        task: a.task?.name || a.task_name || 'Assessment',
        status: a.status === 'submitted' || a.status === 'graded' ? 'completed' : (a.status || 'in-progress'),
        score: a.score ?? a.overall_score ?? null,
        time: a.duration_taken ? `${Math.round(a.duration_taken / 60)}m` : '—',
        position: a.candidate?.position || a.task?.name || '',
        completedDate: a.completed_at ? new Date(a.completed_at).toLocaleDateString() : null,
        breakdown: a.breakdown || null,
        prompts: a.prompt_count ?? 0,
        promptsList: a.prompts_list || [],
        timeline: a.timeline || [],
        results: a.results || [],
        token: a.token,
        assessmentLink: a.token ? getAssessmentLink(a.token) : '',
        _raw: a,
      }))
    : [];

  const userName = user?.full_name?.split(' ')[0] || 'there';

  // Compute live stats from current page (total count from API)
  const totalAssessments = totalAssessmentsCount;
  const completedCount = displayCandidates.filter((c) => c.status === 'completed' || c.status === 'submitted' || c.status === 'graded').length;
  const totalPages = Math.max(1, Math.ceil(totalAssessmentsCount / PAGE_SIZE));
  const startRow = page * PAGE_SIZE + 1;
  const endRow = Math.min((page + 1) * PAGE_SIZE, totalAssessmentsCount);
  const completionRate = totalAssessments > 0 ? ((completedCount / totalAssessments) * 100).toFixed(1) : '0';
  const scores = displayCandidates.filter((c) => c.score !== null).map((c) => c.score);
  const avgScore = scores.length > 0 ? (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1) : '—';
  const monthCost = `£${completedCount * 25}`;
  const notifications = displayCandidates
    .filter((c) => c.status === 'completed')
    .slice(0, 5)
    .map((c) => ({
      id: `n-${c.id}`,
      text: `${c.name} completed ${c.task} (${c.score ?? '—'}/10)`,
    }));
  const compareCandidates = displayCandidates.filter((c) => compareIds.includes(c.id)).slice(0, 2);

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(displayCandidates, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'assessments.json';
    a.click();
    URL.revokeObjectURL(url);
  };
  const exportCsv = () => {
    const rows = [['Candidate', 'Email', 'Task', 'Status', 'Score']].concat(
      displayCandidates.map((c) => [c.name, c.email, c.task, c.status, c.score ?? ''])
    );
    const csv = rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'assessments.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <DashboardNav currentPage="dashboard" onNavigate={onNavigate} />
      <div className="md:hidden p-8 text-center border-b-2 border-black">
        <p className="font-mono text-sm">Desktop browser required for dashboard</p>
      </div>
      <div className="hidden md:block max-w-7xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold">Dashboard</h1>
            <p className="font-mono text-sm text-gray-600 mt-1">Welcome back, {userName}</p>
          </div>
          <button
            className="border-2 border-black px-6 py-3 font-bold text-white hover:bg-black transition-colors flex items-center gap-2"
            style={{ backgroundColor: '#9D00FF' }}
            onClick={() => setShowNewModal(true)}
          >
            <Zap size={18} /> New Assessment
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-3 mb-6">
          <button className="border-2 border-black px-4 py-2 font-mono text-xs font-bold hover:bg-black hover:text-white" onClick={exportCsv}>Export CSV</button>
          <button className="border-2 border-black px-4 py-2 font-mono text-xs font-bold hover:bg-black hover:text-white" onClick={exportJson}>Export JSON</button>
        </div>
        {notifications.length > 0 && (
          <div className="border-2 border-black p-4 mb-6">
            <div className="font-mono text-xs text-gray-500 mb-2">Recent Notifications</div>
            <div className="space-y-1">
              {notifications.map((n) => (
                <div key={n.id} className="font-mono text-sm">• {n.text}</div>
              ))}
            </div>
          </div>
        )}
        {compareCandidates.length === 2 && (
          <div className="border-2 border-black p-4 mb-6">
            <div className="font-mono text-xs text-gray-500 mb-2">Comparison</div>
            <div className="grid md:grid-cols-2 gap-4">
              {compareCandidates.map((c) => (
                <div key={`cmp-${c.id}`} className="border-2 border-black p-3">
                  <div className="font-bold">{c.name}</div>
                  <div className="font-mono text-xs text-gray-500">{c.task}</div>
                  <div className="font-mono text-sm mt-2">Score: {c.score ?? '—'}/10</div>
                  <div className="font-mono text-sm">Status: {c.status}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Stats Cards */}
        <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
          <StatsCard icon={Clipboard} label="Active Assessments" value={String(totalAssessments)} change={`${completedCount} completed`} />
          <StatsCard icon={CheckCircle} label="Completion Rate" value={`${completionRate}%`} change="Industry avg: 65%" />
          <StatsCard icon={Star} label="Avg Score" value={avgScore !== '—' ? `${avgScore}/10` : '—'} change="Candidates this month" />
          <StatsCard icon={DollarSign} label="This Month Cost" value={monthCost} change={`${completedCount} assessments`} />
        </div>

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-4 mb-4">
          <span className="font-mono text-sm font-bold">Filters:</span>
          <select
            className="border-2 border-black px-3 py-2 font-mono text-sm bg-white"
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(0); }}
          >
            <option value="">All statuses</option>
            <option value="pending">Pending</option>
            <option value="in_progress">In progress</option>
            <option value="completed">Completed</option>
          </select>
          <select
            className="border-2 border-black px-3 py-2 font-mono text-sm bg-white"
            value={taskFilter}
            onChange={(e) => { setTaskFilter(e.target.value); setPage(0); }}
          >
            <option value="">All tasks</option>
            {tasksForFilter.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </div>

        {/* Assessments Table */}
        <div className="border-2 border-black">
          <div className="border-b-2 border-black px-6 py-4 bg-black text-white flex items-center justify-between">
            <h2 className="font-bold text-lg">Recent Assessments</h2>
            {totalAssessmentsCount > 0 && (
              <span className="font-mono text-sm text-gray-300">
                Showing {startRow}–{endRow} of {totalAssessmentsCount}
              </span>
            )}
          </div>
          {loading ? (
            <div className="flex items-center justify-center py-16 gap-3">
              <Loader2 size={24} className="animate-spin" style={{ color: '#9D00FF' }} />
              <span className="font-mono text-sm text-gray-500">Loading assessments...</span>
            </div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b-2 border-black bg-gray-50">
                  <th className="text-left px-2 py-3 font-mono text-xs font-bold uppercase">Compare</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Candidate</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Task</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Status</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Score</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Time</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Assessment link</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Actions</th>
                </tr>
              </thead>
              <tbody>
                {displayCandidates.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-6 py-12 text-center font-mono text-sm text-gray-500">
                      No assessments yet. Click &quot;New Assessment&quot; to create one.
                    </td>
                  </tr>
                ) : (
                  displayCandidates.map((c) => (
                    <tr key={c.id} className="border-b border-gray-200 hover:bg-gray-50 transition-colors">
                      <td className="px-2 py-4">
                        <input
                          type="checkbox"
                          className="w-4 h-4 accent-purple-600"
                          checked={compareIds.includes(c.id)}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setCompareIds((prev) => Array.from(new Set([...prev, c.id])).slice(-2));
                            } else {
                              setCompareIds((prev) => prev.filter((id) => id !== c.id));
                            }
                          }}
                        />
                      </td>
                      <td className="px-6 py-4">
                        <div className="font-bold">{c.name}</div>
                        <div className="font-mono text-xs text-gray-500">{c.email}</div>
                      </td>
                      <td className="px-6 py-4 font-mono text-sm">{c.task}</td>
                      <td className="px-6 py-4"><StatusBadge status={c.status} /></td>
                      <td className="px-6 py-4 font-bold">{c.score !== null ? `${c.score}/10` : '—'}</td>
                      <td className="px-6 py-4 font-mono text-sm">{c.time}</td>
                      <td className="px-6 py-4">
                        {c.token ? (
                          <button
                            type="button"
                            className="border-2 border-black bg-white px-3 py-1.5 font-mono text-xs font-bold hover:bg-black hover:text-white transition-colors flex items-center gap-1"
                            onClick={() => {
                              const link = c.assessmentLink || getAssessmentLink(c.token);
                              navigator.clipboard?.writeText(link).then(() => { /* copied */ }).catch(() => {});
                            }}
                            title={c.assessmentLink || getAssessmentLink(c.token)}
                          >
                            <Clipboard size={14} /> Copy link
                          </button>
                        ) : (
                          <span className="font-mono text-xs text-gray-400">—</span>
                        )}
                      </td>
                      <td className="px-6 py-4">
                        {c.status === 'completed' || c.status === 'submitted' || c.status === 'graded' ? (
                          <button
                            className="border-2 border-black bg-white px-4 py-2 font-mono text-sm font-bold hover:bg-black hover:text-white transition-colors flex items-center gap-1 disabled:opacity-70"
                            disabled={loadingViewId === c.id}
                            onClick={async () => {
                              setLoadingViewId(c.id);
                              try {
                                const res = await assessmentsApi.get(c.id);
                                const a = res.data;
                                const merged = {
                                  ...c,
                                  promptsList: a.prompts_list || [],
                                  timeline: a.timeline || [],
                                  results: a.results || [],
                                  breakdown: a.breakdown || null,
                                  prompts: (a.prompts_list || []).length,
                                };
                                onViewCandidate(merged);
                              } catch (err) {
                                console.warn('Failed to fetch assessment detail, using list data:', err);
                                onViewCandidate(c);
                              } finally {
                                setLoadingViewId(null);
                              }
                            }}
                          >
                            {loadingViewId === c.id ? <Loader2 size={14} className="animate-spin" /> : <Eye size={14} />} View
                          </button>
                        ) : (
                          <button
                            className="border-2 border-gray-300 bg-gray-100 px-4 py-2 font-mono text-sm font-bold text-gray-400 cursor-not-allowed flex items-center gap-1"
                            disabled
                          >
                            <Timer size={14} /> Pending
                          </button>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          )}
          {!loading && totalAssessmentsCount > PAGE_SIZE && (
            <div className="border-t-2 border-black px-6 py-3 flex items-center justify-between bg-gray-50">
              <button
                type="button"
                className="border-2 border-black px-4 py-2 font-mono text-sm font-bold disabled:opacity-50 disabled:cursor-not-allowed hover:bg-black hover:text-white transition-colors"
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
              >
                Previous
              </button>
              <span className="font-mono text-sm">Page {page + 1} of {totalPages}</span>
              <button
                type="button"
                className="border-2 border-black px-4 py-2 font-mono text-sm font-bold disabled:opacity-50 disabled:cursor-not-allowed hover:bg-black hover:text-white transition-colors"
                disabled={page >= totalPages - 1}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </button>
            </div>
          )}
        </div>
      </div>

      {showNewModal && (
        <NewAssessmentModal
          onClose={() => setShowNewModal(false)}
          onCreated={(newAssessment) => {
            setAssessmentsList((prev) => [newAssessment, ...prev]);
          }}
        />
      )}
    </div>
  );
};

// ============================================================
// CANDIDATES PAGE
// ============================================================

const CandidatesPage = ({ onNavigate, onViewCandidate }) => {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [expandedCandidateId, setExpandedCandidateId] = useState(null);
  const [sendAssessmentCandidate, setSendAssessmentCandidate] = useState(null);
  const [candidateAssessments, setCandidateAssessments] = useState([]);
  const [loadingAssessments, setLoadingAssessments] = useState(false);
  const [form, setForm] = useState({ email: '', full_name: '', position: '' });
  const [editingId, setEditingId] = useState(null);
  const [uploadingDoc, setUploadingDoc] = useState(null); // { candidateId, type: 'cv'|'job_spec' }
  const [showDocUpload, setShowDocUpload] = useState(null); // candidateId to show upload panel for

  const loadCandidates = useCallback(async () => {
    setLoading(true);
    try {
      const res = await candidatesApi.list({ q, limit: 100, offset: 0 });
      setItems(res.data?.items || []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [q]);

  useEffect(() => {
    loadCandidates();
  }, [loadCandidates]);

  const loadCandidateAssessments = async (candidateId) => {
    setLoadingAssessments(true);
    try {
      const res = await assessmentsApi.list({ candidate_id: candidateId, limit: 100, offset: 0 });
      setCandidateAssessments(res.data?.items || []);
    } catch {
      setCandidateAssessments([]);
    } finally {
      setLoadingAssessments(false);
    }
  };

  const handleCreateOrUpdate = async () => {
    if (!form.email.trim() && !editingId) {
      alert('Email is required');
      return;
    }
    try {
      if (editingId) {
        await candidatesApi.update(editingId, {
          full_name: form.full_name || null,
          position: form.position || null,
        });
        setEditingId(null);
      } else {
        const res = await candidatesApi.create({
          email: form.email.trim(),
          full_name: form.full_name || null,
          position: form.position || null,
        });
        // After creation, show document upload panel for the new candidate
        if (res.data?.id) {
          setShowDocUpload(res.data.id);
        }
      }
      setForm({ email: '', full_name: '', position: '' });
      await loadCandidates();
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to save candidate');
    }
  };

  const handleDocUpload = async (candidateId, docType, file) => {
    setUploadingDoc({ candidateId, type: docType });
    try {
      if (docType === 'cv') {
        await candidatesApi.uploadCv(candidateId, file);
      } else {
        await candidatesApi.uploadJobSpec(candidateId, file);
      }
      await loadCandidates();
    } catch (err) {
      alert(err?.response?.data?.detail || `Failed to upload ${docType === 'cv' ? 'CV' : 'job spec'}`);
    } finally {
      setUploadingDoc(null);
    }
  };

  const handleEdit = (candidate) => {
    setEditingId(candidate.id);
    setForm({
      email: candidate.email || '',
      full_name: candidate.full_name || '',
      position: candidate.position || '',
    });
  };

  const handleDelete = async (candidateId) => {
    if (!window.confirm('Delete this candidate?')) return;
    try {
      await candidatesApi.remove(candidateId);
      if (expandedCandidateId === candidateId) {
        setExpandedCandidateId(null);
        setCandidateAssessments([]);
      }
      await loadCandidates();
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to delete candidate');
    }
  };

  return (
    <div>
      <DashboardNav currentPage="candidates" onNavigate={onNavigate} />
      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-3xl font-bold">Candidates</h1>
            <p className="font-mono text-sm text-gray-600 mt-1">Search and manage candidate profiles</p>
          </div>
          <div className="font-mono text-sm text-gray-600">{items.length} total</div>
        </div>

        <div className="border-2 border-black p-4 mb-6">
          <div className="font-mono text-xs text-gray-500 mb-2">Search</div>
          <input
            type="text"
            className="w-full border-2 border-black px-3 py-2 font-mono text-sm"
            placeholder="Search by name or email"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>

        <div className="border-2 border-black p-4 mb-6">
          <div className="font-mono text-xs text-gray-500 mb-2">{editingId ? 'Edit Candidate' : 'Create Candidate'}</div>
          <div className="grid md:grid-cols-3 gap-2">
            <input
              type="email"
              className="border-2 border-black px-3 py-2 font-mono text-sm"
              placeholder="email@company.com"
              value={form.email}
              onChange={(e) => setForm((p) => ({ ...p, email: e.target.value }))}
              disabled={Boolean(editingId)}
            />
            <input
              type="text"
              className="border-2 border-black px-3 py-2 font-mono text-sm"
              placeholder="Full name"
              value={form.full_name}
              onChange={(e) => setForm((p) => ({ ...p, full_name: e.target.value }))}
            />
            <input
              type="text"
              className="border-2 border-black px-3 py-2 font-mono text-sm"
              placeholder="Position"
              value={form.position}
              onChange={(e) => setForm((p) => ({ ...p, position: e.target.value }))}
            />
          </div>
          <div className="flex gap-2 mt-3">
            <button
              type="button"
              className="border-2 border-black px-4 py-2 font-mono text-sm font-bold text-white"
              style={{ backgroundColor: '#9D00FF' }}
              onClick={handleCreateOrUpdate}
            >
              {editingId ? 'Update Candidate' : 'Create Candidate'}
            </button>
            {editingId && (
              <button
                type="button"
                className="border-2 border-black px-4 py-2 font-mono text-sm font-bold hover:bg-black hover:text-white"
                onClick={() => {
                  setEditingId(null);
                  setForm({ email: '', full_name: '', position: '' });
                }}
              >
                Cancel
              </button>
            )}
          </div>
        </div>

        {/* Document upload panel (shown after creating a new candidate) */}
        {showDocUpload && (() => {
          const candidate = items.find(c => c.id === showDocUpload);
          if (!candidate) return null;
          return (
            <div className="border-2 border-black p-4 mb-6" style={{ borderColor: '#9D00FF' }}>
              <div className="flex items-center justify-between mb-3">
                <div>
                  <div className="font-mono text-xs text-gray-500">Upload Documents for {candidate.full_name || candidate.email}</div>
                  <div className="font-mono text-xs text-gray-400 mt-1">Upload CV and job specification before sending the assessment</div>
                </div>
                <button type="button" className="font-mono text-xs text-gray-500 hover:text-black" onClick={() => setShowDocUpload(null)}>Close</button>
              </div>
              <div className="grid md:grid-cols-2 gap-4">
                <div className="border border-gray-300 p-3">
                  <div className="font-mono text-xs font-bold mb-2">CV Upload {candidate.cv_filename && <span className="text-green-600 font-normal ml-1">Uploaded</span>}</div>
                  {candidate.cv_filename ? (
                    <div className="font-mono text-xs text-gray-600">{candidate.cv_filename}</div>
                  ) : null}
                  <input
                    type="file"
                    accept=".pdf,.docx"
                    className="font-mono text-xs mt-2"
                    disabled={uploadingDoc?.candidateId === candidate.id && uploadingDoc?.type === 'cv'}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) handleDocUpload(candidate.id, 'cv', file);
                    }}
                  />
                  {uploadingDoc?.candidateId === candidate.id && uploadingDoc?.type === 'cv' && (
                    <div className="font-mono text-xs text-gray-500 mt-1">Uploading...</div>
                  )}
                </div>
                <div className="border border-gray-300 p-3">
                  <div className="font-mono text-xs font-bold mb-2">Job Spec Upload {candidate.job_spec_filename && <span className="text-green-600 font-normal ml-1">Uploaded</span>}</div>
                  {candidate.job_spec_filename ? (
                    <div className="font-mono text-xs text-gray-600">{candidate.job_spec_filename}</div>
                  ) : null}
                  <input
                    type="file"
                    accept=".pdf,.docx,.txt"
                    className="font-mono text-xs mt-2"
                    disabled={uploadingDoc?.candidateId === candidate.id && uploadingDoc?.type === 'job_spec'}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) handleDocUpload(candidate.id, 'job_spec', file);
                    }}
                  />
                  {uploadingDoc?.candidateId === candidate.id && uploadingDoc?.type === 'job_spec' && (
                    <div className="font-mono text-xs text-gray-500 mt-1">Uploading...</div>
                  )}
                </div>
              </div>
            </div>
          );
        })()}

        <div className="border-2 border-black overflow-x-auto">
          <table className="w-full min-w-[900px]">
            <thead>
              <tr className="border-b-2 border-black bg-gray-50">
                <th className="px-4 py-3 text-left font-mono text-xs font-bold uppercase">Name</th>
                <th className="px-4 py-3 text-left font-mono text-xs font-bold uppercase">Email</th>
                <th className="px-4 py-3 text-left font-mono text-xs font-bold uppercase">Position</th>
                <th className="px-4 py-3 text-left font-mono text-xs font-bold uppercase">Documents</th>
                <th className="px-4 py-3 text-left font-mono text-xs font-bold uppercase">Created</th>
                <th className="px-4 py-3 text-left font-mono text-xs font-bold uppercase">Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center font-mono text-sm text-gray-500">Loading candidates...</td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center font-mono text-sm text-gray-500">No candidates found.</td>
                </tr>
              ) : (
                items.map((c) => (
                  <React.Fragment key={c.id}>
                  <tr className="border-b border-gray-200 align-top">
                    <td className="px-4 py-3 font-bold">{c.full_name || '--'}</td>
                    <td className="px-4 py-3 font-mono text-sm">{c.email}</td>
                    <td className="px-4 py-3 font-mono text-sm">{c.position || '--'}</td>
                    <td className="px-4 py-3">
                      <div className="flex gap-1">
                        <span className={`px-1.5 py-0.5 font-mono text-xs border ${c.cv_filename ? 'bg-green-50 border-green-600 text-green-700' : 'bg-gray-50 border-gray-300 text-gray-400'}`}>
                          CV {c.cv_filename ? '✓' : '—'}
                        </span>
                        <span className={`px-1.5 py-0.5 font-mono text-xs border ${c.job_spec_filename ? 'bg-green-50 border-green-600 text-green-700' : 'bg-gray-50 border-gray-300 text-gray-400'}`}>
                          JD {c.job_spec_filename ? '✓' : '—'}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3 font-mono text-sm">{c.created_at ? new Date(c.created_at).toLocaleDateString() : '--'}</td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          className="border border-black px-2 py-1 font-mono text-xs hover:bg-black hover:text-white"
                          onClick={() => handleEdit(c)}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="border border-black px-2 py-1 font-mono text-xs hover:bg-black hover:text-white"
                          onClick={() => setShowDocUpload(showDocUpload === c.id ? null : c.id)}
                        >
                          Upload Docs
                        </button>
                        <button
                          type="button"
                          className="border-2 border-black px-2 py-1 font-mono text-xs font-bold text-white"
                          style={{ backgroundColor: '#9D00FF' }}
                          onClick={() => setSendAssessmentCandidate(c)}
                        >
                          Send Assessment
                        </button>
                        <button
                          type="button"
                          className="border border-black px-2 py-1 font-mono text-xs hover:bg-black hover:text-white"
                          onClick={async () => {
                            if (expandedCandidateId === c.id) {
                              setExpandedCandidateId(null);
                              setCandidateAssessments([]);
                              return;
                            }
                            setExpandedCandidateId(c.id);
                            await loadCandidateAssessments(c.id);
                          }}
                        >
                          {expandedCandidateId === c.id ? 'Hide' : 'Assessments'}
                        </button>
                        <button
                          type="button"
                          className="border border-red-600 text-red-700 px-2 py-1 font-mono text-xs hover:bg-red-600 hover:text-white"
                          onClick={() => handleDelete(c.id)}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                  {expandedCandidateId === c.id && (
                    <tr className="border-b border-gray-200">
                      <td colSpan={6} className="px-4 py-3">
                        <div className="border border-gray-300 p-2">
                          <div className="font-mono text-xs text-gray-500 mb-2">Assessments</div>
                          {loadingAssessments ? (
                            <div className="font-mono text-xs text-gray-500">Loading...</div>
                          ) : candidateAssessments.length === 0 ? (
                            <div className="font-mono text-xs text-gray-500">No assessments for this candidate.</div>
                          ) : (
                            <div className="space-y-2">
                              {candidateAssessments.map((a) => (
                                <div key={a.id} className="flex items-center justify-between border border-gray-200 p-2">
                                  <div>
                                    <div className="font-mono text-xs">{a.task_name || 'Assessment'}</div>
                                    <div className="font-mono text-xs text-gray-500">Status: {a.status} | Score: {a.score ?? '--'}</div>
                                  </div>
                                  <button
                                    type="button"
                                    className="border border-black px-2 py-1 font-mono text-xs hover:bg-black hover:text-white"
                                    onClick={() =>
                                      onViewCandidate({
                                        id: a.id,
                                        name: a.candidate_name || c.full_name || c.email,
                                        email: a.candidate_email || c.email,
                                        task: a.task_name || 'Assessment',
                                        status: a.status || 'pending',
                                        score: a.score ?? null,
                                        time: a.duration_taken ? `${Math.round(a.duration_taken / 60)}m` : '—',
                                        position: c.position || '',
                                        completedDate: a.completed_at ? new Date(a.completed_at).toLocaleDateString() : null,
                                        breakdown: a.breakdown || null,
                                        prompts: a.prompt_count ?? 0,
                                        promptsList: a.prompts_list || [],
                                        timeline: a.timeline || [],
                                        results: a.results || [],
                                        token: a.token,
                                        _raw: a,
                                      })
                                    }
                                  >
                                    Open Detail
                                  </button>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {sendAssessmentCandidate && (
        <NewAssessmentModal
          candidate={sendAssessmentCandidate}
          onClose={() => setSendAssessmentCandidate(null)}
          onCreated={() => {
            setSendAssessmentCandidate(null);
            loadCandidates();
          }}
        />
      )}
    </div>
  );
};

// ============================================================
// CANDIDATE DETAIL PAGE
// ============================================================

export const CandidateDetailPage = ({ candidate, onNavigate, onDeleted, onNoteAdded }) => {
  const [activeTab, setActiveTab] = useState('results');
  const [busyAction, setBusyAction] = useState('');
  const [noteText, setNoteText] = useState('');
  const [avgCalibrationScore, setAvgCalibrationScore] = useState(null);

  const [expandedCategory, setExpandedCategory] = useState(null);

  const getRecommendation = (score100) => {
    if (score100 >= 80) return { label: 'STRONG HIRE', color: '#16a34a' };
    if (score100 >= 65) return { label: 'HIRE', color: '#2563eb' };
    if (score100 >= 50) return { label: 'CONSIDER', color: '#d97706' };
    return { label: 'NOT RECOMMENDED', color: '#FF0033' };
  };

  const score100 = candidate._raw?.final_score || (candidate.score ? candidate.score * 10 : null);
  const rec = score100 != null ? getRecommendation(score100) : null;
  const assessmentId = candidate?._raw?.id;

  useEffect(() => {
    let cancelled = false;
    const loadCalibrationAverage = async () => {
      try {
        const res = await analyticsApi.get();
        if (!cancelled) {
          setAvgCalibrationScore(res.data?.avg_calibration_score ?? null);
        }
      } catch {
        if (!cancelled) setAvgCalibrationScore(null);
      }
    };
    loadCalibrationAverage();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!candidate) return null;

  const handleDownloadReport = async () => {
    if (!assessmentId) return;
    setBusyAction('report');
    try {
      const res = await assessmentsApi.downloadReport(assessmentId);
      const blob = new Blob([res.data], { type: 'application/pdf' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `assessment-${assessmentId}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to download report');
    } finally {
      setBusyAction('');
    }
  };

  const handlePostToWorkable = async () => {
    if (!assessmentId) return;
    setBusyAction('workable');
    try {
      await assessmentsApi.postToWorkable(assessmentId);
      alert('Posted to Workable');
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to post to Workable');
    } finally {
      setBusyAction('');
    }
  };

  const handleDeleteAssessment = async () => {
    if (!assessmentId) return;
    if (!window.confirm('Delete this assessment? This cannot be undone.')) return;
    setBusyAction('delete');
    try {
      await assessmentsApi.remove(assessmentId);
      if (onDeleted) onDeleted();
      onNavigate('dashboard');
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to delete assessment');
    } finally {
      setBusyAction('');
    }
  };

  const handleAddNote = async () => {
    if (!assessmentId || !noteText.trim()) return;
    setBusyAction('note');
    try {
      const res = await assessmentsApi.addNote(assessmentId, noteText.trim());
      if (onNoteAdded && Array.isArray(res?.data?.timeline)) {
        onNoteAdded(res.data.timeline);
      }
      setNoteText('');
      alert('Note added');
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to add note');
    } finally {
      setBusyAction('');
    }
  };

  return (
    <div>
      <DashboardNav currentPage="dashboard" onNavigate={onNavigate} />
      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Back button */}
        <button
          className="flex items-center gap-2 font-mono text-sm mb-6 hover:underline"
          onClick={() => onNavigate('dashboard')}
        >
          <ArrowLeft size={16} /> Back to Dashboard
        </button>

        {/* Header */}
        <div className="grid md:grid-cols-3 gap-8 mb-8">
          <div className="md:col-span-2">
            <h1 className="text-4xl font-bold mb-2">{candidate.name}</h1>
            <p className="font-mono text-gray-500 mb-4">{candidate.email}</p>
            <div className="flex flex-wrap gap-4 font-mono text-sm text-gray-600">
              <span className="border-2 border-black px-3 py-1">{candidate.position}</span>
              <span className="border-2 border-black px-3 py-1">Task: {candidate.task}</span>
              <span className="border-2 border-black px-3 py-1">Duration: {candidate.time}</span>
              {candidate.completedDate && (
                <span className="border-2 border-black px-3 py-1">Completed: {candidate.completedDate}</span>
              )}
            </div>
          </div>
          {/* Score card */}
          {(score100 != null || candidate.score) && (
            <div className="border-2 bg-black p-6 text-white" style={{ borderColor: '#9D00FF' }}>
              <div className="text-5xl font-bold mb-1" style={{ color: '#9D00FF' }}>
                {score100 != null ? `${Math.round(score100)}` : candidate.score}<span className="text-lg text-gray-400">/{score100 != null ? '100' : '10'}</span>
              </div>
              {rec && (
                <div
                  className="inline-block px-3 py-1 text-xs font-bold font-mono text-white mb-3"
                  style={{ backgroundColor: rec.color }}
                >
                  {rec.label}
                </div>
              )}
              {candidate.breakdown?.categoryScores && (
                <div className="space-y-1.5 font-mono text-xs">
                  {[
                    ['Task Completion', 'task_completion'],
                    ['Prompt Clarity', 'prompt_clarity'],
                    ['Context', 'context_provision'],
                    ['Independence', 'independence'],
                    ['Utilization', 'utilization'],
                    ['Communication', 'communication'],
                    ['Approach', 'approach'],
                    ['CV Match', 'cv_match'],
                  ].map(([label, key]) => {
                    const val = candidate.breakdown.categoryScores[key];
                    return val != null ? (
                      <div key={key} className="flex items-center gap-2">
                        <span className="text-gray-400 w-28 truncate">{label}</span>
                        <div className="flex-1 bg-gray-700 h-1.5 rounded">
                          <div className="h-full rounded" style={{ width: `${(val / 10) * 100}%`, backgroundColor: val >= 7 ? '#16a34a' : val >= 5 ? '#d97706' : '#dc2626' }} />
                        </div>
                        <span className="w-8 text-right">{val}</span>
                      </div>
                    ) : null;
                  })}
                </div>
              )}
              {!candidate.breakdown?.categoryScores && candidate.breakdown && (
                <div className="space-y-1.5 font-mono text-xs">
                  <div className="flex justify-between"><span className="text-gray-400">Tests Passed</span><span>{candidate.breakdown.testsPassed}</span></div>
                  <div className="flex justify-between"><span className="text-gray-400">Code Quality</span><span>{candidate.breakdown.codeQuality}/10</span></div>
                  <div className="flex justify-between"><span className="text-gray-400">Time Efficiency</span><span>{candidate.breakdown.timeEfficiency}/10</span></div>
                  <div className="flex justify-between"><span className="text-gray-400">AI Usage</span><span>{candidate.breakdown.aiUsage}/10</span></div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Tabs */}
        <div className="flex flex-wrap gap-3 mb-6">
          <button
            type="button"
            className="border-2 border-black px-4 py-2 font-mono text-sm font-bold hover:bg-black hover:text-white"
            onClick={handleDownloadReport}
            disabled={busyAction !== ''}
          >
            {busyAction === 'report' ? 'Downloading…' : 'Download PDF'}
          </button>
          <button
            type="button"
            className="border-2 border-black px-4 py-2 font-mono text-sm font-bold hover:bg-black hover:text-white"
            onClick={handlePostToWorkable}
            disabled={busyAction !== ''}
          >
            {busyAction === 'workable' ? 'Posting…' : 'Post to Workable'}
          </button>
          <button
            type="button"
            className="border-2 border-red-600 text-red-700 px-4 py-2 font-mono text-sm font-bold hover:bg-red-600 hover:text-white"
            onClick={handleDeleteAssessment}
            disabled={busyAction !== ''}
          >
            {busyAction === 'delete' ? 'Deleting…' : 'Delete'}
          </button>
        </div>
        <div className="border-2 border-black p-4 mb-6">
          <div className="font-mono text-xs text-gray-500 mb-2">Recruiter Notes</div>
          <div className="flex gap-2">
            <input
              type="text"
              className="flex-1 border-2 border-black px-3 py-2 font-mono text-sm"
              placeholder="Add note about this candidate"
              value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
            />
            <button
              type="button"
              className="border-2 border-black px-4 py-2 font-mono text-sm font-bold hover:bg-black hover:text-white"
              onClick={handleAddNote}
              disabled={busyAction !== ''}
            >
              {busyAction === 'note' ? 'Saving…' : 'Save Note'}
            </button>
          </div>
        </div>
        <div className="flex border-2 border-black mb-6">
          {['results', 'ai-usage', 'cv-fit', 'timeline'].map((tab) => (
            <button
              key={tab}
              className={`flex-1 px-6 py-3 font-mono text-sm font-bold border-r-2 border-black last:border-r-0 transition-colors ${
                activeTab === tab ? 'text-white' : 'bg-white hover:bg-gray-100'
              }`}
              style={activeTab === tab ? { backgroundColor: '#9D00FF' } : {}}
              onClick={() => setActiveTab(tab)}
            >
              {tab === 'results' && 'Results'}
              {tab === 'ai-usage' && 'AI Usage'}
              {tab === 'cv-fit' && 'CV & Fit'}
              {tab === 'timeline' && 'Timeline'}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        {activeTab === 'results' && (() => {
          const assessment = candidate._raw || {};
          const bd = candidate.breakdown || {};
          const catScores = bd.categoryScores || bd.detailedScores?.category_scores || {};
          const detailedScores = bd.detailedScores || assessment.prompt_analytics?.detailed_scores || {};
          const explanations = bd.explanations || assessment.prompt_analytics?.explanations || {};

          const CATEGORY_CONFIG = [
            { key: 'task_completion', label: 'Task Completion', icon: '✅', weight: '20%' },
            { key: 'prompt_clarity', label: 'Prompt Clarity', icon: '🎯', weight: '15%' },
            { key: 'context_provision', label: 'Context Provision', icon: '📎', weight: '15%' },
            { key: 'independence', label: 'Independence & Efficiency', icon: '🧠', weight: '20%' },
            { key: 'utilization', label: 'Response Utilization', icon: '⚡', weight: '10%' },
            { key: 'communication', label: 'Communication Quality', icon: '✍️', weight: '10%' },
            { key: 'approach', label: 'Debugging & Design', icon: '🔧', weight: '5%' },
            { key: 'cv_match', label: 'CV-Job Fit', icon: '📄', weight: '5%' },
          ];

          const METRIC_LABELS = {
            tests_passed_ratio: 'Tests Passed', time_compliance: 'Time Compliance', time_efficiency: 'Time Efficiency',
            prompt_length_quality: 'Prompt Length', question_clarity: 'Clear Questions', prompt_specificity: 'Specificity', vagueness_score: 'Avoids Vagueness',
            code_context_rate: 'Includes Code', error_context_rate: 'Includes Errors', reference_rate: 'References', attempt_mention_rate: 'Prior Attempts',
            first_prompt_delay: 'Thinks Before Asking', prompt_spacing: 'Spacing Between', prompt_efficiency: 'Prompts/Test', token_efficiency: 'Token Efficiency', pre_prompt_effort: 'Self-Attempt Rate',
            post_prompt_changes: 'Uses Responses', wasted_prompts: 'Actionable Prompts', iteration_quality: 'Iterative Refinement',
            grammar_score: 'Grammar', readability_score: 'Readability', tone_score: 'Professional Tone',
            debugging_score: 'Debugging Strategy', design_score: 'Design Thinking',
            cv_job_match_score: 'Overall Match', skills_match: 'Skills Alignment', experience_relevance: 'Experience',
          };

          const radarData = CATEGORY_CONFIG.filter(c => catScores[c.key] != null).map(c => ({
            signal: c.label.split(' ')[0],
            score: catScores[c.key] || 0,
            fullMark: 10,
          }));

          return (
            <div className="space-y-6">
              {/* Category Radar Chart */}
              {radarData.length > 0 && (
                <div className="border-2 border-black p-4">
                  <div className="font-bold mb-4">Category Breakdown</div>
                  <div style={{ width: '100%', height: 350 }}>
                    <ResponsiveContainer>
                      <RadarChart data={radarData}>
                        <PolarGrid />
                        <PolarAngleAxis dataKey="signal" tick={{ fontSize: 11, fontFamily: 'monospace' }} />
                        <PolarRadiusAxis domain={[0, 10]} tick={{ fontSize: 10 }} />
                        <Radar name="Score" dataKey="score" stroke="#9D00FF" fill="#9D00FF" fillOpacity={0.3} />
                      </RadarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}

              {/* Expandable Category Sections */}
              <div className="space-y-3">
                {CATEGORY_CONFIG.map((cat) => {
                  const catScore = catScores[cat.key];
                  const metrics = detailedScores[cat.key] || {};
                  const catExplanations = explanations[cat.key] || {};
                  const isExpanded = expandedCategory === cat.key;

                  if (catScore == null && Object.keys(metrics).length === 0) return null;

                  return (
                    <div key={cat.key} className="border-2 border-black">
                      <button
                        type="button"
                        className="w-full flex items-center justify-between p-4 hover:bg-gray-50 text-left"
                        onClick={() => setExpandedCategory(isExpanded ? null : cat.key)}
                      >
                        <div className="flex items-center gap-3">
                          <span>{cat.icon}</span>
                          <span className="font-bold">{cat.label}</span>
                          <span className="font-mono text-xs text-gray-500">(Weight: {cat.weight})</span>
                        </div>
                        <div className="flex items-center gap-3">
                          {catScore != null && (
                            <span className="font-mono font-bold text-lg" style={{ color: catScore >= 7 ? '#16a34a' : catScore >= 5 ? '#d97706' : '#dc2626' }}>
                              {catScore}/10
                            </span>
                          )}
                          <span className="font-mono text-gray-400">{isExpanded ? '▲' : '▼'}</span>
                        </div>
                      </button>
                      {isExpanded && (
                        <div className="border-t-2 border-black p-4 space-y-3 bg-gray-50">
                          {Object.entries(metrics).map(([metricKey, metricVal]) => (
                            <div key={metricKey}>
                              <div className="flex items-center gap-3 mb-1">
                                <div className="font-mono text-sm w-44 text-gray-700">{METRIC_LABELS[metricKey] || metricKey.replace(/_/g, ' ')}</div>
                                <div className="flex-1 bg-gray-200 h-2.5 border border-gray-300 rounded">
                                  <div
                                    className="h-full rounded"
                                    style={{
                                      width: `${((metricVal || 0) / 10) * 100}%`,
                                      backgroundColor: (metricVal || 0) >= 7 ? '#16a34a' : (metricVal || 0) >= 5 ? '#d97706' : '#dc2626',
                                    }}
                                  />
                                </div>
                                <div className="font-mono text-sm w-14 text-right font-bold">
                                  {metricVal != null ? `${metricVal}/10` : '—'}
                                </div>
                              </div>
                              {catExplanations[metricKey] && (
                                <div className="font-mono text-xs text-gray-500 ml-0 pl-44 mt-0.5">{catExplanations[metricKey]}</div>
                              )}
                            </div>
                          ))}
                          {Object.keys(metrics).length === 0 && (
                            <div className="font-mono text-sm text-gray-500">No detailed metrics available for this category.</div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Assessment Metadata */}
              <div className="border-2 border-black p-4">
                <div className="font-bold mb-3">Assessment Metadata</div>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3 font-mono text-sm">
                  <div><span className="text-gray-500">Duration:</span> {assessment.total_duration_seconds ? `${Math.floor(assessment.total_duration_seconds / 60)}m ${assessment.total_duration_seconds % 60}s` : '—'}</div>
                  <div><span className="text-gray-500">Total Prompts:</span> {assessment.total_prompts ?? '—'}</div>
                  <div><span className="text-gray-500">Tokens Used:</span> {((assessment.total_input_tokens || 0) + (assessment.total_output_tokens || 0)).toLocaleString()}</div>
                  <div><span className="text-gray-500">Tests:</span> {assessment.tests_passed ?? 0}/{assessment.tests_total ?? 0}</div>
                  <div><span className="text-gray-500">Started:</span> {assessment.started_at ? new Date(assessment.started_at).toLocaleString() : '—'}</div>
                  <div><span className="text-gray-500">Submitted:</span> {assessment.completed_at ? new Date(assessment.completed_at).toLocaleString() : '—'}</div>
                </div>
              </div>

              {/* Fraud Flags */}
              {assessment.prompt_fraud_flags && assessment.prompt_fraud_flags.length > 0 && (
                <div className="border-2 border-red-500 bg-red-50 p-4">
                  <div className="font-bold text-red-700 mb-2 flex items-center gap-2"><AlertTriangle size={18} /> Fraud Flags Detected</div>
                  {assessment.prompt_fraud_flags.map((flag, i) => (
                    <div key={i} className="font-mono text-sm text-red-700 mb-1">
                      • {flag.type}: {flag.evidence} (confidence: {(flag.confidence * 100).toFixed(0)}%)
                    </div>
                  ))}
                </div>
              )}

              {/* Legacy results */}
              {candidate.results.length > 0 && (
                <div className="space-y-3">
                  <div className="font-bold">Test Results</div>
                  {candidate.results.map((r, i) => (
                    <div key={i} className="border-2 border-black bg-green-50 p-4 flex items-start gap-3">
                      <CheckCircle size={20} style={{ color: '#9D00FF' }} className="mt-0.5 flex-shrink-0" />
                      <div>
                        <div className="font-bold">{r.title} <span className="font-mono text-sm text-gray-500">({r.score})</span></div>
                        <p className="font-mono text-sm text-gray-600 mt-1">{r.description}</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })()}

        {activeTab === 'ai-usage' && (() => {
          const assessment = candidate._raw || {};

          return (
            <div className="space-y-6">
              {/* Summary Stats */}
              <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                <div className="border-2 border-black p-4">
                  <div className="font-mono text-xs text-gray-500">Avg Prompt Quality</div>
                  <div className="text-2xl font-bold">{assessment.prompt_quality_score?.toFixed(1) || '--'}<span className="text-sm text-gray-500">/10</span></div>
                </div>
                <div className="border-2 border-black p-4">
                  <div className="font-mono text-xs text-gray-500">Time to First Prompt</div>
                  <div className="text-2xl font-bold">{assessment.time_to_first_prompt_seconds ? `${Math.floor(assessment.time_to_first_prompt_seconds / 60)}m ${Math.round(assessment.time_to_first_prompt_seconds % 60)}s` : '--'}</div>
                </div>
                <div className="border-2 border-black p-4">
                  <div className="font-mono text-xs text-gray-500">Browser Focus</div>
                  <div className="text-2xl font-bold" style={assessment.browser_focus_ratio != null && assessment.browser_focus_ratio < 0.8 ? { color: '#dc2626' } : {}}>{assessment.browser_focus_ratio != null ? `${Math.round(assessment.browser_focus_ratio * 100)}%` : '--'}</div>
                </div>
                <div className="border-2 border-black p-4">
                  <div className="font-mono text-xs text-gray-500">Tab Switches</div>
                  <div className="text-2xl font-bold" style={assessment.tab_switch_count > 5 ? { color: '#dc2626' } : {}}>{assessment.tab_switch_count ?? '--'}</div>
                </div>
                <div className="border-2 border-black p-4">
                  <div className="font-mono text-xs text-gray-500">Calibration</div>
                  <div className="text-2xl font-bold">{assessment.calibration_score != null ? `${assessment.calibration_score.toFixed(1)}/10` : '--'}</div>
                  <div className="font-mono text-xs text-gray-500 mt-1">
                    vs avg {avgCalibrationScore != null ? `${avgCalibrationScore.toFixed(1)}/10` : '--'}
                  </div>
                </div>
              </div>

              {/* Browser Focus Warning */}
              {assessment.browser_focus_ratio != null && assessment.browser_focus_ratio < 0.8 && (
                <div className="border-2 border-yellow-500 bg-yellow-50 p-4">
                  <div className="font-bold text-yellow-700 flex items-center gap-2"><AlertTriangle size={18} /> Low Browser Focus ({Math.round(assessment.browser_focus_ratio * 100)}%)</div>
                  <div className="font-mono text-xs text-yellow-600 mt-1">Candidate spent less than 80% of assessment time with the browser in focus. {assessment.tab_switch_count > 5 ? `${assessment.tab_switch_count} tab switches recorded.` : ''}</div>
                </div>
              )}

              {/* Prompt Progression Chart */}
              {assessment.prompt_analytics?.per_prompt_scores?.length > 0 && (
                <div className="border-2 border-black p-4">
                  <div className="font-bold mb-4">Prompt Quality Progression</div>
                  <div style={{ width: '100%', height: 200 }}>
                    <ResponsiveContainer>
                      <LineChart data={assessment.prompt_analytics.per_prompt_scores.map((p, i) => ({ name: `#${i + 1}`, clarity: p.clarity || 0, specificity: p.specificity || 0, efficiency: p.efficiency || 0 }))}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="name" tick={{ fontSize: 10, fontFamily: 'monospace' }} />
                        <YAxis domain={[0, 10]} tick={{ fontSize: 10 }} />
                        <Tooltip />
                        <Line type="monotone" dataKey="clarity" stroke="#9D00FF" strokeWidth={2} dot={{ r: 3 }} />
                        <Line type="monotone" dataKey="specificity" stroke="#000" strokeWidth={1} />
                        <Line type="monotone" dataKey="efficiency" stroke="#666" strokeWidth={1} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}

              {/* Prompt Log */}
              <div className="border-2 border-black p-4">
                <div className="font-bold mb-4">Prompt Log ({(candidate.promptsList || []).length} prompts)</div>
                <div className="space-y-3">
                  {(candidate.promptsList || []).map((p, i) => {
                    const perPrompt = assessment.prompt_analytics?.per_prompt_scores?.[i];
                    return (
                      <div key={i} className="border border-gray-300 p-3">
                        <div className="flex items-center justify-between mb-2">
                          <div className="flex items-center gap-3">
                            <span className="font-mono text-xs font-bold bg-black text-white px-2 py-0.5">#{i + 1}</span>
                            {p.timestamp && <span className="font-mono text-xs text-gray-400">{new Date(p.timestamp).toLocaleTimeString()}</span>}
                            {perPrompt && <span className="font-mono text-xs text-gray-500">{perPrompt.word_count} words</span>}
                          </div>
                          <div className="flex items-center gap-2">
                            {perPrompt && (
                              <>
                                <span className="font-mono text-xs px-2 py-0.5 border" style={{ borderColor: '#9D00FF', color: '#9D00FF' }}>C:{perPrompt.clarity}</span>
                                <span className="font-mono text-xs px-2 py-0.5 border border-gray-400">S:{perPrompt.specificity}</span>
                                <span className="font-mono text-xs px-2 py-0.5 border border-gray-400">E:{perPrompt.efficiency}</span>
                              </>
                            )}
                          </div>
                        </div>
                        <div className="font-mono text-sm bg-gray-50 p-2 rounded">{p.message || p.text}</div>
                        <div className="flex items-center gap-2 mt-2">
                          {perPrompt?.has_context && <span className="text-xs font-mono px-2 py-0.5 bg-green-100 text-green-700 border border-green-300">Has Context</span>}
                          {perPrompt?.is_vague && <span className="text-xs font-mono px-2 py-0.5 bg-red-100 text-red-700 border border-red-300">Vague</span>}
                          {p.paste_detected && <span className="text-xs font-mono px-2 py-0.5 bg-yellow-100 text-yellow-700 border border-yellow-400">PASTED</span>}
                          {p.response_latency_ms && <span className="text-xs font-mono px-2 py-0.5 bg-gray-100 border border-gray-300">{p.response_latency_ms}ms</span>}
                        </div>
                      </div>
                    );
                  })}
                  {(candidate.promptsList || []).length === 0 && (
                    <div className="border-2 border-black p-8 text-center font-mono text-gray-500">
                      No prompt data available yet
                    </div>
                  )}
                </div>
              </div>

              {/* Prompt Statistics */}
              {(candidate.promptsList || []).length > 0 && assessment.prompt_analytics && (
                <div className="border-2 border-black p-4">
                  <div className="font-bold mb-3">Prompt Statistics</div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 font-mono text-sm">
                    <div><span className="text-gray-500">Avg Words:</span> {assessment.prompt_analytics.metric_details?.word_count_avg || '—'}</div>
                    <div><span className="text-gray-500">Questions:</span> {assessment.prompt_analytics.metric_details?.question_presence ? `${(assessment.prompt_analytics.metric_details.question_presence * 100).toFixed(0)}%` : '—'}</div>
                    <div><span className="text-gray-500">Code Context:</span> {assessment.prompt_analytics.metric_details?.code_snippet_rate ? `${(assessment.prompt_analytics.metric_details.code_snippet_rate * 100).toFixed(0)}%` : '—'}</div>
                    <div><span className="text-gray-500">Paste Detected:</span> {assessment.prompt_analytics.metric_details?.paste_ratio ? `${(assessment.prompt_analytics.metric_details.paste_ratio * 100).toFixed(0)}%` : '0%'}</div>
                  </div>
                </div>
              )}
            </div>
          );
        })()}

        {activeTab === 'cv-fit' && (() => {
          const assessment = candidate._raw || {};
          const cvMatch = assessment.cv_job_match_details || assessment.prompt_analytics?.cv_job_match?.details || {};
          const matchScores = assessment.prompt_analytics?.cv_job_match || {};
          const overall = matchScores.overall || assessment.cv_job_match_score;
          const skills = matchScores.skills;
          const experience = matchScores.experience;

          return (
            <div className="space-y-6">
              {/* Fit Score Cards */}
              {overall != null ? (
                <>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div className="border-2 border-black p-6 text-center">
                      <div className="font-mono text-xs text-gray-500 mb-1">Overall Match</div>
                      <div className="text-4xl font-bold" style={{ color: overall >= 7 ? '#16a34a' : overall >= 5 ? '#d97706' : '#dc2626' }}>{overall}/10</div>
                    </div>
                    <div className="border-2 border-black p-6 text-center">
                      <div className="font-mono text-xs text-gray-500 mb-1">Skills Match</div>
                      <div className="text-4xl font-bold" style={{ color: skills >= 7 ? '#16a34a' : skills >= 5 ? '#d97706' : '#dc2626' }}>{skills != null ? `${skills}/10` : '—'}</div>
                    </div>
                    <div className="border-2 border-black p-6 text-center">
                      <div className="font-mono text-xs text-gray-500 mb-1">Experience</div>
                      <div className="text-4xl font-bold" style={{ color: experience >= 7 ? '#16a34a' : experience >= 5 ? '#d97706' : '#dc2626' }}>{experience != null ? `${experience}/10` : '—'}</div>
                    </div>
                  </div>

                  {/* Matching Skills */}
                  {cvMatch.matching_skills?.length > 0 && (
                    <div className="border-2 border-black p-4">
                      <div className="font-bold mb-3 text-green-700">Matching Skills</div>
                      <div className="flex flex-wrap gap-2">
                        {cvMatch.matching_skills.map((skill, i) => (
                          <span key={i} className="px-3 py-1 bg-green-100 text-green-800 font-mono text-sm border border-green-300">{skill}</span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Missing Skills */}
                  {cvMatch.missing_skills?.length > 0 && (
                    <div className="border-2 border-black p-4">
                      <div className="font-bold mb-3 text-red-700">Missing Skills</div>
                      <div className="flex flex-wrap gap-2">
                        {cvMatch.missing_skills.map((skill, i) => (
                          <span key={i} className="px-3 py-1 bg-red-100 text-red-800 font-mono text-sm border border-red-300">{skill}</span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Experience Highlights */}
                  {cvMatch.experience_highlights?.length > 0 && (
                    <div className="border-2 border-black p-4">
                      <div className="font-bold mb-3">Relevant Experience</div>
                      <ul className="space-y-1">
                        {cvMatch.experience_highlights.map((exp, i) => (
                          <li key={i} className="font-mono text-sm text-gray-700 flex items-start gap-2">
                            <span className="text-green-600 mt-0.5">•</span>{exp}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Concerns */}
                  {cvMatch.concerns?.length > 0 && (
                    <div className="border-2 border-yellow-500 bg-yellow-50 p-4">
                      <div className="font-bold mb-3 text-yellow-700">Concerns</div>
                      <ul className="space-y-1">
                        {cvMatch.concerns.map((concern, i) => (
                          <li key={i} className="font-mono text-sm text-yellow-800 flex items-start gap-2">
                            <AlertTriangle size={14} className="mt-0.5 flex-shrink-0 text-yellow-600" />{concern}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Summary */}
                  {cvMatch.summary && (
                    <div className="border-2 border-black p-4">
                      <div className="font-bold mb-2">Summary</div>
                      <p className="font-mono text-sm text-gray-700 italic">&quot;{cvMatch.summary}&quot;</p>
                    </div>
                  )}
                </>
              ) : (
                <div className="border-2 border-black p-8 text-center">
                  <div className="font-mono text-gray-500 mb-2">No CV-Job fit analysis available</div>
                  <div className="font-mono text-xs text-gray-400">
                    Fit analysis requires both a CV and a job specification to be uploaded for this candidate.
                    Upload documents on the Candidates page.
                  </div>
                </div>
              )}

              {/* Document Status */}
              <div className="border-2 border-black p-4">
                <div className="font-bold mb-3">Documents</div>
                <div className="space-y-2 font-mono text-sm">
                  <div className="flex items-center gap-3">
                    <span>{assessment.cv_uploaded ? '✅' : '❌'}</span>
                    <span>CV: {assessment.cv_filename || 'Not uploaded'}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span>{candidate._raw?.score_breakdown?.cv_job_match?.overall != null ? '✅' : '❌'}</span>
                    <span>Job Specification: {candidate._raw?.score_breakdown?.cv_job_match?.overall != null ? 'Uploaded' : 'Not uploaded'}</span>
                  </div>
                </div>
              </div>
            </div>
          );
        })()}

        {activeTab === 'timeline' && (
          <div className="relative pl-8">
            <div className="absolute left-3 top-0 bottom-0 w-0.5" style={{ backgroundColor: '#9D00FF' }} />
            {candidate.timeline.map((t, i) => (
              <div key={i} className="relative mb-6 pl-8">
                <div
                  className="absolute -left-5 top-1 w-4 h-4 border-2 border-black"
                  style={{ backgroundColor: '#9D00FF' }}
                />
                <div className="font-mono text-xs text-gray-500 mb-1">{t.time}</div>
                <div className="font-bold">{t.event}</div>
                {t.prompt && (
                  <div className="font-mono text-sm text-gray-500 italic mt-1">&quot;{t.prompt}&quot;</div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

// ============================================================
// TASKS PAGE
// ============================================================

const TaskFormFields = ({ form, setForm, readOnly = false }) => {
  const noop = () => {};
  const upd = readOnly ? noop : setForm;
  const inputClass = (base) => `${base} ${readOnly ? 'bg-gray-100 cursor-default' : ''}`;
  return (
    <div className="space-y-4">
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Task Name *</label>
        <input
          type="text"
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none')}
          placeholder="e.g. Async Pipeline Debugging"
          value={form.name}
          onChange={(e) => upd((p) => ({ ...p, name: e.target.value }))}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Description *</label>
        <p className="font-mono text-xs text-gray-500 mb-1">What the candidate sees as the brief. Be specific about what they need to accomplish.</p>
        <textarea
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none min-h-[80px]')}
          placeholder="Fix 3 bugs in an async data pipeline that processes streaming JSON events..."
          value={form.description}
          onChange={(e) => upd((p) => ({ ...p, description: e.target.value }))}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
      <div className="grid grid-cols-3 gap-4">
        <div>
          <label className="block font-mono text-sm mb-1 font-bold">Type</label>
          <select
            className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white')}
            value={form.task_type}
            onChange={(e) => upd((p) => ({ ...p, task_type: e.target.value }))}
            disabled={readOnly}
          >
            <option value="debugging">Debugging</option>
            <option value="ai_engineering">AI Engineering</option>
            <option value="optimization">Optimization</option>
            <option value="build">Build from Scratch</option>
            <option value="refactor">Refactoring</option>
          </select>
        </div>
        <div>
          <label className="block font-mono text-sm mb-1 font-bold">Difficulty</label>
          <select
            className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white')}
            value={form.difficulty}
            onChange={(e) => upd((p) => ({ ...p, difficulty: e.target.value }))}
            disabled={readOnly}
          >
            <option value="junior">Junior</option>
            <option value="mid">Mid-Level</option>
            <option value="senior">Senior</option>
            <option value="staff">Staff+</option>
          </select>
        </div>
        <div>
          <label className="block font-mono text-sm mb-1 font-bold">Duration</label>
          <select
            className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white')}
            value={form.duration_minutes}
            onChange={(e) => upd((p) => ({ ...p, duration_minutes: parseInt(e.target.value) }))}
            disabled={readOnly}
          >
            <option value={15}>15 min</option>
            <option value={30}>30 min</option>
            <option value={45}>45 min</option>
            <option value={60}>60 min</option>
            <option value={90}>90 min</option>
          </select>
        </div>
      </div>
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Starter Code *</label>
        <p className="font-mono text-xs text-gray-500 mb-1">The code the candidate starts with. Include bugs, scaffolding, or an incomplete implementation.</p>
        <textarea
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-xs focus:outline-none min-h-[180px] bg-gray-50 leading-relaxed')}
          placeholder={"# Python starter code\n# Include realistic bugs or incomplete sections\n\ndef process_data(items):\n    ..."}
          value={form.starter_code}
          onChange={(e) => upd((p) => ({ ...p, starter_code: e.target.value }))}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Test Suite *</label>
        <p className="font-mono text-xs text-gray-500 mb-1">pytest tests that validate the correct solution. These run automatically when the candidate submits.</p>
        <textarea
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-xs focus:outline-none min-h-[120px] bg-gray-50 leading-relaxed')}
          placeholder={"import pytest\n\ndef test_basic_case():\n    assert process_data([1, 2, 3]) == [2, 4, 6]\n\ndef test_edge_case():\n    assert process_data([]) == []"}
          value={form.test_code}
          onChange={(e) => upd((p) => ({ ...p, test_code: e.target.value }))}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
    </div>
  );
};

const CreateTaskModal = ({ onClose, onCreated, initialTask, onUpdated, viewOnly = false }) => {
  const isEdit = Boolean(initialTask) && !viewOnly;
  const [step, setStep] = useState(initialTask ? 'manual' : 'choose');
  const [form, setForm] = useState({
    name: initialTask?.name ?? '',
    description: initialTask?.description ?? '',
    task_type: initialTask?.task_type ?? 'debugging',
    difficulty: initialTask?.difficulty ?? 'mid',
    duration_minutes: initialTask?.duration_minutes ?? 30,
    starter_code: initialTask?.starter_code ?? '',
    test_code: initialTask?.test_code ?? '',
  });
  const [aiPrompt, setAiPrompt] = useState('');
  const [aiDifficulty, setAiDifficulty] = useState('');
  const [aiDuration, setAiDuration] = useState('');
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (initialTask) {
      setForm({
        name: initialTask.name ?? '',
        description: initialTask.description ?? '',
        task_type: initialTask.task_type ?? 'debugging',
        difficulty: initialTask.difficulty ?? 'mid',
        duration_minutes: initialTask.duration_minutes ?? 30,
        starter_code: initialTask.starter_code ?? '',
        test_code: initialTask.test_code ?? '',
      });
      setStep('manual');
    }
  }, [initialTask]);

  const handleGenerate = async () => {
    setError('');
    if (!aiPrompt.trim()) {
      setError('Describe what you want to assess');
      return;
    }
    setGenerating(true);
    try {
      const res = await tasksApi.generate({
        prompt: aiPrompt,
        difficulty: aiDifficulty || undefined,
        duration_minutes: aiDuration ? parseInt(aiDuration) : undefined,
      });
      setForm(res.data);
      setStep('ai-review');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to generate task — try again');
    } finally {
      setGenerating(false);
    }
  };

  const handleSave = async () => {
    setError('');
    if (!form.name || !form.description) {
      setError('Name and description are required');
      return;
    }
    if (!form.starter_code) {
      setError('Starter code is required');
      return;
    }
    setLoading(true);
    try {
      if (isEdit && initialTask?.id) {
        const res = await tasksApi.update(initialTask.id, form);
        onUpdated?.(initialTask.id, res.data);
      } else {
        const res = await tasksApi.create({ ...form, is_active: true });
        onCreated(res.data);
      }
      onClose();
    } catch (err) {
      setError(err.response?.data?.detail || (isEdit ? 'Failed to update task' : 'Failed to create task'));
    } finally {
      setLoading(false);
    }
  };

  const modalTitle = viewOnly ? 'View Task' : isEdit ? 'Edit Task' : {
    'choose': 'Create New Task',
    'ai-prompt': 'Generate with AI',
    'ai-review': 'Review Generated Task',
    'manual': 'Create Task Manually',
  }[step];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="bg-white border-2 border-black w-full max-w-2xl max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-8 py-5 border-b-2 border-black">
          <div className="flex items-center gap-3">
            {!viewOnly && !isEdit && step !== 'choose' && (
              <button
                className="border-2 border-black p-1 hover:bg-black hover:text-white transition-colors"
                onClick={() => setStep(step === 'ai-review' ? 'ai-prompt' : 'choose')}
              >
                <ArrowLeft size={16} />
              </button>
            )}
            <h2 className="text-xl font-bold">{modalTitle}</h2>
          </div>
          <button className="border-2 border-black p-1 hover:bg-black hover:text-white transition-colors" onClick={onClose}>
            <X size={18} />
          </button>
        </div>

        <div className="px-8 py-6">
          {error && (
            <div className="border-2 border-red-500 bg-red-50 p-3 mb-5 font-mono text-sm text-red-700 flex items-center gap-2">
              <AlertTriangle size={16} /> {error}
            </div>
          )}

          {/* Step: Choose Path (skip when editing) */}
          {!isEdit && step === 'choose' && (
            <div className="space-y-4">
              <p className="font-mono text-sm text-gray-600 mb-6">How would you like to create your assessment task?</p>
              <button
                className="w-full border-2 border-black p-6 text-left hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-shadow group"
                onClick={() => setStep('ai-prompt')}
              >
                <div className="flex items-start gap-4">
                  <div
                    className="w-12 h-12 border-2 border-black flex items-center justify-center shrink-0"
                    style={{ backgroundColor: '#9D00FF' }}
                  >
                    <Bot size={24} className="text-white" />
                  </div>
                  <div>
                    <h3 className="font-bold text-lg mb-1">Generate with AI</h3>
                    <p className="font-mono text-sm text-gray-600">
                      Describe what you want to assess in plain English. Claude will generate the full task including starter code, bugs, and test suite.
                    </p>
                    <p className="font-mono text-xs mt-2" style={{ color: '#9D00FF' }}>
                      Recommended for quick setup
                    </p>
                  </div>
                </div>
              </button>
              <button
                className="w-full border-2 border-black p-6 text-left hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-shadow group"
                onClick={() => setStep('manual')}
              >
                <div className="flex items-start gap-4">
                  <div className="w-12 h-12 border-2 border-black flex items-center justify-center bg-black shrink-0">
                    <FileText size={24} className="text-white" />
                  </div>
                  <div>
                    <h3 className="font-bold text-lg mb-1">Create Manually</h3>
                    <p className="font-mono text-sm text-gray-600">
                      Write your own task from scratch. Full control over the description, starter code, test suite, and all parameters.
                    </p>
                    <p className="font-mono text-xs text-gray-400 mt-2">
                      Best for specific, custom assessments
                    </p>
                  </div>
                </div>
              </button>
            </div>
          )}

          {/* Step: AI Prompt (create only) */}
          {!isEdit && step === 'ai-prompt' && (
            <div className="space-y-5">
              <div>
                <label className="block font-mono text-sm mb-1 font-bold">What do you want to assess?</label>
                <p className="font-mono text-xs text-gray-500 mb-2">Be specific about the role, skills, and what kind of challenge you want. The more detail, the better the task.</p>
                <textarea
                  className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none min-h-[140px]"
                  placeholder={"Example: Create a debugging task for a senior Python backend engineer.\nThe code should be a REST API handler with 3 bugs:\n- An off-by-one error in pagination\n- A race condition in the cache layer\n- Incorrect error handling that swallows exceptions\nShould test async/await knowledge and production debugging skills."}
                  value={aiPrompt}
                  onChange={(e) => setAiPrompt(e.target.value)}
                  autoFocus
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block font-mono text-sm mb-1">Difficulty (optional)</label>
                  <select
                    className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white"
                    value={aiDifficulty}
                    onChange={(e) => setAiDifficulty(e.target.value)}
                  >
                    <option value="">Auto-detect</option>
                    <option value="junior">Junior</option>
                    <option value="mid">Mid-Level</option>
                    <option value="senior">Senior</option>
                    <option value="staff">Staff+</option>
                  </select>
                </div>
                <div>
                  <label className="block font-mono text-sm mb-1">Duration (optional)</label>
                  <select
                    className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white"
                    value={aiDuration}
                    onChange={(e) => setAiDuration(e.target.value)}
                  >
                    <option value="">Auto-detect</option>
                    <option value="15">15 min</option>
                    <option value="30">30 min</option>
                    <option value="45">45 min</option>
                    <option value="60">60 min</option>
                    <option value="90">90 min</option>
                  </select>
                </div>
              </div>
              <button
                className="w-full border-2 border-black py-3 font-bold text-white transition-colors flex items-center justify-center gap-2"
                style={{ backgroundColor: generating ? '#6b21a8' : '#9D00FF' }}
                onClick={handleGenerate}
                disabled={generating}
              >
                {generating ? (
                  <><Loader2 size={18} className="animate-spin" /> Generating task with Claude...</>
                ) : (
                  <><Zap size={18} /> Generate Task</>
                )}
              </button>
              {generating && (
                <p className="font-mono text-xs text-center text-gray-500">This usually takes 5-10 seconds...</p>
              )}
            </div>
          )}

          {/* Step: AI Review (create only) */}
          {!isEdit && step === 'ai-review' && (
            <div className="space-y-4">
              <div className="border-2 border-black p-3 mb-2 flex items-center gap-2" style={{ backgroundColor: '#f3e8ff' }}>
                <Bot size={16} style={{ color: '#9D00FF' }} />
                <span className="font-mono text-xs" style={{ color: '#6b21a8' }}>
                  AI-generated — review and edit anything below before saving
                </span>
              </div>
              <TaskFormFields form={form} setForm={setForm} />
              <div className="flex gap-3">
                <button
                  className="flex-1 border-2 border-black py-3 font-bold hover:bg-gray-100 transition-colors flex items-center justify-center gap-2"
                  onClick={() => setStep('ai-prompt')}
                >
                  <Zap size={16} /> Regenerate
                </button>
                <button
                  className="flex-1 border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors flex items-center justify-center gap-2"
                  style={{ backgroundColor: '#9D00FF' }}
                  onClick={handleSave}
                  disabled={loading}
                >
                  {loading ? <><Loader2 size={18} className="animate-spin" /> Saving...</> : <><Check size={18} /> Save Task</>}
                </button>
              </div>
            </div>
          )}

          {/* Step: Manual (create or edit or view) */}
          {step === 'manual' && (
            <div className="space-y-4">
              <TaskFormFields form={form} setForm={setForm} readOnly={viewOnly} />
              {!viewOnly && (
                <button
                  className="w-full border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors flex items-center justify-center gap-2"
                  style={{ backgroundColor: '#9D00FF' }}
                  onClick={handleSave}
                  disabled={loading}
                >
                  {loading ? <><Loader2 size={18} className="animate-spin" /> {isEdit ? 'Saving...' : 'Creating...'}</> : (isEdit ? 'Save changes' : 'Create Task')}
                </button>
              )}
              {viewOnly && (
                <button
                  type="button"
                  className="w-full border-2 border-black py-3 font-bold hover:bg-black hover:text-white transition-colors"
                  onClick={onClose}
                >
                  Close
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

const TasksPage = ({ onNavigate }) => {
  const [tasksList, setTasksList] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingTask, setEditingTask] = useState(null);
  const [viewingTask, setViewingTask] = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const fetchTasks = async () => {
      try {
        const res = await tasksApi.list();
        if (!cancelled) setTasksList(res.data || []);
      } catch (err) {
        console.warn('Failed to fetch tasks:', err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchTasks();
    return () => { cancelled = true; };
  }, []);

  const difficultyColors = {
    junior: '#22c55e',
    mid: '#FFAA00',
    senior: '#9D00FF',
    staff: '#FF0033',
  };

  return (
    <div>
      <DashboardNav currentPage="tasks" onNavigate={onNavigate} />
      <div className="hidden md:block max-w-7xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold">Tasks</h1>
            <p className="font-mono text-sm text-gray-600 mt-1">Manage assessment task templates</p>
          </div>
          <button
            className="border-2 border-black px-6 py-3 font-bold text-white hover:bg-black transition-colors flex items-center gap-2"
            style={{ backgroundColor: '#9D00FF' }}
            onClick={() => setShowCreateModal(true)}
          >
            <Code size={18} /> New Task
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-16 gap-3">
            <Loader2 size={24} className="animate-spin" style={{ color: '#9D00FF' }} />
            <span className="font-mono text-sm text-gray-500">Loading tasks...</span>
          </div>
        ) : tasksList.length === 0 ? (
          <div className="border-2 border-black p-16 text-center">
            <Code size={48} className="mx-auto mb-4 text-gray-300" />
            <h3 className="text-xl font-bold mb-2">No tasks yet</h3>
            <p className="font-mono text-sm text-gray-500 mb-6">Create your first task template to start assessing candidates</p>
            <button
              className="border-2 border-black px-6 py-3 font-bold text-white hover:bg-black transition-colors"
              style={{ backgroundColor: '#9D00FF' }}
              onClick={() => setShowCreateModal(true)}
            >
              Create Task
            </button>
          </div>
        ) : (
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {tasksList.map((task) => (
              <div key={task.id} className="border-2 border-black p-6 hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-shadow">
                <div className="flex items-center justify-between mb-3">
                  <span
                    className="px-3 py-1 text-xs font-mono font-bold text-white border-2 border-black"
                    style={{ backgroundColor: difficultyColors[task.difficulty] || '#9D00FF' }}
                  >
                    {task.difficulty?.toUpperCase()}
                  </span>
                  <span className="font-mono text-xs text-gray-500">{task.duration_minutes}min</span>
                </div>
                <h3 className="font-bold text-lg mb-2">{task.name}</h3>
                <p className="font-mono text-sm text-gray-600 mb-4 line-clamp-3">{task.description}</p>
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <span className="font-mono text-xs px-2 py-1 border border-gray-300">{task.task_type?.replace('_', ' ')}</span>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      className="border-2 border-black p-2 hover:bg-black hover:text-white transition-colors"
                      title="View task"
                      onClick={() => setViewingTask(task)}
                    >
                      <Eye size={14} />
                    </button>
                    {task.is_template && (
                      <span className="font-mono text-xs text-gray-400">template</span>
                    )}
                    {!task.is_template && (
                      <>
                        <button
                          type="button"
                          className="border-2 border-black p-2 hover:bg-black hover:text-white transition-colors"
                          title="Edit task"
                          onClick={() => setEditingTask(task)}
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          type="button"
                          className="border-2 border-red-600 text-red-600 p-2 hover:bg-red-600 hover:text-white transition-colors disabled:opacity-50"
                          title="Delete task"
                          disabled={deletingId === task.id}
                          onClick={async () => {
                            if (!window.confirm(`Delete "${task.name}"? This cannot be undone.`)) return;
                            setDeletingId(task.id);
                            try {
                              await tasksApi.delete(task.id);
                              setTasksList((prev) => prev.filter((t) => t.id !== task.id));
                            } catch (err) {
                              alert(err.response?.data?.detail || 'Failed to delete task');
                            } finally {
                              setDeletingId(null);
                            }
                          }}
                        >
                          {deletingId === task.id ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                        </button>
                      </>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {showCreateModal && (
        <CreateTaskModal
          onClose={() => setShowCreateModal(false)}
          onCreated={(newTask) => {
            setTasksList((prev) => [newTask, ...prev]);
          }}
        />
      )}
      {editingTask && (
        <CreateTaskModal
          initialTask={editingTask}
          onClose={() => setEditingTask(null)}
          onUpdated={(taskId, updatedTask) => {
            setTasksList((prev) => prev.map((t) => (t.id === taskId ? updatedTask : t)));
          }}
        />
      )}
      {viewingTask && (
        <CreateTaskModal
          initialTask={viewingTask}
          viewOnly
          onClose={() => setViewingTask(null)}
        />
      )}
    </div>
  );
};

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
          window.history.replaceState(null, '', `${window.location.origin}${window.location.pathname || '/'}#/settings`);
          onNavigate('settings');
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

const SettingsPage = ({ onNavigate }) => {
  const { user } = useAuth();
  const [settingsTab, setSettingsTab] = useState('workable');
  const [orgData, setOrgData] = useState(null);
  const [orgLoading, setOrgLoading] = useState(true);
  const [billingUsage, setBillingUsage] = useState(null);
  const [checkoutLoading, setCheckoutLoading] = useState(false);
  const [teamMembers, setTeamMembers] = useState([]);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteName, setInviteName] = useState('');
  const [inviteLoading, setInviteLoading] = useState(false);
  const [darkMode, setDarkMode] = useState(() => localStorage.getItem('tali_dark_mode') === '1');

  useEffect(() => {
    let cancelled = false;
    const fetchOrg = async () => {
      try {
        const res = await orgsApi.get();
        if (!cancelled) setOrgData(res.data);
      } catch (err) {
        console.warn('Failed to fetch org data:', err.message);
      } finally {
        if (!cancelled) setOrgLoading(false);
      }
    };
    fetchOrg();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (settingsTab !== 'billing') return;
    let cancelled = false;
    const fetchUsage = async () => {
      try {
        const res = await billingApi.usage();
        if (!cancelled) setBillingUsage(res.data);
      } catch (err) {
        console.warn('Failed to fetch billing usage:', err.message);
      }
    };
    fetchUsage();
    return () => { cancelled = true; };
  }, [settingsTab]);

  useEffect(() => {
    if (settingsTab !== 'team') return;
    let cancelled = false;
    const fetchTeam = async () => {
      try {
        const res = await teamApi.list();
        if (!cancelled) setTeamMembers(res.data || []);
      } catch (err) {
        console.warn('Failed to fetch team:', err.message);
      }
    };
    fetchTeam();
    return () => { cancelled = true; };
  }, [settingsTab]);

  useEffect(() => {
    localStorage.setItem('tali_dark_mode', darkMode ? '1' : '0');
    document.documentElement.classList.toggle('dark', darkMode);
    document.body.classList.toggle('bg-zinc-950', darkMode);
    document.body.classList.toggle('text-white', darkMode);
  }, [darkMode]);

  const handleAddCredits = async () => {
    const base = window.location.origin + window.location.pathname + '#/settings';
    setCheckoutLoading(true);
    try {
      const res = await billingApi.createCheckoutSession({
        success_url: base + '?payment=success',
        cancel_url: base,
      });
      if (res.data?.url) window.location.href = res.data.url;
      else setCheckoutLoading(false);
    } catch (err) {
      console.warn('Checkout failed:', err?.response?.data?.detail || err.message);
      setCheckoutLoading(false);
    }
  };

  const handleInvite = async (e) => {
    e.preventDefault();
    if (!inviteEmail || !inviteName) return;
    setInviteLoading(true);
    try {
      const res = await teamApi.invite({ email: inviteEmail, full_name: inviteName });
      setTeamMembers((prev) => [...prev, res.data]);
      setInviteEmail('');
      setInviteName('');
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to invite team member');
    } finally {
      setInviteLoading(false);
    }
  };

  // Derive display values from API data or fallback
  const orgName = orgData?.name || user?.organization?.name || '--';
  const adminEmail = user?.email || '--';
  const workableConnected = orgData?.workable_connected ?? false;
  const connectedSince = orgData?.workable_connected_at
    ? new Date(orgData.workable_connected_at).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
    : '—';
  const billingPlan = orgData?.plan || 'Pay-Per-Use';
  const costPerAssessment = 25;
  const usageHistory = billingUsage?.usage ?? [];
  const monthlyAssessments = usageHistory.length;
  const monthlyCost = billingUsage?.total_cost ?? 0;

  return (
    <div>
      <DashboardNav currentPage="settings" onNavigate={onNavigate} />
      <div className="max-w-7xl mx-auto px-6 py-8">
        <h1 className="text-3xl font-bold mb-2">Settings</h1>
        <p className="font-mono text-sm text-gray-600 mb-8">Manage integrations and billing</p>

        {/* Tabs */}
        <div className="flex border-2 border-black mb-8">
          {['workable', 'billing', 'team', 'preferences'].map((tab) => (
            <button
              key={tab}
              className={`flex-1 px-6 py-3 font-mono text-sm font-bold border-r-2 border-black last:border-r-0 transition-colors ${
                settingsTab === tab ? 'text-white' : 'bg-white hover:bg-gray-100'
              }`}
              style={settingsTab === tab ? { backgroundColor: '#9D00FF' } : {}}
              onClick={() => setSettingsTab(tab)}
            >
              {tab === 'workable' && 'Workable'}
              {tab === 'billing' && 'Billing'}
              {tab === 'team' && 'Team'}
              {tab === 'preferences' && 'Preferences'}
            </button>
          ))}
        </div>

        {orgLoading ? (
          <div className="flex items-center justify-center py-16 gap-3">
            <Loader2 size={24} className="animate-spin" style={{ color: '#9D00FF' }} />
            <span className="font-mono text-sm text-gray-500">Loading settings...</span>
          </div>
        ) : (
          <>
            {settingsTab === 'workable' && (
              <div>
                {/* Connected banner */}
                <div className={`border-2 border-black p-6 mb-8 flex items-center justify-between gap-4 flex-wrap ${workableConnected ? 'bg-green-50' : 'bg-yellow-50'}`}>
                  <div className="flex items-center gap-4">
                    {workableConnected ? (
                      <CheckCircle size={24} className="text-green-600" />
                    ) : (
                      <AlertTriangle size={24} className="text-yellow-600" />
                    )}
                    <div>
                      <div className="font-bold text-lg">Status: {workableConnected ? 'Connected' : 'Not Connected'}</div>
                      <div className="font-mono text-sm text-gray-600">
                        {workableConnected ? 'Workable integration is active' : 'Connect your Workable account to sync candidates'}
                      </div>
                    </div>
                  </div>
                  {!workableConnected && (
                    <ConnectWorkableButton />
                  )}
                </div>
                {/* Details */}
                <div className="border-2 border-black p-6 space-y-4">
                  <div>
                    <div className="font-mono text-xs text-gray-500 mb-1">Organization</div>
                    <div className="font-bold">{orgName}</div>
                  </div>
                  <div>
                    <div className="font-mono text-xs text-gray-500 mb-1">Admin Email</div>
                    <div className="font-mono">{adminEmail}</div>
                  </div>
                  <div>
                    <div className="font-mono text-xs text-gray-500 mb-1">Connected Since</div>
                    <div className="font-mono">{workableConnected ? connectedSince : '—'}</div>
                  </div>
                  <hr className="border-black" />
                  <div>
                    <div className="font-bold mb-3">Workflow Automation</div>
                    <div className="space-y-3">
                      {[
                        'Auto-send assessments when candidate reaches "Technical Screen" stage',
                        'Sync assessment results back to Workable candidate profile',
                        'Auto-advance candidates scoring 8+ to next stage',
                      ].map((item, i) => (
                        <label key={i} className="flex items-start gap-3 cursor-pointer">
                          <input type="checkbox" defaultChecked={workableConnected} className="mt-1 w-4 h-4 accent-purple-600" />
                          <span className="font-mono text-sm">{item}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {settingsTab === 'billing' && (
              <div>
                {/* Current Plan */}
                <div className="border-2 border-black p-6 mb-8">
                  <div className="flex items-start justify-between flex-wrap gap-4">
                    <div>
                      <div className="font-mono text-xs text-gray-500 mb-1">Current Plan</div>
                      <div className="text-2xl font-bold">{billingPlan}</div>
                      <div className="font-mono text-sm text-gray-600 mt-1">£{costPerAssessment} per assessment</div>
                    </div>
                    <div className="text-right">
                      <div className="font-mono text-xs text-gray-500 mb-1">Total usage</div>
                      <div className="text-3xl font-bold" style={{ color: '#9D00FF' }}>£{monthlyCost}</div>
                      <div className="font-mono text-xs text-gray-500">{monthlyAssessments} assessments</div>
                    </div>
                    <button
                      type="button"
                      onClick={handleAddCredits}
                      disabled={checkoutLoading}
                      className="flex items-center gap-2 px-6 py-3 font-mono text-sm font-bold border-2 border-black bg-black text-white hover:bg-gray-800 disabled:opacity-60 disabled:cursor-not-allowed"
                    >
                      {checkoutLoading ? <Loader2 size={18} className="animate-spin" /> : <CreditCard size={18} />}
                      {checkoutLoading ? 'Redirecting…' : 'Add credits (£25)'}
                    </button>
                  </div>
                </div>

                {/* Usage History */}
                <div className="border-2 border-black">
                  <div className="border-b-2 border-black px-6 py-4 bg-black text-white">
                    <h3 className="font-bold">Usage History</h3>
                  </div>
                  <table className="w-full">
                    <thead>
                      <tr className="border-b-2 border-black bg-gray-50">
                        <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Date</th>
                        <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Candidate</th>
                        <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Task</th>
                        <th className="text-right px-6 py-3 font-mono text-xs font-bold uppercase">Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {usageHistory.length === 0 ? (
                        <tr>
                          <td colSpan={4} className="px-6 py-8 font-mono text-sm text-gray-500 text-center">
                            No usage yet. Completed assessments will appear here.
                          </td>
                        </tr>
                      ) : (
                        usageHistory.map((row, i) => (
                          <tr key={row.assessment_id ?? i} className="border-b border-gray-200 hover:bg-gray-50">
                            <td className="px-6 py-3 font-mono text-sm">{row.date}</td>
                            <td className="px-6 py-3 text-sm">{row.candidate}</td>
                            <td className="px-6 py-3 font-mono text-sm">{row.task}</td>
                            <td className="px-6 py-3 font-mono text-sm text-right font-bold">{row.cost || `£${costPerAssessment}`}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {settingsTab === 'team' && (
              <div className="space-y-6">
                <div className="border-2 border-black p-6">
                  <h3 className="text-xl font-bold mb-4">Invite Team Member</h3>
                  <form className="grid md:grid-cols-3 gap-3" onSubmit={handleInvite}>
                    <input
                      type="text"
                      className="border-2 border-black px-3 py-2 font-mono text-sm"
                      placeholder="Full name"
                      value={inviteName}
                      onChange={(e) => setInviteName(e.target.value)}
                    />
                    <input
                      type="email"
                      className="border-2 border-black px-3 py-2 font-mono text-sm"
                      placeholder="Email"
                      value={inviteEmail}
                      onChange={(e) => setInviteEmail(e.target.value)}
                    />
                    <button
                      type="submit"
                      disabled={inviteLoading}
                      className="border-2 border-black px-4 py-2 font-mono font-bold text-white"
                      style={{ backgroundColor: '#9D00FF' }}
                    >
                      {inviteLoading ? 'Inviting…' : 'Invite'}
                    </button>
                  </form>
                </div>
                <div className="border-2 border-black">
                  <div className="border-b-2 border-black px-6 py-4 bg-black text-white">
                    <h3 className="font-bold">Team Members</h3>
                  </div>
                  <table className="w-full">
                    <thead>
                      <tr className="border-b-2 border-black bg-gray-50">
                        <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Name</th>
                        <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Email</th>
                      </tr>
                    </thead>
                    <tbody>
                      {teamMembers.length === 0 ? (
                        <tr><td colSpan={2} className="px-6 py-8 font-mono text-sm text-gray-500 text-center">No members yet.</td></tr>
                      ) : teamMembers.map((m) => (
                        <tr key={m.id} className="border-b border-gray-200">
                          <td className="px-6 py-3">{m.full_name || '—'}</td>
                          <td className="px-6 py-3 font-mono text-sm">{m.email}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {settingsTab === 'preferences' && (
              <div className="border-2 border-black p-6">
                <h3 className="text-xl font-bold mb-4">Display Preferences</h3>
                <label className="flex items-center gap-3 font-mono text-sm">
                  <input
                    type="checkbox"
                    checked={darkMode}
                    onChange={(e) => setDarkMode(e.target.checked)}
                    className="w-4 h-4 accent-purple-600"
                  />
                  Enable dark mode
                </label>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};

// ============================================================
// CANDIDATE PORTAL — WELCOME PAGE
// ============================================================

const CandidateWelcomePage = ({ token, assessmentId, onNavigate, onStarted }) => {
  const [loadingStart, setLoadingStart] = useState(false);
  const [startError, setStartError] = useState('');
  const [startData, setStartData] = useState(null);
  const [cvFile, setCvFile] = useState(null);
  const [cvUploading, setCvUploading] = useState(false);
  const [cvUploaded, setCvUploaded] = useState(false);
  const [cvError, setCvError] = useState('');

  const handleUploadCv = async () => {
    if (!token) {
      setCvError('Assessment link is missing required details.');
      return;
    }
    if (!cvFile) {
      setCvError('Please select your CV file first.');
      return;
    }
    setCvUploading(true);
    setCvError('');
    try {
      await assessmentsApi.uploadCv(assessmentId, token, cvFile);
      setCvUploaded(true);
    } catch (err) {
      setCvUploaded(false);
      setCvError(err?.response?.data?.detail || 'Failed to upload CV');
    } finally {
      setCvUploading(false);
    }
  };

  const handleStart = async () => {
    if (!token) {
      setStartError('Assessment token is missing from the link.');
      return;
    }
    if (!cvUploaded) {
      setStartError('Please upload your CV before starting the assessment.');
      return;
    }
    setLoadingStart(true);
    setStartError('');
    try {
      const res = await assessmentsApi.start(token);
      const data = res.data;
      // Store start data to show proctoring notice
      setStartData(data);
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

        {/* Proctoring notice */}
        {startData?.task?.proctoring_enabled && (
          <div className="border-2 border-yellow-500 bg-yellow-50 p-4 mb-8">
            <div className="font-bold text-yellow-700">This assessment is proctored</div>
            <div className="font-mono text-xs text-yellow-600 mt-1">Tab switches and browser focus will be monitored during this assessment.</div>
          </div>
        )}

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
            </ul>
          </div>
        </div>

        {startError && (
          <div className="border-2 border-red-500 bg-red-50 p-4 mb-4 font-mono text-sm text-red-700">
            {startError}
          </div>
        )}

        <div className="border-2 border-black p-6 mb-4">
          <div className="font-bold mb-2">CV Upload (Required)</div>
          <p className="font-mono text-xs text-gray-600 mb-3">Upload PDF or DOCX (max 5MB) before starting.</p>
          <input
            type="file"
            accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={(e) => {
              const file = e.target.files?.[0] || null;
              setCvFile(file);
              setCvUploaded(false);
              setCvError('');
            }}
            className="w-full border-2 border-black px-3 py-2 font-mono text-sm mb-3"
          />
          <button
            className="border-2 border-black px-4 py-2 font-mono text-sm bg-black text-white disabled:opacity-50"
            onClick={handleUploadCv}
            disabled={cvUploading || !cvFile}
          >
            {cvUploading ? 'Uploading CV…' : cvUploaded ? 'CV Uploaded' : 'Upload CV'}
          </button>
          {cvError && <div className="mt-2 font-mono text-xs text-red-600">{cvError}</div>}
        </div>

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

  // Workable OAuth callback: pathname is /settings/workable/callback?code=...
  const isWorkableCallback = typeof window !== 'undefined' && window.location.pathname === '/settings/workable/callback';
  const workableCallbackCode = isWorkableCallback ? new URLSearchParams(window.location.search).get('code') : null;

  // Parse hash route on initial load
  const initialHash = window.location.hash;
  const initialAssessMatch = initialHash.match(/^#\/assess\/(.+)$/);
  const initialAssessWithIdMatch = initialHash.match(/^#\/assessment\/(\d+)\?token=(.+)$/);
  const pathAssessMatch = typeof window !== 'undefined' ? window.location.pathname.match(/^\/assessment\/(\d+)$/) : null;
  const pathAssessToken = typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('token') : null;
  const initialResetMatch = initialHash.match(/^#\/reset-password(?:\?(.*))?$/);
  const initialVerifyMatch = initialHash.match(/^#\/verify-email(?:\?(.*))?$/);
  const getResetToken = () => {
    const qs = initialHash.split('?')[1] || '';
    const params = new URLSearchParams(qs);
    return params.get('token') || '';
  };
  const getVerifyToken = () => {
    const qs = initialHash.split('?')[1] || '';
    const params = new URLSearchParams(qs);
    return params.get('token') || '';
  };
  const [currentPage, setCurrentPage] = useState(
    isWorkableCallback
      ? 'workable-callback'
      : (initialAssessMatch || initialAssessWithIdMatch || (pathAssessMatch && pathAssessToken))
        ? 'candidate-welcome'
        : initialVerifyMatch
          ? 'verify-email'
          : initialResetMatch
            ? 'reset-password'
            : 'landing'
  );
  const [assessmentToken, setAssessmentToken] = useState(
    initialAssessWithIdMatch
      ? initialAssessWithIdMatch[2]
      : (pathAssessMatch && pathAssessToken)
        ? pathAssessToken
        : (initialAssessMatch ? initialAssessMatch[1] : null)
  );
  const [assessmentIdFromLink, setAssessmentIdFromLink] = useState(
    initialAssessWithIdMatch
      ? Number(initialAssessWithIdMatch[1])
      : (pathAssessMatch ? Number(pathAssessMatch[1]) : null)
  );
  const [resetPasswordToken, setResetPasswordToken] = useState(initialResetMatch ? getResetToken() : '');
  const [verifyEmailToken, setVerifyEmailToken] = useState(initialVerifyMatch ? getVerifyToken() : '');

  // Handle hash-based routing for candidate assessment and reset-password links
  useEffect(() => {
    const handleHashRoute = () => {
      const hash = window.location.hash;
      const assessMatch = hash.match(/^#\/assess\/(.+)$/);
      const assessWithIdMatch = hash.match(/^#\/assessment\/(\d+)\?token=(.+)$/);
      const resetMatch = hash.match(/^#\/reset-password(?:\?(.*))?$/);
      if (assessWithIdMatch) {
        setAssessmentToken(assessWithIdMatch[2]);
        setAssessmentIdFromLink(Number(assessWithIdMatch[1]));
        setCurrentPage('candidate-welcome');
      } else if (assessMatch) {
        setAssessmentToken(assessMatch[1]);
        setAssessmentIdFromLink(null);
        setCurrentPage('candidate-welcome');
      } else if (hash.match(/^#\/verify-email/)) {
        const qs = (hash.split('?')[1] || '');
        setVerifyEmailToken(new URLSearchParams(qs).get('token') || '');
        setCurrentPage('verify-email');
      } else if (resetMatch) {
        const qs = (hash.split('?')[1] || '');
        setResetPasswordToken(new URLSearchParams(qs).get('token') || '');
        setCurrentPage('reset-password');
      }
    };
    window.addEventListener('hashchange', handleHashRoute);
    return () => window.removeEventListener('hashchange', handleHashRoute);
  }, []);

  // Auto-redirect: if already authenticated and on landing/login/forgot-password, go to dashboard
  useEffect(() => {
    if (isAuthenticated && ['landing', 'login', 'forgot-password'].includes(currentPage)) {
      setCurrentPage('dashboard');
    }
  }, [isAuthenticated, currentPage]);

  // When user logs out, redirect to landing (except on reset-password and workable-callback which may be in progress)
  useEffect(() => {
    if (!authLoading && !isAuthenticated && ['dashboard', 'candidates', 'analytics', 'settings', 'tasks', 'candidate-detail'].includes(currentPage)) {
      setCurrentPage('landing');
    }
  }, [isAuthenticated, authLoading, currentPage]);

  const navigateToPage = (page) => {
    setCurrentPage(page);
    window.scrollTo(0, 0);
  };

  const handleCandidateStarted = (startData) => {
    setStartedAssessmentData(startData);
  };

  const navigateToCandidate = (candidate) => {
    setSelectedCandidate(candidate);
    setCurrentPage('candidate-detail');
    window.scrollTo(0, 0);
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
        <DashboardPage onNavigate={navigateToPage} onViewCandidate={navigateToCandidate} />
      )}
      {currentPage === 'candidates' && (
        <CandidatesPage onNavigate={navigateToPage} onViewCandidate={navigateToCandidate} />
      )}
      {currentPage === 'candidate-detail' && (
        <CandidateDetailPage
          candidate={selectedCandidate}
          onNavigate={navigateToPage}
          onDeleted={() => setSelectedCandidate(null)}
          onNoteAdded={(timeline) =>
            setSelectedCandidate((prev) => (prev ? { ...prev, timeline } : prev))
          }
        />
      )}
      {currentPage === 'tasks' && <TasksPage onNavigate={navigateToPage} />}
      {currentPage === 'analytics' && <AnalyticsPage onNavigate={navigateToPage} />}
      {currentPage === 'settings' && <SettingsPage onNavigate={navigateToPage} />}
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
