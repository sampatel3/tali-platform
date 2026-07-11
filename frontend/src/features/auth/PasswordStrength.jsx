import React from 'react';

// Small advisory strength meter. The server enforces the real policy
// (length + blocklist + email similarity); this only guides the user as
// they type. NIST-style: length + variety, no forced complexity classes.
//
// This blocklist is a small mirror of the worst offenders — it does NOT need
// to match the backend list exactly. Advisory only.
const COMMON = new Set([
  '123456', '12345678', '123456789', '1234567890', '1234567', '12345',
  '111111', '000000', '121212', '654321', '123123', '112233',
  '1q2w3e4r', '1qaz2wsx', 'qazwsx', 'password', 'password1', 'password12',
  'password123', 'passw0rd', 'p@ssword', 'p@ssw0rd', 'letmein', 'letmein1',
  'welcome', 'welcome1', 'welcome123', 'qwerty', 'qwerty123', 'qwertyuiop',
  'asdfgh', 'asdfghjkl', 'zxcvbn', 'zxcvbnm', 'iloveyou', 'trustno1',
  'sunshine', 'princess', 'superman', 'batman', 'admin', 'admin123',
  'administrator', 'root', 'guest', 'user', 'test', 'test123', 'changeme',
  'default', 'secret', 'login', 'master', 'football', 'baseball', 'shadow',
  'monkey', 'dragon', 'mustang', 'abc123', 'abcd1234', 'hello', 'hello123',
  'whatever', 'freedom', 'starwars', 'money', 'flower', 'summer', 'winter',
  'apple', 'iphone', 'android', 'newpassword', 'mypassword', 'changeit',
]);

const HINTS = {
  0: 'Too weak — add length or avoid common words',
  1: 'Weak — a few more characters would help',
  2: 'Fair — getting there',
  3: 'Strong password',
};

// Score 0–3. Common passwords are always 0.
function scorePassword(password) {
  if (!password) return null;
  const pw = password.trim().toLowerCase();
  if (COMMON.has(pw)) return { score: 0, hint: 'Too common — choose something less predictable' };

  let score = 0;
  if (password.length >= 8) score += 1;
  if (password.length >= 12) score += 1;

  let variety = 0;
  if (/[a-z]/.test(password)) variety += 1;
  if (/[A-Z]/.test(password)) variety += 1;
  if (/[0-9]/.test(password)) variety += 1;
  if (/[^A-Za-z0-9]/.test(password)) variety += 1;
  if (variety >= 3) score += 1;

  score = Math.min(3, score);
  return { score, hint: HINTS[score] };
}

// Purple shades, light → saturated. Neutral for empty segments. No red/amber/green.
const SEGMENT_COLORS = ['var(--purple-lav)', 'var(--purple-2, var(--purple))', 'var(--purple)'];
const SEGMENTS = 3;

export const PasswordStrength = ({ password = '', email }) => {
  const result = scorePassword(password);
  if (!result) return null;

  const { score, hint } = result;
  // email-similarity nudge mirrors the backend rule (advisory).
  let finalHint = hint;
  if (email) {
    const local = String(email).split('@', 1)[0].trim().toLowerCase();
    const pwLower = password.toLowerCase();
    if (local.length >= 3 && (pwLower.includes(local) || local.includes(pwLower))) {
      finalHint = "Don't put your email in your password";
    }
  }

  // score 0 → 1 filled segment (still shows the user something), else score segments.
  const filled = Math.max(1, score);

  return (
    <div className="mc-auth-strength" aria-live="polite">
      <div className="mc-auth-strength-bar">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="mc-auth-strength-seg"
            style={{
              background: i < filled ? SEGMENT_COLORS[Math.min(score, SEGMENTS - 1)] : 'var(--line)',
            }}
          />
        ))}
      </div>
      <div className="mc-auth-strength-hint">{finalHint}</div>
    </div>
  );
};

export default PasswordStrength;
