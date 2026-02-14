import React, { useState } from 'react';
import {
  Brain,
  Check,
  ChevronRight,
  Loader2,
  Shield,
  Terminal,
} from 'lucide-react';

import { assessments as assessmentsApi } from '../../shared/api';
import { Logo } from '../../shared/ui/Branding';

export const CandidateWelcomePage = ({ token, assessmentId, onNavigate, onStarted }) => {
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
      if (onStarted) onStarted(data);
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
            TAALI Assessment
          </div>
          <h1 className="text-4xl font-bold mb-2">Technical Assessment</h1>
          <p className="font-mono text-gray-600">You&apos;ve been invited to complete a coding challenge</p>
        </div>

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
