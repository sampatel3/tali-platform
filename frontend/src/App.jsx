import { useState, useEffect } from 'react';
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
} from 'lucide-react';
import { useAuth } from './context/AuthContext';
import { assessments as assessmentsApi, organizations as orgsApi, tasks as tasksApi } from './lib/api';
import AssessmentPage from './components/assessment/AssessmentPage';

// ============================================================
// DATA
// ============================================================

const candidates = [
  {
    id: 1,
    name: 'Sarah Chen',
    email: 'sarah.chen@example.com',
    task: 'Debugging',
    status: 'completed',
    score: 8.7,
    time: '28m',
    position: 'Senior Data Engineer',
    completedDate: '2 hours ago',
    breakdown: {
      bugsFixed: '3/3',
      testsPassed: '5/5',
      codeQuality: 8.5,
      timeEfficiency: 9.0,
      aiUsage: 8.5,
    },
    prompts: 7,
    promptsList: [
      {
        text: 'What\'s wrong with the delimiter in line 42?',
        assessment: 'Good diagnostic approach — targeted the exact issue',
      },
      {
        text: 'How should I handle the edge case for empty CSV rows?',
        assessment: 'Shows awareness of edge cases before coding',
      },
      {
        text: 'Can you explain the difference between pandas merge and join?',
        assessment: 'Learning-oriented prompt — positive signal',
      },
      {
        text: 'Write a test for the fixed parse_row function',
        assessment: 'Good use of AI for test generation',
      },
    ],
    timeline: [
      { time: '00:00', event: 'Started assessment' },
      { time: '02:30', event: 'Read through codebase and requirements' },
      { time: '05:00', event: 'Fixed delimiter bug (Bug 1/3)', prompt: 'What\'s wrong with the delimiter in line 42?' },
      { time: '12:00', event: 'Fixed empty row handling (Bug 2/3)', prompt: 'How should I handle the edge case for empty CSV rows?' },
      { time: '18:00', event: 'Identified race condition (Bug 3/3)' },
      { time: '22:00', event: 'Added test coverage', prompt: 'Write a test for the fixed parse_row function' },
      { time: '26:00', event: 'Final review and cleanup' },
      { time: '28:00', event: 'Submitted assessment' },
    ],
    results: [
      { title: 'Fixed delimiter bug', score: '9/10', description: 'Correctly identified tab vs comma delimiter issue in CSV parser' },
      { title: 'Fixed empty row handling', score: '8/10', description: 'Added proper null checks and empty row filtering' },
      { title: 'Fixed race condition', score: '9/10', description: 'Resolved async data pipeline race condition with proper awaits' },
    ],
  },
  {
    id: 2,
    name: 'Mike Ross',
    email: 'mike.ross@example.com',
    task: 'AI Engineer',
    status: 'completed',
    score: 7.2,
    time: '35m',
    position: 'AI Engineer',
    completedDate: 'yesterday',
    breakdown: {
      bugsFixed: '2/3',
      testsPassed: '4/5',
      codeQuality: 7.0,
      timeEfficiency: 7.5,
      aiUsage: 7.0,
    },
    prompts: 12,
    promptsList: [
      {
        text: 'Write the entire function for me',
        assessment: 'Over-reliance on AI — did not demonstrate own understanding',
      },
      {
        text: 'Fix the errors in this code',
        assessment: 'Vague prompt — could be more specific about the issue',
      },
    ],
    timeline: [
      { time: '00:00', event: 'Started assessment' },
      { time: '05:00', event: 'Read through codebase' },
      { time: '15:00', event: 'Fixed first bug' },
      { time: '25:00', event: 'Fixed second bug' },
      { time: '35:00', event: 'Submitted (third bug not fixed)' },
    ],
    results: [
      { title: 'Fixed parsing error', score: '7/10', description: 'Resolved JSON parsing issue but solution could be cleaner' },
      { title: 'Fixed validation bug', score: '7/10', description: 'Added input validation but missed some edge cases' },
    ],
  },
  {
    id: 3,
    name: 'Amy Wong',
    email: 'amy.wong@example.com',
    task: 'Optimization',
    status: 'in-progress',
    score: null,
    time: '15m',
    position: 'Full Stack Developer',
    completedDate: null,
    breakdown: null,
    prompts: 4,
    promptsList: [],
    timeline: [
      { time: '00:00', event: 'Started assessment' },
      { time: '10:00', event: 'Analyzing codebase' },
    ],
    results: [],
  },
  {
    id: 4,
    name: 'James Liu',
    email: 'james.liu@example.com',
    task: 'RAG Pipeline',
    status: 'completed',
    score: 6.2,
    time: '40m',
    position: 'ML Engineer',
    completedDate: '3 days ago',
    breakdown: {
      bugsFixed: '2/3',
      testsPassed: '3/5',
      codeQuality: 6.0,
      timeEfficiency: 6.5,
      aiUsage: 6.0,
    },
    prompts: 18,
    promptsList: [
      {
        text: 'Do everything for me step by step',
        assessment: 'Heavy AI dependency — limited independent problem-solving',
      },
    ],
    timeline: [
      { time: '00:00', event: 'Started assessment' },
      { time: '20:00', event: 'First bug identified' },
      { time: '35:00', event: 'Partial fix submitted' },
      { time: '40:00', event: 'Time ran out' },
    ],
    results: [
      { title: 'Partial RAG fix', score: '6/10', description: 'Identified the vector store issue but fix was incomplete' },
      { title: 'Embedding update', score: '6/10', description: 'Updated embeddings but introduced regression' },
    ],
  },
];

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

const DemoBanner = () => (
  <div className="border-b-2 border-black p-3" style={{ backgroundColor: '#9D00FF' }}>
    <div className="max-w-7xl mx-auto px-6 text-center">
      <span className="font-bold text-white text-sm">INTERACTIVE DEMO — Explore all features</span>
    </div>
  </div>
);

const StatsCard = ({ icon: Icon, label, value, change }) => (
  <div
    className="border-2 border-black bg-white p-6 hover:shadow-lg transition-shadow cursor-pointer"
    onClick={() => console.log(`Stats card clicked: ${label} — ${value}`)}
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
  const [email, setEmail] = useState('sam@deeplight.ai');
  const [password, setPassword] = useState('demo1234');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleLogin = async () => {
    setError('');
    setLoading(true);
    try {
      await login(email, password);
      onNavigate('dashboard');
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || 'Login failed';
      setError(typeof msg === 'string' ? msg : 'Invalid credentials');
      // Fallback: allow demo mode navigation even if API is down
      if (!err.response) {
        console.warn('API unreachable — entering demo mode');
        onNavigate('dashboard');
      }
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
          <div className="border-2 bg-black text-white p-4 mb-6" style={{ borderColor: '#9D00FF' }}>
            <div className="font-bold mb-2" style={{ color: '#9D00FF' }}>DEMO MODE</div>
            <p className="font-mono text-xs">Sign in with credentials or click &quot;Sign In&quot; to explore the dashboard</p>
          </div>
          {error && (
            <div className="border-2 border-red-500 bg-red-50 p-4 mb-6 flex items-center gap-2">
              <AlertTriangle size={18} className="text-red-500 flex-shrink-0" />
              <span className="font-mono text-sm text-red-700">{error}</span>
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
              <button className="font-mono text-sm hover:underline" style={{ color: '#9D00FF' }}>
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

  const updateField = (field) => (e) => setForm((prev) => ({ ...prev, [field]: e.target.value }));

  const handleRegister = async () => {
    setError('');
    if (!form.email || !form.password || !form.full_name) {
      setError('Email, password, and full name are required');
      return;
    }
    setLoading(true);
    try {
      await register(form);
      setSuccess(true);
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || 'Registration failed';
      setError(typeof msg === 'string' ? msg : 'Registration failed');
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
          {success ? (
            <div className="border-2 border-black p-8 text-center">
              <CheckCircle size={48} className="mx-auto mb-4" style={{ color: '#9D00FF' }} />
              <h2 className="text-2xl font-bold mb-2">Account Created!</h2>
              <p className="font-mono text-sm text-gray-600 mb-6">You can now sign in with your credentials.</p>
              <button
                className="w-full border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors"
                style={{ backgroundColor: '#9D00FF' }}
                onClick={() => onNavigate('login')}
              >
                Go to Sign In
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
// DASHBOARD NAV
// ============================================================

const DashboardNav = ({ currentPage, onNavigate }) => {
  const { user, logout } = useAuth();
  const orgName = user?.organization?.name || 'DeepLight AI';
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

const NewAssessmentModal = ({ onClose, onCreated }) => {
  const [tasksList, setTasksList] = useState([]);
  const [form, setForm] = useState({ candidate_email: '', candidate_name: '', task_id: '' });
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

const DashboardPage = ({ onNavigate, onViewCandidate }) => {
  const { user } = useAuth();
  const [assessmentsList, setAssessmentsList] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showNewModal, setShowNewModal] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetchAssessments = async () => {
      try {
        const res = await assessmentsApi.list();
        if (!cancelled) setAssessmentsList(res.data || []);
      } catch (err) {
        console.warn('Failed to fetch assessments, using mock data:', err.message);
        if (!cancelled) setAssessmentsList([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchAssessments();
    return () => { cancelled = true; };
  }, []);

  // Map API assessments to table-friendly shape, falling back to mock data
  const displayCandidates = assessmentsList.length > 0
    ? assessmentsList.map((a) => ({
        id: a.id,
        name: a.candidate_name || a.candidate?.full_name || a.candidate_email || 'Unknown',
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
        // Keep raw data for detail view
        _raw: a,
      }))
    : candidates;

  const userName = user?.full_name?.split(' ')[0] || 'Sam';

  // Compute live stats from API data
  const totalAssessments = displayCandidates.length;
  const completedCount = displayCandidates.filter((c) => c.status === 'completed' || c.status === 'submitted' || c.status === 'graded').length;
  const completionRate = totalAssessments > 0 ? ((completedCount / totalAssessments) * 100).toFixed(1) : '0';
  const scores = displayCandidates.filter((c) => c.score !== null).map((c) => c.score);
  const avgScore = scores.length > 0 ? (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1) : '—';
  const monthCost = `£${completedCount * 25}`;

  return (
    <div>
      <DemoBanner />
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

        {/* Stats Cards */}
        <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
          <StatsCard icon={Clipboard} label="Active Assessments" value={String(totalAssessments)} change={`${completedCount} completed`} />
          <StatsCard icon={CheckCircle} label="Completion Rate" value={`${completionRate}%`} change="Industry avg: 65%" />
          <StatsCard icon={Star} label="Avg Score" value={avgScore !== '—' ? `${avgScore}/10` : '—'} change="Candidates this month" />
          <StatsCard icon={DollarSign} label="This Month Cost" value={monthCost} change={`${completedCount} assessments`} />
        </div>

        {/* Assessments Table */}
        <div className="border-2 border-black">
          <div className="border-b-2 border-black px-6 py-4 bg-black text-white">
            <h2 className="font-bold text-lg">Recent Assessments</h2>
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
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Candidate</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Task</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Status</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Score</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Time</th>
                  <th className="text-left px-6 py-3 font-mono text-xs font-bold uppercase">Actions</th>
                </tr>
              </thead>
              <tbody>
                {displayCandidates.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-6 py-12 text-center font-mono text-sm text-gray-500">
                      No assessments yet. Click &quot;New Assessment&quot; to create one.
                    </td>
                  </tr>
                ) : (
                  displayCandidates.map((c) => (
                    <tr key={c.id} className="border-b border-gray-200 hover:bg-gray-50 transition-colors">
                      <td className="px-6 py-4">
                        <div className="font-bold">{c.name}</div>
                        <div className="font-mono text-xs text-gray-500">{c.email}</div>
                      </td>
                      <td className="px-6 py-4 font-mono text-sm">{c.task}</td>
                      <td className="px-6 py-4"><StatusBadge status={c.status} /></td>
                      <td className="px-6 py-4 font-bold">{c.score !== null ? `${c.score}/10` : '—'}</td>
                      <td className="px-6 py-4 font-mono text-sm">{c.time}</td>
                      <td className="px-6 py-4">
                        {c.status === 'completed' || c.status === 'submitted' || c.status === 'graded' ? (
                          <button
                            className="border-2 border-black bg-white px-4 py-2 font-mono text-sm font-bold hover:bg-black hover:text-white transition-colors flex items-center gap-1"
                            onClick={() => onViewCandidate(c)}
                          >
                            <Eye size={14} /> View
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
// CANDIDATE DETAIL PAGE
// ============================================================

const CandidateDetailPage = ({ candidate, onNavigate }) => {
  const [activeTab, setActiveTab] = useState('results');

  if (!candidate) return null;

  const getRecommendation = (score) => {
    if (score >= 8) return { label: 'RECOMMENDED', color: '#9D00FF' };
    if (score >= 7) return { label: 'CONSIDER', color: '#FFAA00' };
    return { label: 'NOT RECOMMENDED', color: '#FF0033' };
  };

  const rec = candidate.score ? getRecommendation(candidate.score) : null;

  return (
    <div>
      <DemoBanner />
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
          {candidate.score && (
            <div className="border-2 bg-black p-6 text-white" style={{ borderColor: '#9D00FF' }}>
              <div className="text-5xl font-bold mb-2" style={{ color: '#9D00FF' }}>
                {candidate.score}/10
              </div>
              {rec && (
                <div
                  className="inline-block px-3 py-1 text-xs font-bold font-mono text-white mb-4 border-2 border-black"
                  style={{ backgroundColor: rec.color }}
                >
                  {rec.label}
                </div>
              )}
              {candidate.breakdown && (
                <div className="space-y-2 font-mono text-xs">
                  <div className="flex justify-between"><span className="text-gray-400">Bugs Fixed</span><span>{candidate.breakdown.bugsFixed}</span></div>
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
        <div className="flex border-2 border-black mb-6">
          {['results', 'ai-usage', 'timeline'].map((tab) => (
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
              {tab === 'timeline' && 'Timeline'}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        {activeTab === 'results' && (
          <div className="space-y-4">
            {candidate.results.length > 0 ? (
              candidate.results.map((r, i) => (
                <div key={i} className="border-2 border-black bg-green-50 p-6 flex items-start gap-4">
                  <CheckCircle size={24} style={{ color: '#9D00FF' }} className="mt-1 flex-shrink-0" />
                  <div>
                    <div className="font-bold text-lg">
                      {r.title} <span className="font-mono text-sm text-gray-500">({r.score})</span>
                    </div>
                    <p className="font-mono text-sm text-gray-600 mt-1">{r.description}</p>
                  </div>
                </div>
              ))
            ) : (
              <div className="border-2 border-black p-8 text-center font-mono text-gray-500">
                No results yet — assessment in progress
              </div>
            )}
          </div>
        )}

        {activeTab === 'ai-usage' && (
          <div>
            <div className="border-2 border-black p-6 mb-6" style={{ backgroundColor: '#f3e8ff' }}>
              <div className="flex gap-8">
                <div>
                  <div className="font-mono text-xs text-gray-600 mb-1">Prompts Used</div>
                  <div className="text-3xl font-bold">{candidate.prompts}</div>
                </div>
                <div>
                  <div className="font-mono text-xs text-gray-600 mb-1">Quality Score</div>
                  <div className="text-3xl font-bold">{candidate.breakdown?.aiUsage || '—'}/10</div>
                </div>
              </div>
            </div>
            <div className="space-y-4">
              {candidate.promptsList.map((p, i) => (
                <div key={i} className="border-2 border-black p-6">
                  <div className="flex items-start gap-3">
                    <Bot size={20} className="mt-1 flex-shrink-0" style={{ color: '#9D00FF' }} />
                    <div>
                      <p className="font-mono text-sm mb-2">&quot;{p.text}&quot;</p>
                      <p className="text-sm italic" style={{ color: '#9D00FF' }}>{p.assessment}</p>
                    </div>
                  </div>
                </div>
              ))}
              {candidate.promptsList.length === 0 && (
                <div className="border-2 border-black p-8 text-center font-mono text-gray-500">
                  No prompt data available yet
                </div>
              )}
            </div>
          </div>
        )}

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

const TaskFormFields = ({ form, setForm }) => (
  <div className="space-y-4">
    <div>
      <label className="block font-mono text-sm mb-1 font-bold">Task Name *</label>
      <input
        type="text"
        className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
        placeholder="e.g. Async Pipeline Debugging"
        value={form.name}
        onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
      />
    </div>
    <div>
      <label className="block font-mono text-sm mb-1 font-bold">Description *</label>
      <p className="font-mono text-xs text-gray-500 mb-1">What the candidate sees as the brief. Be specific about what they need to accomplish.</p>
      <textarea
        className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none min-h-[80px]"
        placeholder="Fix 3 bugs in an async data pipeline that processes streaming JSON events..."
        value={form.description}
        onChange={(e) => setForm((p) => ({ ...p, description: e.target.value }))}
      />
    </div>
    <div className="grid grid-cols-3 gap-4">
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Type</label>
        <select
          className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white"
          value={form.task_type}
          onChange={(e) => setForm((p) => ({ ...p, task_type: e.target.value }))}
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
          className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white"
          value={form.difficulty}
          onChange={(e) => setForm((p) => ({ ...p, difficulty: e.target.value }))}
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
          className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white"
          value={form.duration_minutes}
          onChange={(e) => setForm((p) => ({ ...p, duration_minutes: parseInt(e.target.value) }))}
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
        className="w-full border-2 border-black px-4 py-3 font-mono text-xs focus:outline-none min-h-[180px] bg-gray-50 leading-relaxed"
        placeholder={"# Python starter code\n# Include realistic bugs or incomplete sections\n\ndef process_data(items):\n    ..."}
        value={form.starter_code}
        onChange={(e) => setForm((p) => ({ ...p, starter_code: e.target.value }))}
      />
    </div>
    <div>
      <label className="block font-mono text-sm mb-1 font-bold">Test Suite *</label>
      <p className="font-mono text-xs text-gray-500 mb-1">pytest tests that validate the correct solution. These run automatically when the candidate submits.</p>
      <textarea
        className="w-full border-2 border-black px-4 py-3 font-mono text-xs focus:outline-none min-h-[120px] bg-gray-50 leading-relaxed"
        placeholder={"import pytest\n\ndef test_basic_case():\n    assert process_data([1, 2, 3]) == [2, 4, 6]\n\ndef test_edge_case():\n    assert process_data([]) == []"}
        value={form.test_code}
        onChange={(e) => setForm((p) => ({ ...p, test_code: e.target.value }))}
      />
    </div>
  </div>
);

const CreateTaskModal = ({ onClose, onCreated }) => {
  // Step: 'choose' | 'ai-prompt' | 'ai-review' | 'manual'
  const [step, setStep] = useState('choose');
  const [form, setForm] = useState({
    name: '',
    description: '',
    task_type: 'debugging',
    difficulty: 'mid',
    duration_minutes: 30,
    starter_code: '',
    test_code: '',
  });
  const [aiPrompt, setAiPrompt] = useState('');
  const [aiDifficulty, setAiDifficulty] = useState('');
  const [aiDuration, setAiDuration] = useState('');
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');

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
      const res = await tasksApi.create({ ...form, is_active: true });
      onCreated(res.data);
      onClose();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to create task');
    } finally {
      setLoading(false);
    }
  };

  const modalTitle = {
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
            {step !== 'choose' && (
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

          {/* Step: Choose Path */}
          {step === 'choose' && (
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

          {/* Step: AI Prompt */}
          {step === 'ai-prompt' && (
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

          {/* Step: AI Review (editable) */}
          {step === 'ai-review' && (
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

          {/* Step: Manual */}
          {step === 'manual' && (
            <div className="space-y-4">
              <TaskFormFields form={form} setForm={setForm} />
              <button
                className="w-full border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors flex items-center justify-center gap-2"
                style={{ backgroundColor: '#9D00FF' }}
                onClick={handleSave}
                disabled={loading}
              >
                {loading ? <><Loader2 size={18} className="animate-spin" /> Creating...</> : 'Create Task'}
              </button>
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
      <DemoBanner />
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
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs px-2 py-1 border border-gray-300">{task.task_type?.replace('_', ' ')}</span>
                  {task.is_template && (
                    <span className="font-mono text-xs text-gray-400">template</span>
                  )}
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
    </div>
  );
};

// ============================================================
// ANALYTICS PAGE
// ============================================================

const AnalyticsPage = ({ onNavigate }) => {
  const maxRate = 100;
  return (
    <div>
      <DemoBanner />
      <DashboardNav currentPage="analytics" onNavigate={onNavigate} />
      <div className="max-w-7xl mx-auto px-6 py-8">
        <h1 className="text-3xl font-bold mb-2">Analytics</h1>
        <p className="font-mono text-sm text-gray-600 mb-8">Assessment performance over time</p>

        {/* Completion Rate Chart */}
        <div className="border-2 border-black p-8 mb-8">
          <h2 className="font-bold text-xl mb-6">Completion Rate</h2>
          <div className="flex items-end gap-4 h-64">
            {weeklyData.map((w, i) => (
              <div key={i} className="flex-1 flex flex-col items-center justify-end h-full">
                <div className="font-mono text-xs mb-2 font-bold">{w.rate}%</div>
                <div
                  className="w-full border-2 border-black transition-all"
                  style={{
                    height: `${(w.rate / maxRate) * 100}%`,
                    backgroundColor: i === weeklyData.length - 1 ? '#9D00FF' : '#e5e7eb',
                  }}
                />
                <div className="font-mono text-xs mt-2 text-gray-600">{w.week}</div>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-6 mt-6 font-mono text-xs">
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 border-2 border-black" style={{ backgroundColor: '#9D00FF' }} />
              <span>Your rate: 87.5%</span>
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
            <div className="text-4xl font-bold">52</div>
            <div className="font-mono text-xs text-gray-500 mt-1">Last 30 days</div>
          </div>
          <div className="border-2 border-black p-6">
            <div className="font-mono text-sm text-gray-600 mb-2">Top Score</div>
            <div className="text-4xl font-bold" style={{ color: '#9D00FF' }}>9.2/10</div>
            <div className="font-mono text-xs text-gray-500 mt-1">Sarah Chen — Debugging</div>
          </div>
          <div className="border-2 border-black p-6">
            <div className="font-mono text-sm text-gray-600 mb-2">Avg Time to Complete</div>
            <div className="text-4xl font-bold">32m</div>
            <div className="font-mono text-xs text-gray-500 mt-1">Out of 45m allowed</div>
          </div>
        </div>
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

  // Derive display values from API data or fallback
  const orgName = orgData?.name || user?.organization?.name || 'DeepLight AI';
  const adminEmail = user?.email || 'sam@deeplight.ai';
  const workableConnected = orgData?.workable_connected ?? true;
  const connectedSince = orgData?.workable_connected_at
    ? new Date(orgData.workable_connected_at).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
    : 'January 15, 2026';
  const billingPlan = orgData?.billing_plan || 'Pay-Per-Use';
  const costPerAssessment = orgData?.cost_per_assessment ?? 25;
  const monthlyAssessments = orgData?.monthly_assessment_count ?? 13;
  const monthlyCost = orgData?.monthly_cost ?? monthlyAssessments * costPerAssessment;

  // Usage history: from API or fallback
  const usageHistory = orgData?.usage_history || [
    { date: 'Feb 10, 2026', candidate: 'Sarah Chen', task: 'Debugging', cost: '£25' },
    { date: 'Feb 9, 2026', candidate: 'Mike Ross', task: 'AI Engineer', cost: '£25' },
    { date: 'Feb 8, 2026', candidate: 'Amy Wong', task: 'Optimization', cost: '£25' },
    { date: 'Feb 5, 2026', candidate: 'James Liu', task: 'RAG Pipeline', cost: '£25' },
    { date: 'Feb 3, 2026', candidate: 'Priya Sharma', task: 'Debugging', cost: '£25' },
  ];

  return (
    <div>
      <DemoBanner />
      <DashboardNav currentPage="settings" onNavigate={onNavigate} />
      <div className="max-w-7xl mx-auto px-6 py-8">
        <h1 className="text-3xl font-bold mb-2">Settings</h1>
        <p className="font-mono text-sm text-gray-600 mb-8">Manage integrations and billing</p>

        {/* Tabs */}
        <div className="flex border-2 border-black mb-8 max-w-md">
          {['workable', 'billing'].map((tab) => (
            <button
              key={tab}
              className={`flex-1 px-6 py-3 font-mono text-sm font-bold border-r-2 border-black last:border-r-0 transition-colors ${
                settingsTab === tab ? 'text-white' : 'bg-white hover:bg-gray-100'
              }`}
              style={settingsTab === tab ? { backgroundColor: '#9D00FF' } : {}}
              onClick={() => setSettingsTab(tab)}
            >
              {tab === 'workable' ? 'Workable' : 'Billing'}
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
                <div className={`border-2 border-black p-6 mb-8 flex items-center gap-4 ${workableConnected ? 'bg-green-50' : 'bg-yellow-50'}`}>
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
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="font-mono text-xs text-gray-500 mb-1">Current Plan</div>
                      <div className="text-2xl font-bold">{billingPlan}</div>
                      <div className="font-mono text-sm text-gray-600 mt-1">£{costPerAssessment} per assessment</div>
                    </div>
                    <div className="text-right">
                      <div className="font-mono text-xs text-gray-500 mb-1">This Month</div>
                      <div className="text-3xl font-bold" style={{ color: '#9D00FF' }}>£{monthlyCost}</div>
                      <div className="font-mono text-xs text-gray-500">{monthlyAssessments} assessments</div>
                    </div>
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
                      {usageHistory.map((row, i) => (
                        <tr key={i} className="border-b border-gray-200 hover:bg-gray-50">
                          <td className="px-6 py-3 font-mono text-sm">{row.date}</td>
                          <td className="px-6 py-3 text-sm">{row.candidate}</td>
                          <td className="px-6 py-3 font-mono text-sm">{row.task}</td>
                          <td className="px-6 py-3 font-mono text-sm text-right font-bold">{row.cost || `£${costPerAssessment}`}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
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

const CandidateWelcomePage = ({ token, onNavigate }) => {
  const [assessmentData, setAssessmentData] = useState(null);
  const [loadingStart, setLoadingStart] = useState(false);
  const [startError, setStartError] = useState('');

  const handleStart = async () => {
    if (!token) {
      onNavigate('assessment');
      return;
    }
    setLoadingStart(true);
    setStartError('');
    try {
      const res = await assessmentsApi.start(token);
      // Store the started assessment data for the assessment page
      setAssessmentData(res.data);
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
  const [currentPage, setCurrentPage] = useState('landing');
  const [selectedCandidate, setSelectedCandidate] = useState(null);
  const [assessmentToken, setAssessmentToken] = useState(null);

  // Handle hash-based routing for candidate assessment links: /#/assess/:token
  useEffect(() => {
    const handleHashRoute = () => {
      const hash = window.location.hash;
      const assessMatch = hash.match(/^#\/assess\/(.+)$/);
      if (assessMatch) {
        setAssessmentToken(assessMatch[1]);
        setCurrentPage('candidate-welcome');
      }
    };
    handleHashRoute();
    window.addEventListener('hashchange', handleHashRoute);
    return () => window.removeEventListener('hashchange', handleHashRoute);
  }, []);

  // Auto-redirect: if already authenticated and on landing/login, go to dashboard
  useEffect(() => {
    if (isAuthenticated && (currentPage === 'landing' || currentPage === 'login')) {
      setCurrentPage('dashboard');
    }
  }, [isAuthenticated, currentPage]);

  // When user logs out (isAuthenticated becomes false), redirect to landing
  useEffect(() => {
    if (!authLoading && !isAuthenticated && ['dashboard', 'analytics', 'settings', 'tasks', 'candidate-detail'].includes(currentPage)) {
      setCurrentPage('landing');
    }
  }, [isAuthenticated, authLoading, currentPage]);

  const navigateToPage = (page) => {
    setCurrentPage(page);
    window.scrollTo(0, 0);
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
      {currentPage === 'dashboard' && (
        <DashboardPage onNavigate={navigateToPage} onViewCandidate={navigateToCandidate} />
      )}
      {currentPage === 'candidate-detail' && (
        <CandidateDetailPage candidate={selectedCandidate} onNavigate={navigateToPage} />
      )}
      {currentPage === 'tasks' && <TasksPage onNavigate={navigateToPage} />}
      {currentPage === 'analytics' && <AnalyticsPage onNavigate={navigateToPage} />}
      {currentPage === 'settings' && <SettingsPage onNavigate={navigateToPage} />}
      {currentPage === 'candidate-welcome' && (
        <CandidateWelcomePage token={assessmentToken} onNavigate={navigateToPage} />
      )}
      {currentPage === 'assessment' && (
        <AssessmentPage token={assessmentToken} />
      )}
    </div>
  );
}

export default App;
