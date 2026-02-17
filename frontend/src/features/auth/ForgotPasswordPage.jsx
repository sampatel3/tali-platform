import React, { useState } from 'react';
import { AlertTriangle, CheckCircle } from 'lucide-react';

import { auth } from '../../shared/api';
import { Logo } from '../../shared/ui/Branding';
import { Button, Input, Spinner } from '../../shared/ui/TaaliPrimitives';

export const ForgotPasswordPage = ({ onNavigate }) => {
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
      await auth.forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Request failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[var(--taali-surface)] flex flex-col">
      <nav className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-surface)]">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <Logo onClick={() => onNavigate('landing')} />
        </div>
      </nav>
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          {sent ? (
            <div className="border-2 border-[var(--taali-border)] p-8 text-center bg-[var(--taali-surface)]">
              <CheckCircle size={48} className="mx-auto mb-4 text-[var(--taali-purple)]" />
              <h2 className="text-2xl font-bold mb-2">Check your email</h2>
              <p className="text-sm text-[var(--taali-muted)] mb-6">
                If an account exists for that email, we sent a link to reset your password.
              </p>
              <Button variant="primary" className="w-full" onClick={() => onNavigate('login')}>
                Back to Sign In
              </Button>
            </div>
          ) : (
            <div className="border-2 border-[var(--taali-border)] p-8 bg-[var(--taali-surface)]">
              <h2 className="text-3xl font-bold mb-2">Forgot password?</h2>
              <p className="text-sm text-[var(--taali-muted)] mb-6">Enter your email and we&apos;ll send a reset link.</p>
              {error && (
                <div className="border-2 border-[var(--taali-danger)] bg-[var(--taali-danger-soft)] p-3 mb-4 flex items-center gap-2">
                  <AlertTriangle size={18} className="text-[var(--taali-danger)] flex-shrink-0" />
                  <span className="text-sm text-[var(--taali-text)]">{error}</span>
                </div>
              )}
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="block font-mono text-sm mb-1">Email</label>
                  <Input
                    type="email"
                    placeholder="you@company.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </div>
                <Button type="submit" variant="primary" className="w-full" disabled={loading}>
                  {loading ? <><Spinner size={18} /> Sending...</> : 'Send reset link'}
                </Button>
              </form>
              <div className="mt-6 text-center">
                <button
                  type="button"
                  className="text-sm hover:underline text-[var(--taali-purple)]"
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
